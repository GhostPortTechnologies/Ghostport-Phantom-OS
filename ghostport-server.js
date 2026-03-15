/**
 * GhostPort — Command Deck API Server
 * Runs on the Pi, controls gp-mode, serves live status
 * Start with: sudo node ghostport-server.js
 */

const express = require("express");
const { exec } = require("child_process");
const path = require("path");
const crypto = require("crypto");
const https = require("https");
const fs = require("fs");

const http = require("http");

const app = express();
app.disable("x-powered-by");
const PORT = 4200;

// ── Pi-hole API integration ─────────────────────────────
const PIHOLE_FILE = "/etc/ghostport/pihole.json";
let piholeSid = null;

function readPiholeConfig() {
  try { return JSON.parse(fs.readFileSync(PIHOLE_FILE, "utf8")); }
  catch { return null; }
}

function piholeRequest(method, apiPath, body) {
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : null;
    const headers = { "Accept": "application/json" };
    if (data) {
      headers["Content-Type"] = "application/json";
      headers["Content-Length"] = Buffer.byteLength(data);
    }
    if (piholeSid) headers["sid"] = piholeSid;
    const req = http.request({ hostname: "localhost", port: 80, path: `/api${apiPath}`, method, headers, timeout: 10000 }, res => {
      let chunks = "";
      res.on("data", c => chunks += c);
      res.on("end", () => {
        try { resolve({ status: res.statusCode, data: JSON.parse(chunks) }); }
        catch { resolve({ status: res.statusCode, data: chunks }); }
      });
    });
    req.on("error", reject);
    req.on("timeout", () => { req.destroy(); reject(new Error("Pi-hole API timeout")); });
    if (data) req.write(data);
    req.end();
  });
}

async function piholeAuth() {
  const config = readPiholeConfig();
  if (!config || !config.password) {
    console.log("[Pi-hole] No credentials configured");
    return false;
  }
  try {
    const res = await piholeRequest("POST", "/auth", { password: config.password });
    if (res.data?.session?.valid) {
      piholeSid = res.data.session.sid;
      console.log("[Pi-hole] Authenticated");
      return true;
    }
    console.error("[Pi-hole] Auth failed:", res.data?.session?.message);
    return false;
  } catch (e) {
    console.error("[Pi-hole] Auth error:", e.message);
    return false;
  }
}

async function piholeApi(method, apiPath, body) {
  let res = await piholeRequest(method, apiPath, body);
  // Re-auth on 401
  if (res.status === 401) {
    if (await piholeAuth()) {
      res = await piholeRequest(method, apiPath, body);
    }
  }
  return res;
}

// Auth on startup
piholeAuth();

app.use(express.json());

// Security headers
app.use((req, res, next) => {
  res.setHeader("X-Content-Type-Options", "nosniff");
  res.setHeader("X-Frame-Options", "DENY");
  res.setHeader("Referrer-Policy", "no-referrer");
  res.setHeader("X-XSS-Protection", "0");
  res.setHeader("Permissions-Policy", "camera=(), microphone=(), geolocation=()");
  res.setHeader("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'");
  next();
});

// ── authentication ────────────────────────────────────────

const AUTH_FILE = "/etc/ghostport/auth.json";
const SESSION_TTL = 24 * 60 * 60 * 1000;
const LOCKOUT_MS = 60000;
const MAX_ATTEMPTS = 5;
const sessions = new Map();
const failedAttempts = new Map();

const PASSCODE_CHARS = "ABCDEFGHJKMNPQRSTUVWXYZ23456789";

function generatePasscode() {
  const parts = [];
  for (let p = 0; p < 3; p++) {
    let seg = "";
    for (let i = 0; i < 4; i++) seg += PASSCODE_CHARS[crypto.randomInt(PASSCODE_CHARS.length)];
    parts.push(seg);
  }
  return "GP-" + parts.join("-");
}

function hashPasscode(passcode, salt) {
  return crypto.scryptSync(passcode, salt, 64).toString("hex");
}

function readAuth() {
  try { return JSON.parse(fs.readFileSync(AUTH_FILE, "utf8")); }
  catch { return null; }
}

function writeAuth(data) {
  fs.writeFileSync(AUTH_FILE, JSON.stringify(data, null, 2));
  fs.chmodSync(AUTH_FILE, 0o600);
}

function getCookie(req, name) {
  const cookies = req.headers.cookie || "";
  const match = cookies.split(";").map(c => c.trim()).find(c => c.startsWith(name + "="));
  return match ? match.split("=")[1] : null;
}

function isLockedOut(ip) {
  const record = failedAttempts.get(ip);
  if (!record) return false;
  if (record.count >= MAX_ATTEMPTS && (Date.now() - record.lastAttempt) < LOCKOUT_MS) return true;
  if ((Date.now() - record.lastAttempt) >= LOCKOUT_MS) { failedAttempts.delete(ip); return false; }
  return false;
}

function recordFailedAttempt(ip) {
  const record = failedAttempts.get(ip) || { count: 0, lastAttempt: 0 };
  record.count++;
  record.lastAttempt = Date.now();
  failedAttempts.set(ip, record);
}

// Generate passcode on first boot
(function initAuth() {
  let auth = readAuth();
  if (auth && auth.hash && auth.salt) {
    console.log("[Auth] Passcode configured");
    return;
  }
  const passcode = generatePasscode();
  const salt = crypto.randomBytes(32).toString("hex");
  const hash = hashPasscode(passcode, salt);
  writeAuth({ hash, salt });
  console.log("");
  console.log("  ╔═══════════════════════════════════════════╗");
  console.log("  ║   DEVICE PASSCODE (save this!)            ║");
  console.log(`  ║   ${passcode}                      ║`);
  console.log("  ╚═══════════════════════════════════════════╝");
  console.log("");
  console.log("  Use this to log into the Command Deck.");
  console.log("  Reset via SSH: sudo gp-passcode reset");
  console.log("");
})();

// ── public auth routes (no session required) ──────────────

app.get("/login.html", (req, res) => res.sendFile(path.join(__dirname, "public", "login.html")));
app.get("/login", (req, res) => res.redirect("/login.html"));

app.post("/api/auth/login", (req, res) => {
  const ip = req.ip;
  if (isLockedOut(ip)) {
    return res.status(429).json({ ok: false, error: "Too many attempts. Try again in 60 seconds." });
  }

  const { passcode } = req.body;
  if (!passcode) return res.status(400).json({ ok: false, error: "Passcode required" });

  const auth = readAuth();
  if (!auth || !auth.hash) return res.status(500).json({ ok: false, error: "Auth not configured" });

  const hash = hashPasscode(passcode.toUpperCase().trim(), auth.salt);
  if (hash !== auth.hash) {
    recordFailedAttempt(ip);
    const record = failedAttempts.get(ip);
    const remaining = MAX_ATTEMPTS - record.count;
    console.log(`[Auth] Failed login from ${ip} (${remaining} attempts remaining)`);
    if (remaining <= 0) {
      return res.status(429).json({ ok: false, error: "Too many attempts. Locked out for 60 seconds." });
    }
    return res.status(401).json({ ok: false, error: `Invalid passcode. ${remaining} attempt${remaining !== 1 ? "s" : ""} remaining.` });
  }

  failedAttempts.delete(ip);
  const token = crypto.randomBytes(32).toString("hex");
  sessions.set(token, { created: Date.now(), ip });
  console.log(`[Auth] Successful login from ${ip}`);

  res.setHeader("Set-Cookie", `gp-session=${token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=86400`);
  res.json({ ok: true });
});

app.post("/api/auth/logout", (req, res) => {
  const token = getCookie(req, "gp-session");
  if (token) sessions.delete(token);
  res.setHeader("Set-Cookie", "gp-session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0");
  res.json({ ok: true });
});

app.get("/api/auth/check", (req, res) => {
  const token = getCookie(req, "gp-session");
  if (token && sessions.has(token)) {
    const session = sessions.get(token);
    if (Date.now() - session.created < SESSION_TTL) {
      return res.json({ ok: true, authenticated: true });
    }
    sessions.delete(token);
  }
  res.json({ ok: true, authenticated: false });
});

// ── session middleware (everything below requires auth) ────

app.use((req, res, next) => {
  const token = getCookie(req, "gp-session");
  if (token && sessions.has(token)) {
    const session = sessions.get(token);
    if (Date.now() - session.created < SESSION_TTL && session.ip === req.ip) {
      return next();
    }
    sessions.delete(token);
  }
  if (req.path.startsWith("/api/")) {
    return res.status(401).json({ ok: false, error: "Not authenticated" });
  }
  return res.redirect("/login.html");
});

app.use(express.static(path.join(__dirname, "public")));

// ── change passcode (protected) ───────────────────────────

app.post("/api/auth/change-passcode", (req, res) => {
  const { currentPasscode, newPasscode } = req.body;
  if (!currentPasscode) return res.status(400).json({ ok: false, error: "Current passcode required" });

  const auth = readAuth();
  if (!auth) return res.status(500).json({ ok: false, error: "Auth not configured" });

  const currentHash = hashPasscode(currentPasscode.toUpperCase().trim(), auth.salt);
  if (currentHash !== auth.hash) {
    return res.status(401).json({ ok: false, error: "Current passcode is incorrect" });
  }

  const passcode = newPasscode && newPasscode.trim().length >= 6
    ? newPasscode.trim().toUpperCase()
    : generatePasscode();
  const salt = crypto.randomBytes(32).toString("hex");
  const hash = hashPasscode(passcode, salt);
  writeAuth({ hash, salt });

  // Invalidate all other sessions
  const myToken = getCookie(req, "gp-session");
  for (const [tok] of sessions) {
    if (tok !== myToken) sessions.delete(tok);
  }

  console.log(`[Auth] Passcode changed from ${req.ip}`);
  res.json({ ok: true, passcode, generated: !newPasscode || newPasscode.trim().length < 6 });
});

// ── helpers ────────────────────────────────────────────────

function run(cmd, timeout = 15000) {
  return new Promise((resolve) => {
    exec(cmd, { timeout }, (err, stdout, stderr) => {
      resolve({ ok: !err, out: stdout.trim(), err: stderr.trim() });
    });
  });
}

function formatUptime(seconds) {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

// ── rollback state ────────────────────────────────────────

let rollbackTimer = null;
let rollbackTarget = null;
let rollbackDeadline = null;

function cancelRollback() {
  if (rollbackTimer) {
    clearTimeout(rollbackTimer);
    rollbackTimer = null;
    rollbackTarget = null;
    rollbackDeadline = null;
    // Also cancel the shell-level rollback
    run("sudo gp-mode confirm");
  }
}

function scheduleRollback(previousMode, timeoutSec = 60) {
  cancelRollback();
  rollbackTarget = previousMode;
  rollbackDeadline = Date.now() + timeoutSec * 1000;

  rollbackTimer = setTimeout(async () => {
    console.log(`[GhostPort] Rollback timer expired — reverting to ${previousMode}`);
    await run(`sudo gp-mode ${previousMode} --no-rollback`);
    rollbackTimer = null;
    rollbackTarget = null;
    rollbackDeadline = null;
  }, timeoutSec * 1000);
}

// ── routes ─────────────────────────────────────────────────

/**
 * GET /api/status
 * Returns live system status
 */
app.get("/api/status", async (req, res) => {
  try {
    const [gpStatus, wg, ts, ip, uptime, pihole] = await Promise.all([
      run("sudo gp-mode status"),
      run("ip link show wg0 2>/dev/null | grep -q 'state UNKNOWN\\|state UP' && echo up || echo down"),
      run("ip link show tailscale0 2>/dev/null | grep -q 'state UP\\|state UNKNOWN' && echo up || echo down"),
      run("curl -s --max-time 5 https://icanhazip.com || echo unknown"),
      run("awk '{print $1}' /proc/uptime"),
      piholeApi("GET", "/stats/summary").then(r => ({ out: String(r.data?.queries?.blocked || 0), ok: true })).catch(() => ({ out: "0", ok: false })),
    ]);

    // Read active mode from file written by gp-mode
    const modeFile = await run("cat /etc/ghostport/current-mode 2>/dev/null || echo isp");
    const activeMode = ["isp", "zerotrust", "doublehop", "zhop"].includes(modeFile.out.trim())
      ? modeFile.out.trim()
      : "isp";

    // Rollback info
    const rollbackInfo = {
      pending: rollbackTimer !== null,
      target: rollbackTarget,
      remainingSec: rollbackDeadline ? Math.max(0, Math.round((rollbackDeadline - Date.now()) / 1000)) : 0,
    };

    res.json({
      ok: true,
      activeMode,
      tunnels: {
        wg0: wg.out.trim() === "up" ? "up" : "down",
        tailscale: ts.out.trim() === "up" ? "up" : "down",
        pihole: "up",
      },
      ip: ip.out.trim() || "unknown",
      uptime: formatUptime(parseFloat(uptime.out) || 0),
      adsBlocked: parseInt(pihole.out.trim()) || 0,
      rollback: rollbackInfo,
      raw: gpStatus.out,
    });
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message });
  }
});

/**
 * POST /api/mode
 * Body: { mode: "isp" | "zerotrust" | "doublehop" | "zhop" }
 * Switches the active gp-mode with automatic rollback safety
 */
app.post("/api/mode", async (req, res) => {
  const { mode } = req.body;
  const valid = ["isp", "zerotrust", "doublehop", "zhop"];

  if (!valid.includes(mode)) {
    return res.status(400).json({ ok: false, error: `Invalid mode: ${mode}` });
  }

  // Get current mode before switching
  const prevFile = await run("cat /etc/ghostport/current-mode 2>/dev/null || echo isp");
  const previousMode = valid.includes(prevFile.out.trim()) ? prevFile.out.trim() : "isp";

  console.log(`[GhostPort] Switching from ${previousMode} to ${mode}`);
  const result = await run(`sudo gp-mode ${mode}`);

  if (!result.ok) {
    console.error(`[GhostPort] Mode switch failed: ${result.err}`);
    return res.status(500).json({ ok: false, error: result.err || "Mode switch failed" });
  }

  // Schedule rollback (ISP is always safe, no rollback needed)
  const needsRollback = mode !== "isp";
  if (needsRollback) {
    scheduleRollback(previousMode, 60);
  }

  // Remove QUIC block rules if toggle is off
  const arsenalAfter = readArsenal();
  if (arsenalAfter.quicBlock === false && mode !== "isp") {
    await run('sudo nft -a list chain inet filter forward 2>/dev/null | grep gp-quic-block | sed -n "s/.*handle \\([0-9]*\\)/\\1/p" | while read h; do sudo nft delete rule inet filter forward handle $h; done');
    console.log("[Arsenal] QUIC block disabled — removed rules after mode switch");
  }

  console.log(`[GhostPort] Mode switched: ${result.out}`);
  res.json({
    ok: true,
    mode,
    previousMode,
    message: result.out,
    rollback: {
      pending: needsRollback,
      target: needsRollback ? previousMode : null,
      remainingSec: needsRollback ? 60 : 0,
    },
  });
});

/**
 * POST /api/mode/confirm
 * Confirms the current mode is working — cancels rollback timer
 */
app.post("/api/mode/confirm", (req, res) => {
  const wasPending = rollbackTimer !== null;
  cancelRollback();
  console.log(`[GhostPort] Mode confirmed by user (was pending: ${wasPending})`);
  res.json({ ok: true, confirmed: true, wasPending });
});

/**
 * POST /api/mode/rollback
 * Manually triggers immediate rollback
 */
app.post("/api/mode/rollback", async (req, res) => {
  const target = rollbackTarget || "isp";
  cancelRollback();
  console.log(`[GhostPort] Manual rollback to ${target}`);
  const result = await run(`sudo gp-mode ${target} --no-rollback`);
  res.json({ ok: result.ok, mode: target, message: result.out, error: result.err || null });
});

/**
 * GET /api/pihole
 * Returns Pi-hole stats
 */
app.get("/api/pihole", async (req, res) => {
  try {
    const r = await piholeApi("GET", "/stats/summary");
    if (r.status !== 200) return res.json({ ok: false, error: "Pi-hole unreachable" });
    res.json({ ok: true, ...r.data });
  } catch (e) {
    res.json({ ok: false, error: "Pi-hole unreachable" });
  }
});

/**
 * POST /api/tailscale
 * Body: { action: "start" | "stop" }
 */
app.post("/api/tailscale", async (req, res) => {
  const { action } = req.body;
  if (!["start", "stop"].includes(action)) {
    return res.status(400).json({ ok: false, error: "Invalid action" });
  }
  const cmd = action === "start"
    ? "sudo systemctl enable --now tailscaled"
    : "sudo systemctl stop tailscaled";
  const result = await run(cmd);
  res.json({ ok: result.ok, action, error: result.err || null });
});

/**
 * GET /api/wg
 * Returns WireGuard peer stats
 */
app.get("/api/wg", async (req, res) => {
  const result = await run("sudo wg show all dump 2>/dev/null");
  if (!result.ok) return res.json({ ok: false, error: "WireGuard unavailable" });

  const lines = result.out.split("\n").filter(Boolean);
  const peers = lines.slice(1).map((line) => {
    const [pubkey, , endpoint, allowedIps, lastHandshake, rx, tx] = line.split("\t");
    return {
      pubkey: pubkey?.slice(0, 12) + "...",
      endpoint,
      allowedIps,
      lastHandshake: lastHandshake === "0" ? "never" : new Date(parseInt(lastHandshake) * 1000).toLocaleTimeString(),
      rx: `${(parseInt(rx || 0) / 1024).toFixed(1)} KiB`,
      tx: `${(parseInt(tx || 0) / 1024).toFixed(1)} KiB`,
    };
  });

  res.json({ ok: true, peers });
});

// ── hostapd ───────────────────────────────────────────────

app.post("/api/hostapd/restart", async (req, res) => {
  console.log("[GhostPort] Restarting hostapd (WiFi AP)");
  const result = await run("sudo systemctl restart hostapd");
  if (!result.ok) {
    return res.status(500).json({ ok: false, error: result.err || "hostapd restart failed" });
  }
  // Verify AP came back
  const check = await run("sleep 2 && iw dev wlan0 info 2>/dev/null | grep -q 'type AP' && echo up || echo down");
  const isUp = check.out.trim() === "up";
  console.log(`[GhostPort] hostapd restart: ${isUp ? "AP is up" : "AP failed to come up"}`);
  res.json({ ok: isUp, status: isUp ? "AP broadcasting" : "AP failed to start — check hostapd config" });
});

// ── repair tools ──────────────────────────────────────────

app.post("/api/repair/dns", async (req, res) => {
  console.log("[GhostPort] Restarting DNS stack");
  const r1 = await run("sudo systemctl restart dnsmasq");
  const r2 = await run("sudo systemctl restart pihole-FTL");
  const r3 = await run("sudo systemctl restart unbound");
  // Verify resolution works
  const check = await run("sleep 1 && dig +short +time=3 example.com @127.0.0.1");
  const ok = check.ok && check.out.length > 0;
  console.log(`[GhostPort] DNS restart: ${ok ? "resolving" : "still broken"}`);
  res.json({
    ok,
    status: ok ? "DNS stack restarted — resolving" : "DNS restarted but resolution failed",
    services: { dnsmasq: r1.ok, piholeFTL: r2.ok, unbound: r3.ok },
  });
});

app.post("/api/repair/wireguard", async (req, res) => {
  console.log("[GhostPort] Restarting WireGuard");
  const result = await run("sudo systemctl restart wg-quick@wg0");
  const check = await run("sleep 2 && ip link show wg0 2>/dev/null | grep -q 'state UNKNOWN\\|state UP' && echo up || echo down");
  const isUp = check.out.trim() === "up";
  console.log(`[GhostPort] WireGuard restart: ${isUp ? "tunnel up" : "tunnel down"}`);
  res.json({ ok: isUp, status: isUp ? "WireGuard tunnel is up" : "WireGuard failed to start — check wg0 config" });
});

app.post("/api/repair/firewall", async (req, res) => {
  const modeFile = await run("cat /etc/ghostport/current-mode 2>/dev/null || echo isp");
  const mode = modeFile.out.trim() || "isp";
  console.log(`[GhostPort] Reapplying firewall for mode: ${mode}`);
  const result = await run(`sudo gp-mode ${mode} --no-rollback`);
  const check = await run("sudo nft list ruleset | head -3");
  const ok = check.ok && check.out.length > 0;
  console.log(`[GhostPort] Firewall reapply: ${ok ? "rules loaded" : "empty ruleset"}`);
  res.json({ ok, mode, status: ok ? `Firewall reapplied for ${mode}` : "Firewall reapply failed" });
});

app.post("/api/repair/reboot", async (req, res) => {
  console.log("[GhostPort] Reboot requested from UI");
  res.json({ ok: true, status: "Rebooting in 3 seconds..." });
  setTimeout(() => run("sudo reboot"), 3000);
});

app.post("/api/repair/factory-reset", async (req, res) => {
  console.log("[GhostPort] Factory reset requested");
  try {
    // Switch to ISP safe mode
    await run("sudo gp-mode isp --no-rollback");
    // Remove auth (new passcode generated on next boot)
    await run("sudo rm -f /etc/ghostport/auth.json");
    // Reset arsenal config
    await run("sudo rm -f /etc/ghostport/arsenal.json");
    // Remove Pi-hole saved credentials
    await run("sudo rm -f /etc/ghostport/pihole.json");
    // Clear mode state to ISP
    await run("echo isp | sudo tee /etc/ghostport/current-mode");
    console.log("[GhostPort] Factory reset complete — rebooting");
    res.json({ ok: true, status: "Factory reset complete. Rebooting..." });
    setTimeout(() => run("sudo reboot"), 3000);
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message });
  }
});

// ── pihole stats by range ─────────────────────────────────

app.get("/api/pihole/stats", async (req, res) => {
  try {
    const now = Math.floor(Date.now() / 1000);
    const ranges = {
      today:     [now - 86400, now],
      thisMonth: [now - 2592000, now],
      thisYear:  [now - 31536000, now],
      allTime:   [1, now],
    };

    // Live session stats for "today" (most accurate)
    const liveRes = await piholeApi("GET", "/stats/summary");
    const liveBlocked = liveRes.data?.queries?.blocked || 0;
    const liveTotal = liveRes.data?.queries?.total || 0;

    // Database stats for historical ranges
    const dbResults = {};
    for (const [key, [from, until]] of Object.entries(ranges)) {
      if (key === "today") continue;
      try {
        const r = await piholeApi("GET", `/stats/database/summary?from=${from}&until=${until}`);
        dbResults[key] = { blocked: r.data?.sum_blocked || 0, total: r.data?.sum_queries || 0 };
      } catch { dbResults[key] = { blocked: 0, total: 0 }; }
    }

    res.json({
      ok: true,
      today:     { blocked: liveBlocked, total: liveTotal },
      thisMonth: dbResults.thisMonth,
      thisYear:  dbResults.thisYear,
      allTime:   dbResults.allTime,
    });
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message });
  }
});

// ── wireguard setup ──────────────────────────────────────

app.get("/api/wireguard/status", async (req, res) => {
  try {
    const hasConfig = await run("sudo test -s /etc/wireguard/wg0.conf && echo yes || echo no");
    const ifUp = await run("ip link show wg0 2>/dev/null | grep -q 'state UNKNOWN\\|state UP' && echo up || echo down");
    res.json({ ok: true, configured: hasConfig.out.trim() === "yes", status: ifUp.out.trim() });
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message });
  }
});

app.post("/api/wireguard/setup", async (req, res) => {
  const { config } = req.body;
  if (!config || !config.includes("[Interface]")) {
    return res.status(400).json({ ok: false, error: "Invalid WireGuard config — must contain [Interface] section" });
  }
  // Basic validation
  if (!config.includes("PrivateKey") || !config.includes("[Peer]")) {
    return res.status(400).json({ ok: false, error: "Config must contain PrivateKey and at least one [Peer] section" });
  }
  try {
    // Strip dangerous directives that execute arbitrary commands as root
    const dangerousDirectives = /^\s*(PostUp|PostDown|PreUp|PreDown|SaveConfig)\s*=.*$/gmi;
    const sanitizedConfig = config.replace(dangerousDirectives, "# [removed by GhostPort for security]");
    // Write config to temp file, then move with sudo
    const tmpFile = "/tmp/gp-wg0.conf";
    fs.writeFileSync(tmpFile, sanitizedConfig);
    await run(`sudo cp ${tmpFile} /etc/wireguard/wg0.conf`);
    await run("sudo chmod 600 /etc/wireguard/wg0.conf");
    fs.unlinkSync(tmpFile);
    // Restart WireGuard
    await run("sudo systemctl stop wg-quick@wg0 2>/dev/null");
    const start = await run("sudo systemctl start wg-quick@wg0");
    const check = await run("sleep 2 && ip link show wg0 2>/dev/null | grep -q 'state UNKNOWN\\|state UP' && echo up || echo down");
    const isUp = check.out.trim() === "up";
    console.log(`[WireGuard] Config saved and ${isUp ? "tunnel is up" : "tunnel failed to start"}`);
    res.json({ ok: true, status: isUp ? "up" : "down", message: isUp ? "WireGuard tunnel is up" : "Config saved but tunnel failed to start — check your config" });
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message });
  }
});

// ── restart tailscale ─────────────────────────────────────

app.post("/api/repair/tailscale", async (req, res) => {
  console.log("[GhostPort] Restarting Tailscale");
  await run("sudo systemctl restart tailscaled");
  const check = await run("sleep 2 && ip link show tailscale0 2>/dev/null | grep -q 'state UP\\|state UNKNOWN' && echo up || echo down");
  const isUp = check.out.trim() === "up";
  console.log(`[GhostPort] Tailscale restart: ${isUp ? "up" : "down"}`);
  res.json({ ok: isUp, status: isUp ? "Tailscale is up" : "Tailscale failed to start" });
});

// ── flush DNS cache ──────────────────────────────────────

app.post("/api/repair/flushdns", async (req, res) => {
  console.log("[GhostPort] Flushing DNS cache");
  try {
    const r = await piholeApi("DELETE", "/dns/cache");
    const ok = r.status >= 200 && r.status < 300;
    if (!ok) {
      // Fallback: restart pihole-FTL to clear cache
      await run("sudo systemctl restart pihole-FTL");
    }
    res.json({ ok: true, status: "DNS cache flushed" });
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message });
  }
});

// ── speed test ───────────────────────────────────────────

app.post("/api/tools/speedtest", async (req, res) => {
  try {
    // Check if speedtest-cli is available
    const which = await run("which speedtest-cli 2>/dev/null || which speedtest 2>/dev/null");
    if (!which.ok || !which.out.trim()) {
      return res.json({ ok: false, error: "speedtest-cli not installed — run: sudo apt install speedtest-cli" });
    }
    console.log("[Tools] Running speed test...");
    const result = await run("speedtest-cli --json --timeout 60 2>/dev/null || speedtest --format=json 2>/dev/null", 120000);
    if (!result.ok) return res.json({ ok: false, error: "Speed test failed" });
    const data = JSON.parse(result.out);
    res.json({
      ok: true,
      download: (data.download / 1e6).toFixed(1),
      upload: (data.upload / 1e6).toFixed(1),
      ping: data.ping?.toFixed(0) || data.server?.latency?.toFixed(0) || "?",
      server: data.server?.sponsor || data.server?.name || "Unknown",
    });
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message });
  }
});

// ── ping test ────────────────────────────────────────────

app.post("/api/tools/ping", async (req, res) => {
  try {
    const targets = [
      { name: "Gateway", cmd: "ip route | awk '/default/{print $3}'" },
    ];
    const gateway = await run(targets[0].cmd);
    const gatewayIp = gateway.out.trim();

    const [gw, dns, ext] = await Promise.all([
      run(`ping -c3 -W2 ${gatewayIp} 2>/dev/null`),
      run("ping -c3 -W2 127.0.0.1 2>/dev/null"),
      run("ping -c3 -W2 1.1.1.1 2>/dev/null"),
    ]);

    function parseLatency(output) {
      const match = output.match(/rtt min\/avg\/max.*= ([\d.]+)\/([\d.]+)\/([\d.]+)/);
      return match ? { min: match[1], avg: match[2], max: match[3] } : null;
    }

    res.json({
      ok: true,
      results: [
        { name: "Gateway", target: gatewayIp, reachable: gw.ok, latency: parseLatency(gw.out) },
        { name: "DNS (local)", target: "127.0.0.1", reachable: dns.ok, latency: parseLatency(dns.out) },
        { name: "Internet", target: "1.1.1.1", reachable: ext.ok, latency: parseLatency(ext.out) },
      ],
    });
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message });
  }
});

// ── IP leak test ─────────────────────────────────────────

app.post("/api/tools/ipleak", async (req, res) => {
  try {
    const modeFile = await run("cat /etc/ghostport/current-mode 2>/dev/null || echo isp");
    const mode = modeFile.out.trim();
    const vpnModes = ["doublehop", "zhop"];

    const publicIp = await run("curl -s --max-time 5 https://icanhazip.com");
    const wgIp = await run("sudo wg show wg0 endpoints 2>/dev/null | awk '{print $2}' | cut -d: -f1");

    const ip = publicIp.out.trim();
    const wgEndpoint = wgIp.out.trim();
    const isVpn = vpnModes.includes(mode);
    // If in VPN mode, public IP should NOT match WireGuard endpoint
    const leaked = isVpn && ip === wgEndpoint;

    res.json({
      ok: true,
      publicIp: ip || "unknown",
      mode,
      vpnMode: isVpn,
      wgEndpoint: wgEndpoint || "none",
      leaked,
      status: !isVpn ? "Not in VPN mode" : leaked ? "IP LEAK DETECTED" : "No leak — IP is masked",
    });
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message });
  }
});

// ── recent blocked domains ───────────────────────────────

app.get("/api/tools/blocked", async (req, res) => {
  try {
    const r = await piholeApi("GET", "/queries?blocked=true&length=25");
    if (r.status !== 200) return res.json({ ok: false, error: "Pi-hole API error" });
    const queries = (r.data?.queries || []).map(q => ({
      domain: q.domain,
      client: q.client?.name || q.client?.ip || "unknown",
      time: q.time,
      type: q.type,
    }));
    res.json({ ok: true, queries });
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message });
  }
});

// ── bandwidth monitor ────────────────────────────────────

app.get("/api/tools/bandwidth", async (req, res) => {
  try {
    const interfaces = ["eth0", "wlan0", "wg0", "tailscale0"];
    const stats = {};
    for (const iface of interfaces) {
      const rx = await run(`cat /sys/class/net/${iface}/statistics/rx_bytes 2>/dev/null`);
      const tx = await run(`cat /sys/class/net/${iface}/statistics/tx_bytes 2>/dev/null`);
      if (rx.ok && tx.ok) {
        stats[iface] = {
          rx: parseInt(rx.out.trim()) || 0,
          tx: parseInt(tx.out.trim()) || 0,
        };
      }
    }
    res.json({ ok: true, interfaces: stats });
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message });
  }
});

// ── system update ────────────────────────────────────────

app.post("/api/tools/update", async (req, res) => {
  try {
    console.log("[Tools] Running system update...");
    const update = await run("sudo apt-get update -qq 2>&1 | tail -5", 120000);
    const upgrade = await run("sudo DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -qq 2>&1 | tail -10", 300000);
    const ok = upgrade.ok;
    console.log(`[Tools] System update: ${ok ? "complete" : "failed"}`);
    res.json({
      ok,
      status: ok ? "System updated" : "Update failed",
      output: upgrade.out.trim().split("\n").slice(-5).join("\n"),
    });
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message });
  }
});

// ── backup config ────────────────────────────────────────

app.get("/api/tools/backup", async (req, res) => {
  try {
    const backup = {};
    const files = {
      arsenal: "/etc/ghostport/arsenal.json",
      currentMode: "/etc/ghostport/current-mode",
    };
    for (const [key, filePath] of Object.entries(files)) {
      try { backup[key] = fs.readFileSync(filePath, "utf8").trim(); }
      catch { backup[key] = null; }
    }
    // Include schedules from arsenal
    try { backup.arsenal = JSON.parse(backup.arsenal); } catch { /* keep as string */ }
    backup.exportDate = new Date().toISOString();
    backup.version = "1.1";

    res.setHeader("Content-Type", "application/json");
    res.setHeader("Content-Disposition", "attachment; filename=ghostport-backup.json");
    res.json(backup);
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message });
  }
});

app.post("/api/tools/restore", async (req, res) => {
  try {
    const backup = req.body;
    if (!backup || !backup.version) {
      return res.status(400).json({ ok: false, error: "Invalid backup file" });
    }
    // Restore arsenal config
    if (backup.arsenal) {
      const data = typeof backup.arsenal === "string" ? backup.arsenal : JSON.stringify(backup.arsenal, null, 2);
      fs.writeFileSync("/etc/ghostport/arsenal.json", data);
    }
    // Restore mode
    if (backup.currentMode) {
      const mode = backup.currentMode.trim();
      if (["isp", "zerotrust", "doublehop", "zhop"].includes(mode)) {
        await run(`sudo gp-mode ${mode} --no-rollback`);
      }
    }
    console.log("[Tools] Config restored from backup");
    res.json({ ok: true, status: "Config restored" });
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message });
  }
});

// ── diagnostics ───────────────────────────────────────────

app.get("/api/diagnostics", async (req, res) => {
  try {
    const modeFile = await run("cat /etc/ghostport/current-mode 2>/dev/null || echo isp");
    const mode = modeFile.out.trim() || "isp";
    const wgModes = ["doublehop", "zhop"];

    const checks = await Promise.all([
      run("ping -c1 -W3 1.1.1.1").then(r => ({
        name: "Internet", ok: r.ok, detail: r.ok ? "1.1.1.1 reachable" : "No response",
        fix: r.ok ? null : "Check eth0 cable and upstream router", warn: false,
      })),
      run("dig +short +time=3 example.com @127.0.0.1").then(r => {
        const good = r.ok && r.out.length > 0;
        return { name: "DNS", ok: good, detail: good ? r.out.split("\n")[0] : "No resolution",
          fix: good ? null : "Check dnsmasq and Pi-hole services", warn: false };
      }),
      run("ip route | awk '/default/{print $3}'").then(async gwR => {
        if (!gwR.out) return { name: "Gateway", ok: false, detail: "No default route", fix: "Check network config", warn: false };
        const p = await run(`ping -c1 -W3 ${gwR.out}`);
        return { name: "Gateway", ok: p.ok, detail: p.ok ? `${gwR.out} reachable` : `${gwR.out} unreachable`,
          fix: p.ok ? null : "Check eth0 connection to router", warn: false };
      }),
      run("systemctl is-active hostapd").then(r => ({
        name: "hostapd", ok: r.out === "active", detail: r.out,
        fix: r.out === "active" ? null : "WiFi AP is down — run: sudo systemctl restart hostapd", warn: false,
      })),
      run("systemctl is-active pihole-FTL").then(r => ({
        name: "Pi-hole (DNS+DHCP)", ok: r.out === "active", detail: r.out,
        fix: r.out === "active" ? null : "Pi-hole is down — run: sudo systemctl restart pihole-FTL", warn: false,
      })),
      run("systemctl is-active wg-quick@wg0").then(r => {
        const up = r.out === "active";
        const critical = wgModes.includes(mode);
        return { name: "WireGuard", ok: up || !critical, detail: r.out,
          fix: !up && critical ? "WireGuard required for this mode — run: sudo systemctl start wg-quick@wg0" : (!up ? "WireGuard is down (not required in current mode)" : null),
          warn: !up && !critical };
      }),
      run("sudo nft list ruleset | head -5").then(r => ({
        name: "Firewall", ok: r.ok && r.out.length > 0, detail: r.ok && r.out.length > 0 ? "nftables loaded" : "Empty ruleset",
        fix: r.ok && r.out.length > 0 ? null : "Firewall not loaded — run: sudo gp-mode " + mode, warn: false,
      })),
      run("df --output=pcent / | tail -1").then(r => {
        const pct = parseInt(r.out) || 0;
        return { name: "Disk", ok: pct < 90, detail: `${pct}% used`,
          fix: pct >= 90 ? "Disk nearly full — free space on root partition" : null, warn: pct >= 80 && pct < 90 };
      }),
    ]);

    const passed = checks.filter(c => c.ok).length;
    res.json({ ok: true, checks, summary: { passed, total: checks.length, allGood: passed === checks.length } });
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message });
  }
});

// ── trouble tickets ───────────────────────────────────────

app.post("/api/ticket", async (req, res) => {
  const { description, contact } = req.body || {};
  if (!description || description.trim().length < 10) {
    return res.status(400).json({ ok: false, error: "Description must be at least 10 characters" });
  }

  let config;
  try {
    config = JSON.parse(fs.readFileSync("/etc/ghostport/support.json", "utf8"));
  } catch {
    return res.status(500).json({ ok: false, error: "Support config not found" });
  }

  if (!config.webhookUrl) {
    return res.status(400).json({ ok: false, error: "Webhook not configured — ask your admin to set webhookUrl in /etc/ghostport/support.json" });
  }

  // Gather system snapshot
  const [modeSnap, wgStatus, tsStatus, ipResult, uptimeResult] = await Promise.all([
    run("cat /etc/ghostport/current-mode 2>/dev/null || echo isp"),
    run("systemctl is-active wg-quick@wg0"),
    run("systemctl is-active tailscaled"),
    run("curl -s --max-time 5 https://icanhazip.com || echo unknown"),
    run("uptime -p"),
  ]);

  const snapshot = {
    mode: modeSnap.out.trim(),
    wireguard: wgStatus.out.trim(),
    tailscale: tsStatus.out.trim(),
    publicIp: ipResult.out.trim(),
    uptime: uptimeResult.out.trim(),
  };

  const deviceName = config.deviceName || "GhostPort";
  const timestamp = new Date().toISOString();

  // Build webhook payload
  let payload;
  if (config.webhookType === "slack") {
    payload = JSON.stringify({
      blocks: [
        { type: "header", text: { type: "plain_text", text: `Trouble Ticket — ${deviceName}` } },
        { type: "section", text: { type: "mrkdwn", text: `*Description:*\n${description.trim()}` } },
        contact ? { type: "section", text: { type: "mrkdwn", text: `*Contact:* ${contact}` } } : null,
        { type: "section", fields: [
          { type: "mrkdwn", text: `*Mode:* ${snapshot.mode}` },
          { type: "mrkdwn", text: `*WireGuard:* ${snapshot.wireguard}` },
          { type: "mrkdwn", text: `*Tailscale:* ${snapshot.tailscale}` },
          { type: "mrkdwn", text: `*Public IP:* ${snapshot.publicIp}` },
          { type: "mrkdwn", text: `*Uptime:* ${snapshot.uptime}` },
        ]},
        { type: "context", elements: [{ type: "mrkdwn", text: `${timestamp}` }] },
      ].filter(Boolean),
    });
  } else {
    const discordBody = {
      thread_name: `${deviceName} — ${description.trim().slice(0, 80)}`,
      embeds: [{
        title: `Trouble Ticket — ${deviceName}`,
        description: description.trim(),
        color: 0x39ff8f,
        fields: [
          contact ? { name: "Contact", value: contact, inline: true } : null,
          { name: "Mode", value: snapshot.mode, inline: true },
          { name: "WireGuard", value: snapshot.wireguard, inline: true },
          { name: "Tailscale", value: snapshot.tailscale, inline: true },
          { name: "Public IP", value: snapshot.publicIp, inline: true },
          { name: "Uptime", value: snapshot.uptime, inline: true },
        ].filter(Boolean),
        footer: { text: timestamp },
      }],
    };
    payload = JSON.stringify(discordBody);
  }

  // Send via built-in https
  try {
    const url = new URL(config.webhookUrl);
    await new Promise((resolve, reject) => {
      const wreq = https.request({
        hostname: url.hostname,
        port: url.port || 443,
        path: url.pathname + url.search,
        method: "POST",
        headers: { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(payload) },
      }, (resp) => {
        let body = "";
        resp.on("data", d => body += d);
        resp.on("end", () => resp.statusCode < 300 ? resolve(body) : reject(new Error(`Webhook returned ${resp.statusCode}: ${body}`)));
      });
      wreq.on("error", reject);
      wreq.write(payload);
      wreq.end();
    });
    res.json({ ok: true, message: "Ticket sent successfully" });
  } catch (e) {
    res.status(500).json({ ok: false, error: "Failed to send ticket: " + e.message });
  }
});

// ── arsenal ────────────────────────────────────────────────

const ARSENAL_FILE = "/etc/ghostport/arsenal.json";

function readArsenal() {
  try { return JSON.parse(fs.readFileSync(ARSENAL_FILE, "utf8")); }
  catch { return { killSwitch: false, encryptedDns: false, macRandomization: false, blocklistFreq: "weekly", schedules: [] }; }
}

function writeArsenal(data) {
  fs.writeFileSync(ARSENAL_FILE, JSON.stringify(data, null, 2));
}

// Kill switch — blocks all forwarded traffic if wg0 drops in VPN modes
let killSwitchInterval = null;
let killSwitchTripped = false;

// DNS leak monitor — background check every 30s in VPN modes
let dnsLeakInterval = null;
let dnsLeakDetected = false;
let cachedIspIp = null;

function startKillSwitch() {
  if (killSwitchInterval) return;
  console.log("[Arsenal] Kill switch enabled — monitoring wg0");
  killSwitchInterval = setInterval(async () => {
    const modeFile = await run("cat /etc/ghostport/current-mode 2>/dev/null || echo isp");
    const mode = modeFile.out.trim();
    if (mode !== "doublehop" && mode !== "zhop") return;

    const wg = await run("ip link show wg0 2>/dev/null | grep -q 'state UNKNOWN\\|state UP' && echo up || echo down");

    if (wg.out.trim() === "down" && !killSwitchTripped) {
      killSwitchTripped = true;
      console.log("[Arsenal] KILL SWITCH TRIPPED — wg0 down, blocking all forwarded traffic");
      // Drop all forwarded traffic so nothing leaks through eth0
      await run("sudo nft add rule inet filter forward counter drop comment \"gp-killswitch\"");
      // Also drop the Pi's own outbound (except management)
      await run("sudo nft add rule inet filter output oifname eth0 tcp dport != 22 udp dport != 41641 counter drop comment \"gp-killswitch\"");
    } else if (wg.out.trim() === "up" && killSwitchTripped) {
      killSwitchTripped = false;
      console.log("[Arsenal] wg0 recovered — restoring traffic");
      // Remove kill switch rules and reapply mode profile
      await run(`sudo gp-mode ${mode} --no-rollback`);
    }
  }, 5000);
}

function stopKillSwitch() {
  if (killSwitchInterval) {
    clearInterval(killSwitchInterval);
    killSwitchInterval = null;
  }
  if (killSwitchTripped) {
    killSwitchTripped = false;
    console.log("[Arsenal] Kill switch disabled — restoring traffic");
    // Reapply current mode to clear kill switch rules
    run("cat /etc/ghostport/current-mode 2>/dev/null || echo isp").then(async (modeFile) => {
      const mode = modeFile.out.trim();
      await run(`sudo gp-mode ${mode} --no-rollback`);
    });
  } else {
    console.log("[Arsenal] Kill switch disabled");
  }
}

// DNS leak monitor — runs every 30s, checks if DNS resolver matches ISP
async function checkDnsLeak() {
  try {
    const modeFile = await run("cat /etc/ghostport/current-mode 2>/dev/null || echo isp");
    const mode = modeFile.out.trim();
    // Only monitor in VPN modes and zerotrust
    if (mode !== "doublehop" && mode !== "zhop" && mode !== "zerotrust") {
      if (dnsLeakDetected) {
        dnsLeakDetected = false;
        console.log("[DNS Monitor] Not in protected mode — clearing leak flag");
      }
      return;
    }

    const cfResult = await run("dig +short TXT o-o.myaddr.l.google.com @127.0.0.1 +time=5 +tries=1");
    const dnsResolverIp = cfResult.out.replace(/"/g, "").trim();

    if (!dnsResolverIp) return; // DNS not responding, skip this cycle

    // Cache ISP IP so we don't curl every 30s
    if (!cachedIspIp) {
      const ispResult = await run("curl -4 -s --max-time 5 --interface eth0 ifconfig.me");
      cachedIspIp = ispResult.out.trim();
    }

    if (cachedIspIp && dnsResolverIp === cachedIspIp) {
      if (!dnsLeakDetected) {
        dnsLeakDetected = true;
        console.log(`[DNS Monitor] LEAK DETECTED — resolver ${dnsResolverIp} matches ISP ${cachedIspIp}`);

        // Auto-trip kill switch if enabled and set to auto
        const arsenal = readArsenal();
        if (arsenal.killSwitch && arsenal.killSwitchAuto && !killSwitchTripped) {
          killSwitchTripped = true;
          console.log("[DNS Monitor] Auto-tripping kill switch due to DNS leak");
          await run("sudo nft add rule inet filter forward counter drop comment \"gp-killswitch\"");
          await run("sudo nft add rule inet filter output oifname eth0 tcp dport != 22 udp dport != 41641 counter drop comment \"gp-killswitch\"");
        }
      }
    } else if (dnsLeakDetected) {
      dnsLeakDetected = false;
      console.log("[DNS Monitor] Leak resolved — DNS no longer matches ISP");
    }
  } catch (e) {
    // Silent — don't crash the monitor
  }
}

function startDnsLeakMonitor() {
  if (dnsLeakInterval) return;
  console.log("[DNS Monitor] Started — checking every 30s");
  cachedIspIp = null; // refresh ISP IP on start
  dnsLeakInterval = setInterval(checkDnsLeak, 30000);
  checkDnsLeak(); // run immediately
}

function stopDnsLeakMonitor() {
  if (dnsLeakInterval) {
    clearInterval(dnsLeakInterval);
    dnsLeakInterval = null;
    dnsLeakDetected = false;
    console.log("[DNS Monitor] Stopped");
  }
}

// Restore arsenal state on restart
(async () => {
  const arsenal = readArsenal();
  if (arsenal.killSwitch) startKillSwitch();
  // Remove QUIC block rules if toggle is off
  if (arsenal.quicBlock === false) {
    await run('sudo nft -a list chain inet filter forward 2>/dev/null | grep gp-quic-block | sed -n "s/.*handle \\([0-9]*\\)/\\1/p" | while read h; do sudo nft delete rule inet filter forward handle $h; done');
    console.log("[Arsenal] QUIC block disabled — removed rules on startup");
  }
  // Always run DNS leak monitor in protected modes
  startDnsLeakMonitor();
})();

/**
 * POST /api/pihole/setup — { password } — save Pi-hole credentials and test connection
 */
app.post("/api/pihole/setup", async (req, res) => {
  const { password } = req.body;
  if (!password) return res.status(400).json({ ok: false, error: "Password required" });
  try {
    // Test the password before saving
    const testRes = await piholeRequest("POST", "/auth", { password });
    if (!testRes.data?.session?.valid) {
      return res.json({ ok: false, error: "Invalid Pi-hole password" });
    }
    piholeSid = testRes.data.session.sid;
    const dir = path.dirname(PIHOLE_FILE);
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(PIHOLE_FILE, JSON.stringify({ password }, null, 2));
    fs.chmodSync(PIHOLE_FILE, 0o600);
    console.log("[Pi-hole] Credentials saved and authenticated");
    res.json({ ok: true, connected: true });
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message });
  }
});

/**
 * POST /api/pihole/reset-password — { newPassword } — change Pi-hole password and update saved credentials
 */
app.post("/api/pihole/reset-password", async (req, res) => {
  const { newPassword } = req.body;
  if (!newPassword) return res.status(400).json({ ok: false, error: "New password required" });
  try {
    // Sanitize: only allow alphanumeric + basic punctuation, no shell metacharacters
    const sanitizedPw = newPassword.replace(/[^a-zA-Z0-9!@#$%^&*()_+\-=]/g, "");
    if (sanitizedPw !== newPassword) {
      return res.status(400).json({ ok: false, error: "Password contains invalid characters" });
    }
    const result = await run("sudo pihole setpassword '" + sanitizedPw + "'");
    if (!result.ok) return res.status(500).json({ ok: false, error: "Failed to set Pi-hole password" });
    // Save new credentials and re-authenticate
    fs.writeFileSync(PIHOLE_FILE, JSON.stringify({ password: newPassword }, null, 2));
    fs.chmodSync(PIHOLE_FILE, 0o600);
    const authed = await piholeAuth();
    console.log("[Pi-hole] Password reset and re-authenticated");
    res.json({ ok: true, connected: authed });
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message });
  }
});

/**
 * GET /api/pihole/status — check if Pi-hole API is connected
 */
app.get("/api/pihole/status", async (req, res) => {
  const config = readPiholeConfig();
  if (!config) return res.json({ ok: true, configured: false, connected: false });
  // Test current session
  try {
    const r = await piholeApi("GET", "/stats/summary");
    res.json({ ok: true, configured: true, connected: r.status === 200 });
  } catch {
    res.json({ ok: true, configured: true, connected: false });
  }
});

/**
 * GET /api/arsenal/status
 */
app.get("/api/arsenal/status", async (req, res) => {
  try {
    const arsenal = readArsenal();
    const [wg, dnsMode, leases, macService] = await Promise.all([
      run("ip link show wg0 2>/dev/null | grep -q 'state UNKNOWN\\|state UP' && echo up || echo down"),
      run("sudo gp-dns-switch status"),
      run("cat /var/lib/misc/dnsmasq.leases 2>/dev/null"),
      run("systemctl is-enabled gp-mac-random.service 2>/dev/null"),
    ]);

    res.json({
      ok: true,
      killSwitch: arsenal.killSwitch,
      killSwitchTripped,
      killSwitchAuto: arsenal.killSwitchAuto !== false, // default true
      dnsLeakDetected,
      encryptedDns: dnsMode.out.trim() === "on",
      macRandomization: macService.out.trim() === "enabled",
      quicBlock: arsenal.quicBlock !== false, // default true
      piholeConnected: piholeSid !== null,
      blocklistFreq: arsenal.blocklistFreq,
      schedules: arsenal.schedules || [],
      wg0: wg.out.trim(),
      clientCount: leases.out ? leases.out.split("\n").filter(Boolean).length : 0,
    });
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message });
  }
});

/**
 * GET /api/arsenal/clients
 */
app.get("/api/arsenal/clients", async (req, res) => {
  try {
    const leases = await run("cat /var/lib/misc/dnsmasq.leases 2>/dev/null");
    const clients = leases.out.split("\n").filter(Boolean).map(line => {
      const parts = line.split(/\s+/);
      return {
        ip: parts[2],
        mac: parts[1],
        hostname: (!parts[3] || parts[3] === "*") ? "Unknown" : parts[3],
        expiry: parts[0] === "0" ? "static" : new Date(parseInt(parts[0]) * 1000).toLocaleTimeString(),
      };
    });
    res.json({ ok: true, clients });
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message });
  }
});

/**
 * POST /api/arsenal/dnstest
 */
app.post("/api/arsenal/dnstest", async (req, res) => {
  try {
    const [cfResult, publicIpResult, dnsMode, modeFile] = await Promise.all([
      run("dig +short TXT o-o.myaddr.l.google.com @127.0.0.1"),
      run("curl -4 -s --max-time 5 ifconfig.me"),
      run("sudo gp-dns-switch status"),
      run("cat /etc/ghostport/current-mode 2>/dev/null || echo isp"),
    ]);

    const dnsResolverIp = cfResult.out.replace(/"/g, "").trim();
    const publicIp = publicIpResult.out.trim();
    const encrypted = dnsMode.out.trim() === "on";
    const mode = modeFile.out.trim();
    const isVpn = ["doublehop", "zhop"].includes(mode);

    // Get ISP IP (always via eth0, never through tunnel)
    const ispResult = await run("curl -4 -s --max-time 5 --interface eth0 ifconfig.me");
    const ispIp = ispResult.out.trim();

    let passed = true;
    let reason = "DNS resolving through expected path";

    if (!dnsResolverIp) {
      passed = false;
      reason = "Could not determine DNS resolver — check Pi-hole/cloudflared";
    } else if (dnsResolverIp === ispIp || dnsResolverIp === publicIp && isVpn) {
      // DNS resolver is your ISP — that's a leak
      passed = false;
      reason = "DNS leaking through ISP — resolver matches your public IP";
    } else if (isVpn) {
      passed = true;
      reason = "DNS exits through encrypted tunnel, not your ISP";
    } else if (encrypted) {
      passed = true;
      reason = "DNS encrypted via cloudflared (DoH)";
    } else {
      passed = false;
      reason = "DNS is unencrypted — enable encrypted DNS for protection";
    }

    res.json({
      ok: true, passed, reason,
      localResolver: dnsResolverIp || "No response",
      directResolver: `Public: ${publicIp} | ISP: ${ispIp}`,
      encrypted, mode,
    });
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message });
  }
});

/**
 * POST /api/arsenal/blocklist — { freq: "daily"|"weekly" }
 */
app.post("/api/arsenal/blocklist", async (req, res) => {
  const { freq } = req.body;
  if (!["daily", "weekly"].includes(freq)) {
    return res.status(400).json({ ok: false, error: "Invalid frequency" });
  }
  try {
    const cronFile = "/etc/cron.d/pihole";
    if (freq === "daily") {
      await run(`sudo sed -i '/pihole updateGravity\\|pihole -g/s/^[0-9].*\\(root.*\\)/30 3 * * * \\1/' ${cronFile}`);
    } else {
      await run(`sudo sed -i '/pihole updateGravity\\|pihole -g/s/^[0-9].*\\(root.*\\)/30 3 * * 0 \\1/' ${cronFile}`);
    }
    const arsenal = readArsenal();
    arsenal.blocklistFreq = freq;
    writeArsenal(arsenal);
    res.json({ ok: true, freq });
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message });
  }
});

/**
 * POST /api/arsenal/blocklist/update — trigger gravity update now
 */
app.post("/api/arsenal/blocklist/update", async (req, res) => {
  try {
    console.log("[Arsenal] Triggering Pi-hole gravity update");
    const r = await piholeApi("POST", "/action/gravity");
    res.json({ ok: r.status === 200 || r.status === 204, output: r.data });
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message });
  }
});

/**
 * POST /api/arsenal/blocklist/domain — { domain, action: "deny"|"allow"|"remove-deny"|"remove-allow" }
 */
app.post("/api/arsenal/blocklist/domain", async (req, res) => {
  const { domain, action } = req.body;
  const validActions = ["deny", "allow", "remove-deny", "remove-allow"];
  if (!domain || !validActions.includes(action)) {
    return res.status(400).json({ ok: false, error: "Invalid domain or action" });
  }

  // Sanitize domain: only allow valid domain characters
  const sanitized = domain.replace(/^https?:\/\//, "").replace(/\/.*$/, "").toLowerCase();
  if (!/^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$/.test(sanitized)) {
    return res.status(400).json({ ok: false, error: "Invalid domain format" });
  }

  try {
    let r;
    switch (action) {
      case "deny":
        r = await piholeApi("POST", "/domains/deny/exact", { domain: sanitized });
        break;
      case "allow":
        r = await piholeApi("POST", "/domains/allow/exact", { domain: sanitized });
        break;
      case "remove-deny":
        r = await piholeApi("DELETE", `/domains/deny/exact/${encodeURIComponent(sanitized)}`);
        break;
      case "remove-allow":
        r = await piholeApi("DELETE", `/domains/allow/exact/${encodeURIComponent(sanitized)}`);
        break;
    }
    const ok = r.status >= 200 && r.status < 300;
    console.log(`[Arsenal] Domain ${action}: ${sanitized} (${ok ? "ok" : "failed"})`);
    res.json({ ok, domain: sanitized, action, error: ok ? undefined : r.data?.error?.message });
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message });
  }
});

/**
 * POST /api/arsenal/encrypteddns — { enabled: true|false }
 */
app.post("/api/arsenal/encrypteddns", async (req, res) => {
  const { enabled } = req.body;
  try {
    const action = enabled ? "on" : "off";
    const result = await run(`sudo gp-dns-switch ${action}`);
    if (result.out.trim() === "ok") {
      const arsenal = readArsenal();
      arsenal.encryptedDns = enabled;
      writeArsenal(arsenal);
      res.json({ ok: true, encryptedDns: enabled });
    } else {
      res.json({ ok: false, error: "DNS switch failed — rolled back automatically" });
    }
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message });
  }
});

/**
 * POST /api/arsenal/killswitch — { enabled: true|false }
 */
app.post("/api/arsenal/killswitch", async (req, res) => {
  const { enabled } = req.body;
  try {
    if (enabled) {
      startKillSwitch();
    } else {
      stopKillSwitch();
    }
    const arsenal = readArsenal();
    arsenal.killSwitch = enabled;
    writeArsenal(arsenal);
    res.json({ ok: true, killSwitch: enabled });
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message });
  }
});

/**
 * POST /api/arsenal/killswitch/auto — { enabled: true|false }
 * Toggles whether kill switch auto-trips on DNS leak detection
 */
app.post("/api/arsenal/killswitch/auto", async (req, res) => {
  const { enabled } = req.body;
  try {
    const arsenal = readArsenal();
    arsenal.killSwitchAuto = enabled;
    writeArsenal(arsenal);
    console.log(`[Arsenal] Kill switch auto-trip ${enabled ? "enabled" : "disabled"}`);
    res.json({ ok: true, killSwitchAuto: enabled });
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message });
  }
});

/**
 * POST /api/arsenal/macrandom — { enabled: true|false }
 */
app.post("/api/arsenal/macrandom", async (req, res) => {
  const { enabled } = req.body;
  try {
    const serviceFile = "/etc/systemd/system/gp-mac-random.service";
    if (enabled) {
      const unit = [
        "[Unit]",
        "Description=GhostPort MAC Randomization",
        "Before=hostapd.service",
        "After=sys-subsystem-net-devices-wlan0.device",
        "",
        "[Service]",
        "Type=oneshot",
        'ExecStart=/bin/bash -c \'MAC="02:$(od -An -N5 -tx1 /dev/urandom | sed "s/ /:/g" | cut -c2-)"; ip link set wlan0 down; ip link set wlan0 address $MAC; ip link set wlan0 up\'',
        "RemainAfterExit=yes",
        "",
        "[Install]",
        "WantedBy=multi-user.target",
      ].join("\n");
      fs.writeFileSync("/tmp/gp-mac-random.service", unit);
      await run(`sudo cp /tmp/gp-mac-random.service ${serviceFile}`);
      await run("sudo systemctl daemon-reload && sudo systemctl enable gp-mac-random.service");
      const arsenal = readArsenal();
      arsenal.macRandomization = true;
      writeArsenal(arsenal);
      res.json({ ok: true, macRandomization: true, note: "MAC will change on next reboot. AP will briefly disconnect." });
    } else {
      await run("sudo systemctl disable gp-mac-random.service 2>/dev/null");
      await run(`sudo rm -f ${serviceFile}`);
      await run("sudo systemctl daemon-reload");
      const arsenal = readArsenal();
      arsenal.macRandomization = false;
      writeArsenal(arsenal);
      res.json({ ok: true, macRandomization: false });
    }
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message });
  }
});

/**
 * POST /api/arsenal/quicblock — { enabled: true|false }
 * Toggles QUIC (UDP 443) blocking in privacy modes
 */
app.post("/api/arsenal/quicblock", async (req, res) => {
  const { enabled } = req.body;
  try {
    const arsenal = readArsenal();
    arsenal.quicBlock = enabled;
    writeArsenal(arsenal);

    const modeFile = await run("cat /etc/ghostport/current-mode 2>/dev/null || echo isp");
    const mode = modeFile.out.trim();

    if (mode !== "isp") {
      if (enabled) {
        // Re-apply mode to restore QUIC block rules from the nft profile
        await run(`sudo gp-mode ${mode} --no-rollback`);
      } else {
        // Remove QUIC block rules from live ruleset
        await run('sudo nft -a list chain inet filter forward 2>/dev/null | grep gp-quic-block | sed -n "s/.*handle \\([0-9]*\\)/\\1/p" | while read h; do sudo nft delete rule inet filter forward handle $h; done');
      }
    }

    console.log(`[Arsenal] QUIC block ${enabled ? "enabled" : "disabled"}`);
    res.json({ ok: true, quicBlock: enabled });
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message });
  }
});

/**
 * POST /api/arsenal/schedules — { time: "HH:MM", days: [0-6], mode: "isp"|... }
 */
app.post("/api/arsenal/schedules", async (req, res) => {
  const { time, days, mode } = req.body;
  const valid = ["isp", "zerotrust", "doublehop", "zhop"];
  if (!time || !Array.isArray(days) || !valid.includes(mode)) {
    return res.status(400).json({ ok: false, error: "Invalid schedule parameters" });
  }
  if (!/^\d{1,2}:\d{2}$/.test(time)) {
    return res.status(400).json({ ok: false, error: "Invalid time format (use HH:MM)" });
  }
  if (!days.every(d => Number.isInteger(d) && d >= 0 && d <= 6)) {
    return res.status(400).json({ ok: false, error: "Invalid days (must be integers 0-6)" });
  }
  try {
    const [hour, minute] = time.split(":");
    if (parseInt(hour) > 23 || parseInt(minute) > 59) {
      return res.status(400).json({ ok: false, error: "Invalid time value" });
    }
    const id = Date.now().toString(36);
    const dayStr = days.join(",");
    const cronLine = `${minute} ${hour} * * ${dayStr} root /usr/local/bin/gp-mode ${mode} --no-rollback # gp-schedule-${id}`;

    // Ensure cron file exists
    await run("sudo touch /etc/cron.d/ghostport-schedules && sudo chmod 644 /etc/cron.d/ghostport-schedules");
    fs.writeFileSync("/tmp/gp-sched-line", cronLine + "\n");
    await run("cat /tmp/gp-sched-line | sudo tee -a /etc/cron.d/ghostport-schedules > /dev/null");

    const arsenal = readArsenal();
    arsenal.schedules.push({ id, time, days, mode });
    writeArsenal(arsenal);
    res.json({ ok: true, schedule: { id, time, days, mode } });
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message });
  }
});

/**
 * DELETE /api/arsenal/schedules/:id
 */
app.delete("/api/arsenal/schedules/:id", async (req, res) => {
  const { id } = req.params;
  // Sanitize ID to prevent command injection
  if (!/^[a-z0-9]+$/.test(id)) {
    return res.status(400).json({ ok: false, error: "Invalid schedule ID" });
  }
  try {
    await run(`sudo sed -i '/gp-schedule-${id}/d' /etc/cron.d/ghostport-schedules`);
    const arsenal = readArsenal();
    arsenal.schedules = (arsenal.schedules || []).filter(s => s.id !== id);
    writeArsenal(arsenal);
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message });
  }
});

// ── terminal mode ─────────────────────────────────────────

app.get("/api/terminal-mode", async (req, res) => {
  const result = await run("systemctl is-enabled lightdm 2>/dev/null");
  const enabled = result.out.trim() === "enabled";
  res.json({ ok: true, terminalMode: !enabled });
});

app.post("/api/terminal-mode", async (req, res) => {
  const { enabled } = req.body;
  try {
    if (enabled) {
      console.log("[GhostPort] Terminal mode enabled — disabling lightdm for next boot");
      await run("sudo systemctl disable lightdm");
    } else {
      console.log("[GhostPort] Terminal mode disabled — enabling lightdm for next boot");
      await run("sudo systemctl enable lightdm");
    }
    res.json({ ok: true, terminalMode: enabled, note: "Takes effect on next reboot" });
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message });
  }
});


// ── security scan (Lynis) ────────────────────────────────

let lynisScanRunning = false;

app.get("/api/security/scan", async (req, res) => {
  if (lynisScanRunning) {
    return res.status(409).json({ ok: false, error: "Scan already in progress" });
  }
  lynisScanRunning = true;
  console.log("[Security] Starting Lynis scan...");

  try {
    const reportFile = "/tmp/gp-lynis-report.dat";
    // Remove old report
    try { fs.unlinkSync(reportFile); } catch {}

    const result = await run(
      `sudo lynis audit system --quick --no-colors --no-log --report-file ${reportFile} 2>&1 | tail -5`,
      120000
    );

    // Parse report file
    let report;
    try { report = fs.readFileSync(reportFile, "utf8"); } catch {
      lynisScanRunning = false;
      return res.status(500).json({ ok: false, error: "Scan completed but report not found" });
    }

    const lines = report.split("\n");
    let score = 0;
    const warnings = [];
    const suggestions = [];
    const details = {};

    for (const line of lines) {
      if (line.startsWith("hardening_index=")) {
        score = parseInt(line.split("=")[1]) || 0;
      }
      if (line.startsWith("warning[]=")) {
        const parts = line.replace("warning[]=", "").split("|");
        if (parts[0] && parts[1]) {
          warnings.push({ id: parts[0], message: parts[1], detail: parts[2] || "", fix: parts[3] || "" });
        }
      }
      if (line.startsWith("suggestion[]=")) {
        const parts = line.replace("suggestion[]=", "").split("|");
        if (parts[0] && parts[1]) {
          suggestions.push({ id: parts[0], message: parts[1], fix: parts[2] || "", detail: parts[3] || "" });
        }
      }
      // Grab key stats
      if (line.startsWith("firewall_active=")) details.firewall = line.split("=")[1];
      if (line.startsWith("ids_ips_tooling[]=")) details.ids = (details.ids || []).concat(line.split("=")[1]);
      if (line.startsWith("minimum_password_length=")) details.minPwLen = line.split("=")[1];
      if (line.startsWith("ssh_root_login=")) details.sshRoot = line.split("=")[1];
      if (line.startsWith("pam_cracklib=")) details.pamCracklib = line.split("=")[1];
      if (line.startsWith("file_integrity_tool_installed=")) details.fileIntegrity = line.split("=")[1];
    }

    // Categorize score
    let grade;
    if (score >= 80) grade = "A";
    else if (score >= 70) grade = "B";
    else if (score >= 60) grade = "C";
    else if (score >= 50) grade = "D";
    else grade = "F";

    console.log(`[Security] Scan complete — score: ${score}/100 (grade: ${grade}), ${warnings.length} warnings, ${suggestions.length} suggestions`);
    lynisScanRunning = false;

    res.json({
      ok: true,
      score,
      grade,
      warnings,
      suggestions,
      details,
      scannedAt: new Date().toISOString(),
    });
  } catch (e) {
    lynisScanRunning = false;
    res.status(500).json({ ok: false, error: e.message });
  }
});

// ── start ──────────────────────────────────────────────────
// HTTP — primary, no SSL warnings for customers
http.createServer(app).listen(PORT, "0.0.0.0", () => {
  console.log(`  ☠  HTTP  running on port ${PORT}`);
});

// HTTPS — available for admin/Tailscale use
try {
  const sslOptions = {
    key: fs.readFileSync("/opt/ghostport/ssl/ghostport.key"),
    cert: fs.readFileSync("/opt/ghostport/ssl/ghostport.crt"),
  };
  https.createServer(sslOptions, app).listen(4201, "0.0.0.0", () => {
    console.log("  ☠  HTTPS running on port 4201");
  });
} catch (e) {
  console.log("  [!] HTTPS disabled — no SSL certs found");
}

console.log(`
  ☠  GhostPort Command Deck  ☠
  ─────────────────────────────
  HTTP  → http://0.0.0.0:${PORT}  (customers)
  HTTPS → https://0.0.0.0:4201   (admin)
`);

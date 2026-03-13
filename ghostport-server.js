/**
 * GhostPort — Command Deck API Server
 * Runs on the Pi, controls gp-mode, serves live status
 * Start with: sudo node ghostport-server.js
 */

const express = require("express");
const { exec } = require("child_process");
const path = require("path");
const cors = require("cors");
const crypto = require("crypto");
const https = require("https");
const fs = require("fs");

const app = express();
const PORT = 4200;

app.use(cors());
app.use(express.json());

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
  writeAuth({ passcode, hash, salt });
  console.log("");
  console.log("  ╔═══════════════════════════════════════════╗");
  console.log("  ║   DEVICE PASSCODE (save this!)            ║");
  console.log(`  ║   ${passcode}                      ║`);
  console.log("  ╚═══════════════════════════════════════════╝");
  console.log("");
  console.log("  Use this to log into the Command Deck.");
  console.log("  Recover via SSH: sudo gp-passcode show");
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

  res.setHeader("Set-Cookie", `gp-session=${token}; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=86400`);
  res.json({ ok: true });
});

app.post("/api/auth/logout", (req, res) => {
  const token = getCookie(req, "gp-session");
  if (token) sessions.delete(token);
  res.setHeader("Set-Cookie", "gp-session=; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=0");
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
    if (Date.now() - session.created < SESSION_TTL) {
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
  writeAuth({ passcode, hash, salt });

  // Invalidate all other sessions
  const myToken = getCookie(req, "gp-session");
  for (const [tok] of sessions) {
    if (tok !== myToken) sessions.delete(tok);
  }

  console.log(`[Auth] Passcode changed from ${req.ip}`);
  res.json({ ok: true, passcode, generated: !newPasscode || newPasscode.trim().length < 6 });
});

// ── helpers ────────────────────────────────────────────────

function run(cmd) {
  return new Promise((resolve) => {
    exec(cmd, { timeout: 15000 }, (err, stdout, stderr) => {
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
      run("curl -s --max-time 3 http://localhost/admin/api.php?summary | python3 -c \"import sys,json; d=json.load(sys.stdin); print(d['ads_blocked_today'])\" 2>/dev/null || echo 0"),
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
  const result = await run(
    "curl -s --max-time 3 http://localhost/admin/api.php?summary"
  );
  if (!result.ok || !result.out) {
    return res.json({ ok: false, error: "Pi-hole unreachable" });
  }
  try {
    const data = JSON.parse(result.out);
    res.json({ ok: true, ...data });
  } catch {
    res.json({ ok: false, error: "Failed to parse Pi-hole response" });
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
      run("systemctl is-active dnsmasq").then(r => ({
        name: "dnsmasq", ok: r.out === "active", detail: r.out,
        fix: r.out === "active" ? null : "DHCP server is down — run: sudo systemctl restart dnsmasq", warn: false,
      })),
      run("systemctl is-active pihole-FTL").then(r => ({
        name: "pihole-FTL", ok: r.out === "active", detail: r.out,
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

// Kill switch interval state
let killSwitchInterval = null;
let killSwitchTripped = false;

function startKillSwitch() {
  if (killSwitchInterval) return;
  console.log("[Arsenal] Kill switch enabled — monitoring wg0");
  killSwitchInterval = setInterval(async () => {
    const modeFile = await run("cat /etc/ghostport/current-mode 2>/dev/null || echo isp");
    const mode = modeFile.out.trim();
    if (mode !== "doublehop" && mode !== "zhop") return;

    const wg = await run("ip link show wg0 2>/dev/null | grep -q 'state UNKNOWN\\|state UP' && echo up || echo down");
    const conf = "/etc/unbound/unbound.conf.d/resolvconf_resolvers.conf";

    if (wg.out.trim() === "down" && !killSwitchTripped) {
      killSwitchTripped = true;
      console.log("[Arsenal] KILL SWITCH TRIPPED — wg0 down, killing DNS forwarders");
      await run(`sudo sed -i 's/^\\(\\s*forward-addr:\\)/# KILLED \\1/' ${conf}`);
      await run("sudo systemctl restart unbound");
    } else if (wg.out.trim() === "up" && killSwitchTripped) {
      killSwitchTripped = false;
      console.log("[Arsenal] wg0 recovered — restoring DNS forwarders");
      await run(`sudo sed -i 's/^# KILLED \\(\\s*forward-addr:\\)/\\1/' ${conf}`);
      await run("sudo systemctl restart unbound");
    }
  }, 30000);
}

function stopKillSwitch() {
  if (killSwitchInterval) {
    clearInterval(killSwitchInterval);
    killSwitchInterval = null;
    killSwitchTripped = false;
    console.log("[Arsenal] Kill switch disabled");
  }
}

// Self-heal on startup: uncomment any stuck killed forwarders
(async () => {
  const conf = "/etc/unbound/unbound.conf.d/resolvconf_resolvers.conf";
  const check = await run(`grep -c '# KILLED' ${conf}`);
  if (parseInt(check.out) > 0) {
    console.log("[Arsenal] Self-healing: uncommenting killed DNS forwarders from previous crash");
    await run(`sudo sed -i 's/^# KILLED \\(\\s*forward-addr:\\)/\\1/' ${conf}`);
    await run("sudo systemctl restart unbound");
  }
  // Restore kill switch if it was enabled
  const arsenal = readArsenal();
  if (arsenal.killSwitch) startKillSwitch();
})();

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
      encryptedDns: dnsMode.out.trim() === "on",
      macRandomization: macService.out.trim() === "enabled",
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
    const [cfResult, googleResult, dnsMode, modeFile] = await Promise.all([
      run("dig +short whoami.cloudflare TXT @127.0.0.1"),
      run("dig +short whoami.cloudflare TXT @8.8.8.8"),
      run("sudo gp-dns-switch status"),
      run("cat /etc/ghostport/current-mode 2>/dev/null || echo isp"),
    ]);

    const cfResolver = cfResult.out.replace(/"/g, "").trim();
    const googleResolver = googleResult.out.replace(/"/g, "").trim();
    const encrypted = dnsMode.out.trim() === "on";
    const mode = modeFile.out.trim();
    const isVpn = ["doublehop", "zhop"].includes(mode);

    let passed = true;
    let reason = "DNS resolving through expected path";

    if (isVpn && cfResolver === googleResolver && cfResolver !== "") {
      passed = false;
      reason = "DNS resolver matches direct path — possible leak";
    }

    res.json({
      ok: true, passed, reason,
      localResolver: cfResolver || "No response",
      directResolver: googleResolver || "No response",
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
    const result = await run("sudo pihole updateGravity 2>&1 | tail -5");
    res.json({ ok: result.ok, output: result.out });
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
      const conf = "/etc/unbound/unbound.conf.d/resolvconf_resolvers.conf";
      const check = await run(`grep -c '# KILLED' ${conf}`);
      if (parseInt(check.out) > 0) {
        await run(`sudo sed -i 's/^# KILLED \\(\\s*forward-addr:\\)/\\1/' ${conf}`);
        await run("sudo systemctl restart unbound");
      }
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
 * POST /api/arsenal/schedules — { time: "HH:MM", days: [0-6], mode: "isp"|... }
 */
app.post("/api/arsenal/schedules", async (req, res) => {
  const { time, days, mode } = req.body;
  const valid = ["isp", "zerotrust", "doublehop", "zhop"];
  if (!time || !days || !valid.includes(mode)) {
    return res.status(400).json({ ok: false, error: "Invalid schedule parameters" });
  }
  try {
    const [hour, minute] = time.split(":");
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

// ── start ──────────────────────────────────────────────────
const sslOptions = {
  key: fs.readFileSync("/opt/ghostport/ssl/ghostport.key"),
  cert: fs.readFileSync("/opt/ghostport/ssl/ghostport.crt"),
};
https.createServer(sslOptions, app).listen(PORT, "0.0.0.0", () => {
  console.log(`
  ☠  GhostPort Command Deck  ☠
  ─────────────────────────────
  API running at https://0.0.0.0:${PORT}
  Access from any device on your network:
  → https://<PI_IP>:${PORT}
  `);
});

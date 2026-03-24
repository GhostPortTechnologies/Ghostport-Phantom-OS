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
app.set("trust proxy", false);
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

// ── Privacy-preserving ads tally ─────────────────────────
// Counts blocked/total queries without storing any query data.
// Pi-hole privacy level stays at 3 (anonymous everything).
const TALLY_FILE = "/etc/ghostport/ads-tally.json";

function readTally() {
  try { return JSON.parse(fs.readFileSync(TALLY_FILE, "utf8")); }
  catch { return { blocked: 0, total: 0, lastUpdated: null }; }
}

function writeTally(tally) {
  tally.lastUpdated = new Date().toISOString();
  fs.writeFileSync(TALLY_FILE, JSON.stringify(tally, null, 2));
}

let tallyBaseline = { blocked: 0, total: 0 };
let tallyInterval = null;

async function initTally() {
  // Snapshot current Pi-hole session counts as our baseline
  try {
    const r = await piholeApi("GET", "/stats/summary");
    tallyBaseline.blocked = r.data?.queries?.blocked || 0;
    tallyBaseline.total = r.data?.queries?.total || 0;
    console.log(`[Tally] Baseline: ${tallyBaseline.blocked} blocked / ${tallyBaseline.total} total`);
  } catch {
    console.log("[Tally] Could not read Pi-hole stats for baseline");
  }

  // Flush delta to tally file every 60s
  tallyInterval = setInterval(flushTally, 60000);
}

let flushingTally = false;
async function flushTally() {
  if (flushingTally) return;
  flushingTally = true;
  try {
    const r = await piholeApi("GET", "/stats/summary");
    const liveBlocked = r.data?.queries?.blocked || 0;
    const liveTotal = r.data?.queries?.total || 0;

    const deltaBlocked = Math.max(0, liveBlocked - tallyBaseline.blocked);
    const deltaTotal = Math.max(0, liveTotal - tallyBaseline.total);

    if (deltaBlocked === 0 && deltaTotal === 0) return;

    const tally = readTally();
    tally.blocked += deltaBlocked;
    tally.total += deltaTotal;
    writeTally(tally);

    // Update baseline so we don't double-count
    tallyBaseline.blocked = liveBlocked;
    tallyBaseline.total = liveTotal;
  } catch { /* silent */ }
  finally { flushingTally = false; }
}

// Init tally after a short delay (let Pi-hole auth settle)
setTimeout(initTally, 5000);

app.use(express.json({ limit: "10kb" }));

// Security headers
app.use((req, res, next) => {
  res.setHeader("X-Content-Type-Options", "nosniff");
  res.setHeader("X-Frame-Options", "DENY");
  res.setHeader("Referrer-Policy", "no-referrer");
  res.setHeader("X-XSS-Protection", "0");
  res.setHeader("Permissions-Policy", "camera=(), microphone=(), geolocation=()");
  res.setHeader("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'");
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
  return match ? match.substring(name.length + 1) : null;
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
app.get("/login.js", (req, res) => res.sendFile(path.join(__dirname, "public", "login.js")));
app.get("/install.js", (req, res) => res.sendFile(path.join(__dirname, "public", "install.js")));
app.get("/logo.png", (req, res) => res.sendFile(path.join(__dirname, "public", "logo.png")));
app.get("/sw.js", (req, res) => res.sendFile(path.join(__dirname, "public", "sw.js")));
app.get("/manifest.json", (req, res) => res.sendFile(path.join(__dirname, "public", "manifest.json")));
app.get("/icon-192.png", (req, res) => res.sendFile(path.join(__dirname, "public", "icon-192.png")));
app.get("/icon-512.png", (req, res) => res.sendFile(path.join(__dirname, "public", "icon-512.png")));
app.get("/apple-touch-icon.png", (req, res) => res.sendFile(path.join(__dirname, "public", "apple-touch-icon.png")));
app.get(/^\/login\/?$/, (req, res) => res.redirect("/login.html"));
app.get("/install.html", (req, res) => res.sendFile(path.join(__dirname, "public", "install.html")));
app.get(/^\/install\/?$/, (req, res) => res.redirect("/install.html"));
app.use("/qr", express.static(path.join(__dirname, "public", "qr")));

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
  if (!crypto.timingSafeEqual(Buffer.from(hash, "hex"), Buffer.from(auth.hash, "hex"))) {
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
  const csrfToken = crypto.randomBytes(32).toString("hex");
  sessions.set(token, { created: Date.now(), ip, absoluteExpiry: Date.now() + 7 * 24 * 60 * 60 * 1000, csrf: csrfToken });
  console.log(`[Auth] Successful login from ${ip}`);

  res.setHeader("Set-Cookie", `gp-session=${token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=86400${req.secure ? "; Secure" : ""}`);
  res.json({ ok: true, csrfToken });
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
    if (Date.now() - session.created < SESSION_TTL && (!session.absoluteExpiry || Date.now() < session.absoluteExpiry)) {
      return res.json({ ok: true, authenticated: true });
    }
    sessions.delete(token);
  }
  res.json({ ok: true, authenticated: false });
});

// Secure temp file helper — avoids predictable names in /tmp
function escapeHtml(s) {
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

function gpTmpFile(prefix) {
  return `/tmp/${prefix}-${crypto.randomBytes(8).toString("hex")}`;
}


// ── fleet activation (no auth — initial device setup) ────
let activateAttempts = { count: 0, resetAt: 0 };
app.post("/api/fleet/activate", async (req, res) => {
  // Rate limit: max 5 attempts per 10 minutes
  const now = Date.now();
  if (now > activateAttempts.resetAt) { activateAttempts = { count: 0, resetAt: now + 600000 }; }
  activateAttempts.count++;
  if (activateAttempts.count > 5) {
    return res.status(429).json({ ok: false, error: "Too many activation attempts. Try again later." });
  }
  try {
    const { license_key } = req.body || {};

    // Validate license key format (XXXX-XXXX-XXXX-XXXX)
    if (!license_key || !/^[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$/i.test(license_key)) {
      return res.status(400).json({ ok: false, error: "Invalid license key format" });
    }

    // Check if device is already activated
    const authFile = "/etc/ghostport/auth.json";
    if (fs.existsSync(authFile)) {
      try {
        const auth = JSON.parse(fs.readFileSync(authFile, "utf8"));
        if (auth.hash) {
          return res.status(409).json({ ok: false, error: "Device already activated. Use the login page." });
        }
      } catch {}
    }
    // Also block if fleet.json already exists (already registered)
    if (fs.existsSync("/etc/ghostport/fleet.json")) {
      return res.status(409).json({ ok: false, error: "Device already registered. Use the login page." });
    }

    // Generate device passcode
    const chars = "ABCDEFGHJKMNPQRSTUVWXYZ23456789";
    const passParts = [];
    for (let p = 0; p < 3; p++) {
      let s = "";
      for (let i = 0; i < 4; i++) s += chars[crypto.randomInt(chars.length)];
      passParts.push(s);
    }
    const passcode = "GP-" + passParts.join("-");

    // Hash and save passcode
    const salt = crypto.randomBytes(32).toString("hex");
    const hash = crypto.scryptSync(passcode, salt, 64).toString("hex");
    fs.mkdirSync("/etc/ghostport", { recursive: true });
    fs.writeFileSync(authFile, JSON.stringify({ hash, salt }, null, 2));
    fs.chmodSync(authFile, 0o600);
    // Clear any existing sessions from before activation
    sessions.clear();

    // Auto-configure Pi-hole password
    const phChars = "ABCDEFGHJKMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789";
    let piholePw = "";
    for (let i = 0; i < 24; i++) piholePw += phChars[crypto.randomInt(phChars.length)];
    exec("sudo pihole setpassword " + JSON.stringify(piholePw), () => {});
    fs.writeFileSync("/etc/ghostport/pihole.json", JSON.stringify({ password: piholePw }));
    fs.chmodSync("/etc/ghostport/pihole.json", 0o600);

    // Randomize WiFi password
    let wifiPw = "";
    for (let i = 0; i < 16; i++) wifiPw += phChars[crypto.randomInt(phChars.length)];
    const hostapdConf = "/etc/hostapd/hostapd.conf";
    if (fs.existsSync(hostapdConf)) {
      let hConf = fs.readFileSync(hostapdConf, "utf8");
      fs.writeFileSync(hostapdConf + ".bak", hConf);
      hConf = hConf.replace(/^ssid=.*/m, "ssid=GhostPort Router");
      hConf = hConf.replace(/^wpa_passphrase=.*/m, "wpa_passphrase=" + wifiPw);
      fs.writeFileSync(hostapdConf, hConf);
      exec("systemctl restart hostapd", () => {});
    }

    // Fleet registration
    let fleetStatus = "not registered";
    const serial = (() => {
      try {
        const cpuinfo = fs.readFileSync("/proc/cpuinfo", "utf8");
        const m = cpuinfo.match(/Serial\s*:\s*(\S+)/);
        return m ? m[1] : "unknown";
      } catch { return "unknown"; }
    })();
    const hostname = require("os").hostname();

    try {
      const fleetServer = "http://10.66.66.1:8080";
      const fleetToken = (() => { try { const t = JSON.parse(fs.readFileSync("/etc/ghostport/fleet.json","utf8")).fleet_token; if (!t) throw new Error("No fleet token"); return t; } catch(e) { throw new Error("Fleet token not found in /etc/ghostport/fleet.json: " + e.message); } })();
      const regBody = JSON.stringify({
        serial, name: hostname, firmware_version: "0.1.0",
        hardware: "pi5", license_key
      });

      const regResult = await new Promise((resolve, reject) => {
        const regReq = http.request(fleetServer + "/fleet/devices/register", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Content-Length": Buffer.byteLength(regBody),
            "Authorization": "Bearer " + fleetToken
          },
          timeout: 15000
        }, regRes => {
          let data = "";
          regRes.on("data", c => data += c);
          regRes.on("end", () => {
            try { resolve(JSON.parse(data)); } catch { resolve({}); }
          });
        });
        regReq.on("error", () => resolve({}));
        regReq.on("timeout", () => { regReq.destroy(); resolve({}); });
        regReq.write(regBody);
        regReq.end();
      });

      if (regResult.device && regResult.device.id) {
        const fleetConf = {
          device_id: regResult.device.id,
          serial,
          license_key,
          fleet_server: "http://10.66.66.1:8080"
        };
        fs.writeFileSync("/etc/ghostport/fleet.json", JSON.stringify(fleetConf, null, 2));
        fs.chmodSync("/etc/ghostport/fleet.json", 0o600);
        fleetStatus = "registered";
        console.log("[Fleet] Device registered:", regResult.device.id);
      } else {
        console.log("[Fleet] Registration response:", JSON.stringify(regResult));
        fleetStatus = "registration pending";
      }
    } catch (e) {
      console.error("[Fleet] Registration error:", e.message);
      fleetStatus = "offline — will retry";
    }

    console.log("[Setup] Device activated with passcode GP-****-****-****");

    res.json({
      ok: true,
      passcode,
      wifi_ssid: "GhostPort Router",
      wifi_password: wifiPw,
      fleet_status: fleetStatus
    });
  } catch (e) {
    console.error("[Setup] Activation error:", e.message);
    res.status(500).json({ ok: false, error: "Activation failed" });
  }
});

// ── session middleware (everything below requires auth) ────

app.use((req, res, next) => {
  const token = getCookie(req, "gp-session");
  if (token && sessions.has(token)) {
    const session = sessions.get(token);
    if (Date.now() - session.created < SESSION_TTL && (!session.absoluteExpiry || Date.now() < session.absoluteExpiry)) {
      session.created = Date.now(); // sliding window — extend on activity
      // CSRF check on state-changing requests
      if (["POST", "PUT", "DELETE", "PATCH"].includes(req.method) && req.path.startsWith("/api/")) {
        const csrfHeader = req.headers["x-csrf-token"] || "";
        if (csrfHeader !== session.csrf) {
          return res.status(403).json({ ok: false, error: "Invalid CSRF token" });
        }
      }
      return next();
    }
    sessions.delete(token);
  }
  if (req.path.startsWith("/api/")) {
    return res.status(401).json({ ok: false, error: "Not authenticated" });
  }
  return res.redirect("/login.html");
});

// CSRF token endpoint — frontend fetches this after login
app.get("/api/auth/csrf", (req, res) => {
  const token = getCookie(req, "gp-session");
  if (token && sessions.has(token)) {
    return res.json({ ok: true, csrfToken: sessions.get(token).csrf });
  }
  res.status(401).json({ ok: false, error: "Not authenticated" });
});

app.use(express.static(path.join(__dirname, "public")));

// Prune expired sessions every 10 minutes
setInterval(() => {
  const now = Date.now();
  let pruned = 0;
  for (const [token, session] of sessions) {
    if (now - session.created >= SESSION_TTL) {
      sessions.delete(token);
      pruned++;
    }
  }
  if (pruned > 0) console.log(`[Auth] Pruned ${pruned} expired session(s)`);
}, 600000);

// ── change passcode (protected) ───────────────────────────

app.post("/api/auth/change-passcode", (req, res) => {
  const { currentPasscode, newPasscode } = req.body;
  if (!currentPasscode) return res.status(400).json({ ok: false, error: "Current passcode required" });

  const auth = readAuth();
  if (!auth) return res.status(500).json({ ok: false, error: "Auth not configured" });

  const currentHash = hashPasscode(currentPasscode.toUpperCase().trim(), auth.salt);
  if (!crypto.timingSafeEqual(Buffer.from(currentHash, "hex"), Buffer.from(auth.hash, "hex"))) {
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

  const isGenerated = !newPasscode || newPasscode.trim().length < 6;
  console.log(`[Auth] Passcode changed from ${req.ip}`);
  res.json({ ok: true, passcode: isGenerated ? passcode : undefined, generated: isGenerated });
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
      piholeApi("GET", "/stats/summary").then(r => {
        const live = r.data?.queries?.blocked || 0;
        const tally = readTally();
        const delta = Math.max(0, live - tallyBaseline.blocked);
        return { out: String(live), allTime: String(tally.blocked + delta), ok: true };
      }).catch(() => ({ out: "0", allTime: "0", ok: false })),
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
      adsBlockedAllTime: parseInt(pihole.allTime?.trim()) || 0,
      rollback: rollbackInfo,
      raw: gpStatus.out,
    });
  } catch (e) {
    console.error("[GhostPort] Error:", e.message);
    res.status(500).json({ ok: false, error: "Operation failed" });
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
    return res.status(500).json({ ok: false, error: "Mode switch failed" });
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
  if (action === "stop") {
    // Safety: Tailscale is the always-on management plane — never stop it
    return res.status(400).json({ ok: false, error: "Tailscale is the management plane and cannot be stopped. This prevents remote lockout." });
  }
  const cmd = "sudo systemctl enable --now tailscaled";
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
  const peers = lines.slice(1).filter(line => line.split("\t").length >= 7).map((line) => {
    const [pubkey, , endpoint, allowedIps, lastHandshake, rx, tx] = line.split("\t");
    return {
      pubkey: pubkey?.slice(0, 12) + "...",
      endpoint,
      allowedIps,
      lastHandshake: lastHandshake === "0" ? "never" : new Date((parseInt(lastHandshake, 10) || 0) * 1000).toLocaleTimeString(),
      rx: `${((parseInt(rx, 10) || 0) / 1024).toFixed(1)} KiB`,
      tx: `${((parseInt(tx, 10) || 0) / 1024).toFixed(1)} KiB`,
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

const HOSTAPD_CONF = "/etc/hostapd/hostapd.conf";

app.get("/api/hostapd/config", async (req, res) => {
  try {
    const confR = await run("sudo cat " + HOSTAPD_CONF);
    if (!confR.ok) throw new Error("Could not read hostapd config");
    const conf = confR.out;
    const ssid = conf.match(/^ssid=(.+)$/m)?.[1] || "";
    const pass = conf.match(/^wpa_passphrase=(.+)$/m)?.[1] || "";
    res.json({ ok: true, ssid, passphrase: pass });
  } catch (e) {
    res.status(500).json({ ok: false, error: "Could not read hostapd config" });
  }
});

app.post("/api/hostapd/config", async (req, res) => {
  const { ssid, passphrase } = req.body;
  if (!ssid && !passphrase) {
    return res.status(400).json({ ok: false, error: "Provide ssid and/or passphrase" });
  }
  if (ssid !== undefined && (ssid.length < 1 || ssid.length > 32)) {
    return res.status(400).json({ ok: false, error: "SSID must be 1-32 characters" });
  }
  if (passphrase !== undefined && (passphrase.length < 8 || passphrase.length > 63)) {
    return res.status(400).json({ ok: false, error: "Password must be 8-63 characters" });
  }
  // Reject control characters and newlines to prevent config injection
  const hasControlChars = /[\x00-\x1f\x7f]/;
  if ((ssid && hasControlChars.test(ssid)) || (passphrase && hasControlChars.test(passphrase))) {
    return res.status(400).json({ ok: false, error: "SSID and password must not contain control characters" });
  }

  try {
    const readConf = await run("sudo cat " + HOSTAPD_CONF);
    if (!readConf.ok) throw new Error("Could not read hostapd config");
    let conf = readConf.out;
    // Backup current config
    await run("sudo cp " + HOSTAPD_CONF + " " + HOSTAPD_CONF + ".bak");

    if (ssid) conf = conf.replace(/^ssid=.+$/m, `ssid=${ssid}`);
    if (passphrase) conf = conf.replace(/^wpa_passphrase=.+$/m, `wpa_passphrase=${passphrase}`);

    const tmpHostapd = gpTmpFile("gp-hostapd") + ".conf";
    fs.writeFileSync(tmpHostapd, conf);
    await run("sudo cp " + tmpHostapd + " " + HOSTAPD_CONF);
    fs.unlinkSync(tmpHostapd);
    console.log(`[GhostPort] hostapd config updated — SSID: ${ssid || "(unchanged)"}, passphrase: ${passphrase ? "(changed)" : "(unchanged)"}`);

    // Restart hostapd to apply
    const result = await run("sudo systemctl restart hostapd");
    if (!result.ok) {
      // Rollback on failure
      console.log("[GhostPort] hostapd restart failed after config change — rolling back");
      await run("sudo cp " + HOSTAPD_CONF + ".bak " + HOSTAPD_CONF);
      await run("sudo systemctl restart hostapd");
      return res.status(500).json({ ok: false, error: "hostapd failed to start with new config — rolled back" });
    }

    // Verify AP is broadcasting
    const check = await run("sleep 2 && iw dev wlan0 info 2>/dev/null | grep -q 'type AP' && echo up || echo down");
    const isUp = check.out.trim() === "up";
    if (!isUp) {
      console.log("[GhostPort] AP not broadcasting after config change — rolling back");
      await run("sudo cp " + HOSTAPD_CONF + ".bak " + HOSTAPD_CONF);
      await run("sudo systemctl restart hostapd");
      return res.status(500).json({ ok: false, error: "AP failed to broadcast with new config — rolled back" });
    }

    res.json({ ok: true, status: "WiFi network updated — reconnect with new credentials" });
  } catch (e) {
    console.error("[Hostapd] Config update error:", e.message);
    res.status(500).json({ ok: false, error: "Failed to update WiFi config" });
  }
});

// ── repair tools ──────────────────────────────────────────

app.post("/api/repair/dns", async (req, res) => {
  console.log("[GhostPort] Restarting DNS stack");
  const r1 = await run("sudo systemctl restart dnsmasq");
  const r2 = await run("sudo systemctl restart pihole-FTL");
  // Only restart unbound/cloudflared if they exist
  const r3 = await run("systemctl is-active unbound >/dev/null 2>&1 && sudo systemctl restart unbound; echo done");
  const r4 = await run("systemctl is-active cloudflared >/dev/null 2>&1 && sudo systemctl restart cloudflared; echo done");
  // Verify resolution works
  const check = await run("sleep 1 && dig +short +time=3 example.com @127.0.0.1");
  const ok = check.ok && check.out.length > 0;
  console.log(`[GhostPort] DNS restart: ${ok ? "resolving" : "still broken"}`);
  res.json({
    ok,
    status: ok ? "DNS stack restarted — resolving" : "DNS restarted but resolution failed",
    services: { dnsmasq: r1.ok, piholeFTL: r2.ok },
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
  const rawMode = modeFile.out.trim() || "isp";
  const VALID_MODES = ["isp", "zerotrust", "doublehop", "zhop"];
  const mode = VALID_MODES.includes(rawMode) ? rawMode : "isp";
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
    // Remove fleet registration (device will need re-activation)
    await run("sudo rm -f /etc/ghostport/fleet.json");
    // Remove subscription state
    await run("sudo rm -f /etc/ghostport/subscription.json");
    // Remove Family Shield config
    await run("sudo rm -f /etc/ghostport/family-shield.json");
    // Clear ads tally
    await run("sudo rm -f /etc/ghostport/ads-tally.json");
    // Remove scheduled mode switches
    await run("sudo rm -f /etc/cron.d/ghostport-schedules");
    // Clear temp files
    await run("sudo rm -f /tmp/gp-sched-line /tmp/gp-current-mode");
    // Clear mode state to ISP
    await run("echo isp | sudo tee /etc/ghostport/current-mode");
    console.log("[GhostPort] Factory reset complete — rebooting");
    res.json({ ok: true, status: "Factory reset complete. Rebooting..." });
    setTimeout(() => run("sudo reboot"), 3000);
  } catch (e) {
    console.error("[GhostPort] Error:", e.message);
    res.status(500).json({ ok: false, error: "Operation failed" });
  }
});

// ── pihole stats by range ─────────────────────────────────

app.get("/api/pihole/stats", async (req, res) => {
  try {
    // Live session count (resets on Pi-hole restart)
    const liveRes = await piholeApi("GET", "/stats/summary");
    const liveBlocked = liveRes.data?.queries?.blocked || 0;
    const liveTotal = liveRes.data?.queries?.total || 0;

    // Cumulative tally (persists across restarts, no query data stored)
    const tally = readTally();
    const liveDelta = Math.max(0, liveBlocked - tallyBaseline.blocked);
    const allTimeBlocked = tally.blocked + liveDelta;

    res.json({
      ok: true,
      session:  { blocked: liveBlocked, total: liveTotal },
      allTime:  { blocked: allTimeBlocked, total: tally.total + Math.max(0, liveTotal - tallyBaseline.total) },
    });
  } catch (e) {
    console.error("[GhostPort] Error:", e.message);
    res.status(500).json({ ok: false, error: "Operation failed" });
  }
});

// ── wireguard setup ──────────────────────────────────────

app.get("/api/wireguard/status", async (req, res) => {
  try {
    const hasConfig = await run("sudo test -s /etc/wireguard/wg0.conf && echo yes || echo no");
    const ifUp = await run("ip link show wg0 2>/dev/null | grep -q 'state UNKNOWN\\|state UP' && echo up || echo down");
    res.json({ ok: true, configured: hasConfig.out.trim() === "yes", status: ifUp.out.trim() });
  } catch (e) {
    console.error("[GhostPort] Error:", e.message);
    res.status(500).json({ ok: false, error: "Operation failed" });
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
    const tmpFile = gpTmpFile("gp-wg0") + ".conf";
    fs.writeFileSync(tmpFile, sanitizedConfig);
    try {
    await run("sudo mkdir -p /etc/wireguard");
    // Backup existing config before overwriting
    await run("sudo cp /etc/wireguard/wg0.conf /etc/wireguard/wg0.conf.bak 2>/dev/null; true");
    await run(`sudo cp ${tmpFile} /etc/wireguard/wg0.conf`);
    await run("sudo chmod 600 /etc/wireguard/wg0.conf");
    } finally { try { fs.unlinkSync(tmpFile); } catch (_) {} }
    // Restart WireGuard
    await run("sudo systemctl stop wg-quick@wg0 2>/dev/null");
    const start = await run("sudo systemctl start wg-quick@wg0");
    const check = await run("sleep 2 && ip link show wg0 2>/dev/null | grep -q 'state UNKNOWN\\|state UP' && echo up || echo down");
    const isUp = check.out.trim() === "up";
    console.log(`[WireGuard] Config saved and ${isUp ? "tunnel is up" : "tunnel failed to start"}`);
    res.json({ ok: true, status: isUp ? "up" : "down", message: isUp ? "WireGuard tunnel is up" : "Config saved but tunnel failed to start — check your config" });
  } catch (e) {
    console.error("[GhostPort] Error:", e.message);
    res.status(500).json({ ok: false, error: "Operation failed" });
  }
});

app.post("/api/wireguard/restore", async (req, res) => {
  try {
    const hasBak = await run("sudo test -s /etc/wireguard/wg0.conf.bak && echo yes || echo no");
    if (hasBak.out.trim() !== "yes") {
      return res.status(404).json({ ok: false, error: "No backup config found" });
    }
    await run("sudo cp /etc/wireguard/wg0.conf.bak /etc/wireguard/wg0.conf");
    await run("sudo chmod 600 /etc/wireguard/wg0.conf");
    await run("sudo systemctl stop wg-quick@wg0 2>/dev/null");
    const start = await run("sudo systemctl start wg-quick@wg0");
    const check = await run("sleep 2 && ip link show wg0 2>/dev/null | grep -q 'state UNKNOWN\\|state UP' && echo up || echo down");
    const isUp = check.out.trim() === "up";
    console.log(`[WireGuard] Config restored from backup — ${isUp ? "tunnel up" : "tunnel down"}`);
    res.json({ ok: true, status: isUp ? "up" : "down", message: isUp ? "Previous config restored — tunnel is up" : "Previous config restored but tunnel failed to start" });
  } catch (e) {
    console.error("[GhostPort] Error:", e.message);
    res.status(500).json({ ok: false, error: "Operation failed" });
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
    console.error("[GhostPort] Error:", e.message);
    res.status(500).json({ ok: false, error: "Operation failed" });
  }
});

// ── speed test ───────────────────────────────────────────

let lastSpeedtest = 0;
app.post("/api/tools/speedtest", async (req, res) => {
  const now = Date.now();
  if (now - lastSpeedtest < 120000) {
    return res.status(429).json({ ok: false, error: "Speed test throttled — wait 2 minutes" });
  }
  lastSpeedtest = now;
  try {
    // Check if speedtest-cli is available
    const which = await run("which speedtest-cli 2>/dev/null || which speedtest 2>/dev/null");
    if (!which.ok || !which.out.trim()) {
      return res.json({ ok: false, error: "speedtest-cli not installed — run: sudo apt install speedtest-cli" });
    }
    console.log("[Tools] Running speed test...");
    const result = await run("speedtest-cli --json --timeout 60 2>/dev/null || speedtest --format=json 2>/dev/null", 120000);
    if (!result.ok) return res.json({ ok: false, error: "Speed test failed" });
    let data;
    try { data = JSON.parse(result.out); } catch (_) {
      return res.json({ ok: false, error: "Speed test returned invalid data" });
    }
    res.json({
      ok: true,
      download: (data.download / 1e6).toFixed(1),
      upload: (data.upload / 1e6).toFixed(1),
      ping: data.ping?.toFixed(0) || data.server?.latency?.toFixed(0) || "?",
      server: data.server?.sponsor || data.server?.name || "Unknown",
    });
  } catch (e) {
    res.status(500).json({ ok: false, error: "Speed test failed" });
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
    const ipRegex = /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/;

    const [gw, dns, ext] = await Promise.all([
      ipRegex.test(gatewayIp) ? run(`ping -c3 -W2 '${gatewayIp}' 2>/dev/null`) : Promise.resolve({ ok: false, out: "" }),
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
    console.error("[GhostPort] Error:", e.message);
    res.status(500).json({ ok: false, error: "Operation failed" });
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
    console.error("[GhostPort] Error:", e.message);
    res.status(500).json({ ok: false, error: "Operation failed" });
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
    console.error("[GhostPort] Error:", e.message);
    res.status(500).json({ ok: false, error: "Operation failed" });
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
    console.error("[GhostPort] Error:", e.message);
    res.status(500).json({ ok: false, error: "Operation failed" });
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
    console.error("[GhostPort] Error:", e.message);
    res.status(500).json({ ok: false, error: "Operation failed" });
  }
});

// ── backup config ────────────────────────────────────────

app.get("/api/tools/backup", async (req, res) => {
  try {
    const backup = {};
    const files = {
      arsenal: "/etc/ghostport/arsenal.json",
      currentMode: "/etc/ghostport/current-mode",
      // wireguard: excluded — contains PrivateKey (security risk in backups)
      hostapd: "/etc/hostapd/hostapd.conf",
    };
    for (const [key, filePath] of Object.entries(files)) {
      try {
        const r = await run("sudo cat " + filePath + " 2>/dev/null");
        backup[key] = r.ok ? r.out.trim() : null;
      } catch { backup[key] = null; }
    }
    // Include schedules from arsenal
    try { backup.arsenal = JSON.parse(backup.arsenal); } catch { /* keep as string */ }
    backup.exportDate = new Date().toISOString();
    backup.version = "1.2";

    res.setHeader("Content-Type", "application/json");
    res.setHeader("Content-Disposition", "attachment; filename=ghostport-backup.json");
    res.json(backup);
  } catch (e) {
    console.error("[GhostPort] Error:", e.message);
    res.status(500).json({ ok: false, error: "Operation failed" });
  }
});

app.post("/api/tools/restore", async (req, res) => {
  try {
    const backup = req.body;
    if (!backup || !backup.version) {
      return res.status(400).json({ ok: false, error: "Invalid backup file" });
    }
    // Restore arsenal config (use mutex to prevent race with concurrent arsenal writes)
    if (backup.arsenal) {
      const data = typeof backup.arsenal === "string" ? backup.arsenal : JSON.stringify(backup.arsenal, null, 2);
      try { JSON.parse(typeof backup.arsenal === "string" ? backup.arsenal : data); } catch (_) {
        return res.status(400).json({ ok: false, error: "Invalid arsenal JSON in backup" });
      }
      await withArsenal((arsenal) => {
        const restored = JSON.parse(data);
        Object.assign(arsenal, restored);
      });
    }
    // Restore WireGuard config
    if (backup.wireguard && backup.wireguard.includes("[Interface]")) {
      // Strip dangerous directives (same as /api/wireguard/setup)
      const dangerousDirectives = /^\s*(PostUp|PostDown|PreUp|PreDown|SaveConfig)\s*=.*$/gmi;
      const safeWgConfig = backup.wireguard.replace(dangerousDirectives, "# [removed by GhostPort for security]");
      await run("sudo mkdir -p /etc/wireguard");
      const tmpWg = gpTmpFile("gp-restore-wg") + ".conf";
      fs.writeFileSync(tmpWg, safeWgConfig);
      await run(`sudo cp ${tmpWg} /etc/wireguard/wg0.conf`);
      await run("sudo chmod 600 /etc/wireguard/wg0.conf");
      try { fs.unlinkSync(tmpWg); } catch {}
      // Don't use wg-quick service — gp-mode manages wg0 directly
      const currentMode = fs.readFileSync("/etc/ghostport/current-mode", "utf8").trim();
      if (["doublehop", "zhop"].includes(currentMode)) {
        await run(`sudo gp-mode ${currentMode} --no-rollback`);
      }
      console.log("[Tools] WireGuard config restored");
    }
    // Restore hostapd config
    if (backup.hostapd && backup.hostapd.includes("ssid=")) {
      await run("sudo cp /etc/hostapd/hostapd.conf /etc/hostapd/hostapd.conf.bak");
      const tmpHap = gpTmpFile("gp-restore-hostapd") + ".conf";
      fs.writeFileSync(tmpHap, backup.hostapd);
      await run("sudo cp " + tmpHap + " /etc/hostapd/hostapd.conf");
      try { fs.unlinkSync(tmpHap); } catch {}
      await run("sudo systemctl restart hostapd");
      console.log("[Tools] hostapd config restored");
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
    console.error("[Tools] Restore error:", e.message);
    res.status(500).json({ ok: false, error: "Config restore failed" });
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
      run("ip route | awk '/default via/{print $3}' | head -1").then(async gwR => {
        const gw = gwR.out.trim();
        if (!gw) return { name: "Gateway", ok: false, detail: "No default route", fix: "Check network config", warn: false };
        if (!/^[0-9a-f.:]+$/i.test(gw)) return { name: "Gateway", ok: false, detail: "Invalid gateway", fix: "Check network config", warn: false };
        const p = await run(`ping -c1 -W3 '${gw}'`);
        return { name: "Gateway", ok: p.ok, detail: p.ok ? `${gw} reachable` : `${gw} unreachable`,
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
      run("ip link show wg0 2>/dev/null | grep -q 'state UNKNOWN\\|state UP' && echo up || echo down").then(r => {
        const up = r.out.trim() === "up";
        const critical = wgModes.includes(mode);
        return { name: "WireGuard", ok: up || !critical, detail: up ? "wg0 up" : "wg0 down",
          fix: !up && critical ? "WireGuard required for this mode — run: sudo gp-mode " + mode + " --no-rollback" : (!up ? "WireGuard is down (not required in current mode)" : null),
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
    console.error("[GhostPort] Error:", e.message);
    res.status(500).json({ ok: false, error: "Operation failed" });
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
        { type: "section", text: { type: "mrkdwn", text: `*Description:*\n${description.trim().replace(/[\r\n]+/g, " ").slice(0, 500)}` } },
        contact ? { type: "section", text: { type: "mrkdwn", text: `*Contact:* ${contact.replace(/[\r\n]+/g, " ").slice(0, 100)}` } } : null,
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
        description: description.trim().replace(/[\r\n]+/g, " ").slice(0, 500),
        color: 0x39ff8f,
        fields: [
          contact ? { name: "Contact", value: contact.replace(/[\r\n]+/g, " ").slice(0, 100), inline: true } : null,
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
    res.status(500).json({ ok: false, error: "Failed to send ticket" });
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

// Mutex to prevent concurrent read-modify-write races on arsenal.json
let arsenalLock = Promise.resolve();
function withArsenal(fn) {
  arsenalLock = arsenalLock.then(async () => {
    const arsenal = readArsenal();
    const result = await fn(arsenal);
    writeArsenal(arsenal);
    return result;
  }).catch(e => {
    console.error("[Arsenal] withArsenal error:", e.message);
  });
  return arsenalLock;
}

// Kill switch — blocks all forwarded traffic if wg0 drops in VPN modes
let killSwitchInterval = null;
let killSwitchTripped = false;

// DNS leak monitor — background check every 30s in VPN modes
let dnsLeakInterval = null;
let dnsLeakDetected = false;
let cachedIspIp = null;
let ispIpCacheTime = 0;

function startKillSwitch() {
  if (killSwitchInterval) { clearInterval(killSwitchInterval); killSwitchInterval = null; }
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
      // Also drop the Pi's own outbound (except SSH, Tailscale, and port 4200 management)
      await run("sudo nft add rule inet filter output oifname eth0 tcp dport 22 counter accept comment \"gp-killswitch\"");
      await run("sudo nft add rule inet filter output oifname eth0 udp dport 41641 counter accept comment \"gp-killswitch\"");
      await run("sudo nft add rule inet filter output oifname eth0 tcp dport { 4200, 4201 } counter accept comment \"gp-killswitch\"");
      await run("sudo nft add rule inet filter output oifname eth0 counter drop comment \"gp-killswitch\"");
    } else if (wg.out.trim() === "up" && killSwitchTripped) {
      killSwitchTripped = false;
      console.log("[Arsenal] wg0 recovered — restoring traffic");
      // Remove kill switch rules and reapply mode profile
      if (["isp", "zerotrust", "doublehop", "zhop"].includes(mode)) {
        await run(`sudo gp-mode ${mode} --no-rollback`);
      }
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
      if (!["isp", "zerotrust", "doublehop", "zhop"].includes(mode)) return;
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

    // Cache ISP IP — refresh every hour in case ISP changes
    if (!cachedIspIp || (Date.now() - ispIpCacheTime > 3600000)) {
      const ispResult = await run("curl -s --max-time 5 --interface eth0 ifconfig.me");  // dual-stack (v4+v6) to catch both leak types
      cachedIspIp = ispResult.out.trim();
      ispIpCacheTime = Date.now();
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
          await run("sudo nft add rule inet filter output oifname eth0 tcp dport 22 counter accept comment \"gp-killswitch\"");
          await run("sudo nft add rule inet filter output oifname eth0 udp dport 41641 counter accept comment \"gp-killswitch\"");
          await run("sudo nft add rule inet filter output oifname eth0 tcp dport { 4200, 4201 } counter accept comment \"gp-killswitch\"");
          await run("sudo nft add rule inet filter output oifname eth0 counter drop comment \"gp-killswitch\"");
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
    console.error("[GhostPort] Error:", e.message);
    res.status(500).json({ ok: false, error: "Operation failed" });
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
    // Use double-quoting and explicit reject of shell metacharacters
    if (/['"`$\\;|&<>]/.test(sanitizedPw)) {
      return res.status(400).json({ ok: false, error: "Password contains invalid characters" });
    }
    const result = await run("sudo pihole setpassword " + JSON.stringify(sanitizedPw));
    if (!result.ok) return res.status(500).json({ ok: false, error: "Failed to set Pi-hole password" });
    // Save new credentials and re-authenticate
    fs.writeFileSync(PIHOLE_FILE, JSON.stringify({ password: newPassword }, null, 2));
    fs.chmodSync(PIHOLE_FILE, 0o600);
    const authed = await piholeAuth();
    console.log("[Pi-hole] Password reset and re-authenticated");
    res.json({ ok: true, connected: authed });
  } catch (e) {
    console.error("[GhostPort] Error:", e.message);
    res.status(500).json({ ok: false, error: "Operation failed" });
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
    console.error("[GhostPort] Error:", e.message);
    res.status(500).json({ ok: false, error: "Operation failed" });
  }
});

/**
 * GET /api/arsenal/clients
 */
app.get("/api/arsenal/clients", async (req, res) => {
  try {
    const leases = await run("cat /var/lib/misc/dnsmasq.leases 2>/dev/null");
    const clients = leases.out.split("\n").filter(Boolean).filter(line => line.split(/\s+/).length >= 4).map(line => {
      const parts = line.split(/\s+/);
      return {
        ip: parts[2],
        mac: parts[1],
        hostname: (!parts[3] || parts[3] === "*") ? "Unknown" : escapeHtml(parts[3]),
        expiry: parts[0] === "0" ? "static" : new Date(parseInt(parts[0]) * 1000).toLocaleTimeString(),
      };
    });
    res.json({ ok: true, clients });
  } catch (e) {
    console.error("[GhostPort] Error:", e.message);
    res.status(500).json({ ok: false, error: "Operation failed" });
  }
});

/**
 * POST /api/arsenal/dnstest
 */
let lastDnsTest = 0;
app.post("/api/arsenal/dnstest", async (req, res) => {
  const now = Date.now();
  if (now - lastDnsTest < 30000) {
    return res.status(429).json({ ok: false, error: "DNS test throttled — wait 30 seconds" });
  }
  lastDnsTest = now;
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
    const ispResult = await run("curl -s --max-time 5 --interface eth0 ifconfig.me");  // dual-stack (v4+v6) to catch both leak types
    const ispIp = ispResult.out.trim();

    let passed = true;
    let reason = "DNS resolving through expected path";

    if (!dnsResolverIp) {
      passed = false;
      reason = "Could not determine DNS resolver — check Pi-hole/cloudflared";
    } else if ((dnsResolverIp === ispIp || dnsResolverIp === publicIp) && isVpn) {
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
    console.error("[GhostPort] Error:", e.message);
    res.status(500).json({ ok: false, error: "Operation failed" });
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
      await run(`sudo sed -i '/pihole updateGravity\\|pihole -g/s/^[0-9].*\\(root.*\\)/30 3 * * * \\1/' '${cronFile}'`);
    } else {
      await run(`sudo sed -i '/pihole updateGravity\\|pihole -g/s/^[0-9].*\\(root.*\\)/30 3 * * 0 \\1/' ${cronFile}`);
    }
    await withArsenal(arsenal => { arsenal.blocklistFreq = freq; });
    res.json({ ok: true, freq });
  } catch (e) {
    console.error("[GhostPort] Error:", e.message);
    res.status(500).json({ ok: false, error: "Operation failed" });
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
    console.error("[GhostPort] Error:", e.message);
    res.status(500).json({ ok: false, error: "Operation failed" });
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
    console.error("[GhostPort] Error:", e.message);
    res.status(500).json({ ok: false, error: "Operation failed" });
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
      await withArsenal(arsenal => { arsenal.encryptedDns = enabled; });
      res.json({ ok: true, encryptedDns: enabled });
    } else {
      res.json({ ok: false, error: "DNS switch failed — rolled back automatically" });
    }
  } catch (e) {
    console.error("[GhostPort] Error:", e.message);
    res.status(500).json({ ok: false, error: "Operation failed" });
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
    await withArsenal(arsenal => { arsenal.killSwitch = enabled; });
    res.json({ ok: true, killSwitch: enabled });
  } catch (e) {
    console.error("[GhostPort] Error:", e.message);
    res.status(500).json({ ok: false, error: "Operation failed" });
  }
});

/**
 * POST /api/arsenal/killswitch/auto — { enabled: true|false }
 * Toggles whether kill switch auto-trips on DNS leak detection
 */
app.post("/api/arsenal/killswitch/auto", async (req, res) => {
  const { enabled } = req.body;
  try {
    await withArsenal(arsenal => { arsenal.killSwitchAuto = enabled; });
    console.log(`[Arsenal] Kill switch auto-trip ${enabled ? "enabled" : "disabled"}`);
    res.json({ ok: true, killSwitchAuto: enabled });
  } catch (e) {
    console.error("[GhostPort] Error:", e.message);
    res.status(500).json({ ok: false, error: "Operation failed" });
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
      const tmpMac = gpTmpFile("gp-mac-random") + ".service";
      fs.writeFileSync(tmpMac, unit);
      await run(`sudo cp ${tmpMac} ${serviceFile}`);
      try { fs.unlinkSync(tmpMac); } catch {}
      await run("sudo systemctl daemon-reload && sudo systemctl enable gp-mac-random.service");
      await withArsenal(arsenal => { arsenal.macRandomization = true; });
      res.json({ ok: true, macRandomization: true, note: "MAC will change on next reboot. AP will briefly disconnect." });
    } else {
      await run("sudo systemctl disable gp-mac-random.service 2>/dev/null");
      await run(`sudo rm -f ${serviceFile}`);
      await run("sudo systemctl daemon-reload");
      await withArsenal(arsenal => { arsenal.macRandomization = false; });
      res.json({ ok: true, macRandomization: false });
    }
  } catch (e) {
    console.error("[GhostPort] Error:", e.message);
    res.status(500).json({ ok: false, error: "Operation failed" });
  }
});

/**
 * POST /api/arsenal/quicblock — { enabled: true|false }
 * Toggles QUIC (UDP 443) blocking in privacy modes
 */
app.post("/api/arsenal/quicblock", async (req, res) => {
  const { enabled } = req.body;
  try {
    await withArsenal(arsenal => { arsenal.quicBlock = enabled; });

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
    console.error("[GhostPort] Error:", e.message);
    res.status(500).json({ ok: false, error: "Operation failed" });
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
    const id = Date.now().toString(36) + crypto.randomBytes(2).toString("hex");
    const dayStr = days.join(",");
    const cronLine = `${minute} ${hour} * * ${dayStr} root /usr/local/bin/gp-mode ${mode} --no-rollback # gp-schedule-${id}`;

    // Ensure cron file exists
    await run("sudo touch /etc/cron.d/ghostport-schedules && sudo chmod 644 /etc/cron.d/ghostport-schedules");
    const tmpSched = gpTmpFile("gp-sched") + ".txt";
    fs.writeFileSync(tmpSched, cronLine + "\n");
    await run(`cat ${tmpSched} | sudo tee -a /etc/cron.d/ghostport-schedules > /dev/null`);
    try { fs.unlinkSync(tmpSched); } catch {}

    await withArsenal(arsenal => { arsenal.schedules.push({ id, time, days, mode }); });
    res.json({ ok: true, schedule: { id, time, days, mode } });
  } catch (e) {
    console.error("[GhostPort] Error:", e.message);
    res.status(500).json({ ok: false, error: "Operation failed" });
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
    await withArsenal(arsenal => { arsenal.schedules = (arsenal.schedules || []).filter(s => s.id !== id); });
    res.json({ ok: true });
  } catch (e) {
    console.error("[Arsenal] Schedule delete error:", e.message);
    res.status(500).json({ ok: false, error: "Failed to delete schedule" });
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
    console.error("[GhostPort] Error:", e.message);
    res.status(500).json({ ok: false, error: "Operation failed" });
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

  let reportFile;
  try {
    reportFile = gpTmpFile("gp-lynis-report") + ".dat";

    const result = await run(
      `sudo lynis audit system --quick --no-colors --no-log --report-file ${reportFile} 2>&1 | tail -5`,
      120000
    );

    // Parse report file
    let report;
    try { report = fs.readFileSync(reportFile, "utf8"); } catch {
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
    console.error("[GhostPort] Error:", e.message);
    res.status(500).json({ ok: false, error: "Operation failed" });
  } finally {
    lynisScanRunning = false;
    try { fs.unlinkSync(reportFile); } catch {}
  }
});


// ── Family Shield (parental controls) ─────────────────────
const FAMILY_SHIELD_FILE = "/etc/ghostport/family-shield.json";
const FAMILY_SHIELD_GROUP = "FamilyShield";

const FAMILY_SHIELD_LISTS = {
  adult:    ["https://blocklistproject.github.io/Lists/porn.txt"],
  gambling: ["https://blocklistproject.github.io/Lists/gambling.txt"],
  facebook: ["https://blocklistproject.github.io/Lists/facebook.txt"],
  tiktok:   ["https://blocklistproject.github.io/Lists/tiktok.txt"],
  twitter:  ["https://blocklistproject.github.io/Lists/twitter.txt"],
};

// IP ranges owned by each service's ASN (not shared CDN — safe to block)
const FAMILY_SHIELD_IP_RANGES = {
  tiktok: [
    // AS396986 ByteDance US + AS138699 ByteDance APAC
    "71.18.0.0/16", "130.44.212.0/22", "139.177.224.0/20",
    "147.160.176.0/21", "192.64.15.0/24", "199.103.24.0/23",
    "101.45.0.0/20", "101.45.192.0/22", "101.45.248.0/22",
    "180.240.234.0/23", "202.52.240.0/21", "103.136.220.0/22", "118.26.132.0/24",
  ],
  facebook: [
    // AS32934 Meta
    "31.13.24.0/21", "31.13.64.0/18", "45.64.40.0/22", "66.220.144.0/20",
    "69.63.176.0/20", "69.171.224.0/19", "74.119.76.0/22", "102.132.96.0/20",
    "129.134.0.0/17", "157.240.0.0/17", "163.70.128.0/17", "173.252.64.0/18",
    "179.60.192.0/22", "185.60.216.0/22", "185.89.216.0/22", "204.15.20.0/22",
  ],
  twitter: [
    // AS13414 X/Twitter
    "104.244.40.0/21", "185.45.5.0/24", "192.133.76.0/22",
    "199.16.156.0/22", "199.59.148.0/22", "209.237.192.0/19",
  ],
  // DNS-only blocking is sufficient for these
  adult: [],
  gambling: [],
};

// Manage nftables IP blocking for Family Shield categories
const FS_NFT_TABLE = "inet ghostport_fs";

function fsIpBlock(category, enabled) {
  const ranges = FAMILY_SHIELD_IP_RANGES[category] || [];
  if (ranges.length === 0) return; // DNS-only category

  const setName = `fs_${category}`;

  if (enabled) {
    // Ensure table and chain exist
    exec(`sudo nft add table ${FS_NFT_TABLE} 2>/dev/null; sudo nft add chain ${FS_NFT_TABLE} forward '{ type filter hook forward priority 0; policy accept; }' 2>/dev/null`, () => {});

    // Build nft commands: delete old set, create new, add elements, add rule
    const elements = ranges.join(", ");
    const cmds = [
      `sudo nft delete set ${FS_NFT_TABLE} ${setName} 2>/dev/null; true`,
      `sudo nft add set ${FS_NFT_TABLE} ${setName} '{ type ipv4_addr; flags interval; }'`,
      `sudo nft add element ${FS_NFT_TABLE} ${setName} '{ ${elements} }'`,
    ].join(" && ");

    exec(cmds, (err) => {
      if (err) {
        console.error(`[FamilyShield] IP block ${category} set error:`, err.message);
        return;
      }
      // Add forward drop rule if not exists
      exec(`sudo nft -a list chain ${FS_NFT_TABLE} forward 2>/dev/null | grep ${setName}`, (err2, stdout) => {
        if (!stdout || !stdout.trim()) {
          exec(`sudo nft add rule ${FS_NFT_TABLE} forward iifname \"wlan0\" ip daddr @${setName} drop comment \"${setName}\"`, (err3) => {
            if (err3) console.error(`[FamilyShield] IP block ${category} rule error:`, err3.message);
            else console.log(`[FamilyShield] IP block enabled: ${category} (${ranges.length} ranges)`);
          });
        } else {
          console.log(`[FamilyShield] IP block already active: ${category}`);
        }
      });
    });
  } else {
    // Remove rule and set
    exec(`sudo nft -a list chain ${FS_NFT_TABLE} forward 2>/dev/null | grep ${setName} | awk '{print $NF}'`, (err, stdout) => {
      const handle = (stdout || "").trim();
      if (handle) {
        exec(`sudo nft delete rule ${FS_NFT_TABLE} forward handle ${handle}`, () => {});
      }
      exec(`sudo nft delete set ${FS_NFT_TABLE} ${setName} 2>/dev/null`, () => {
        console.log(`[FamilyShield] IP block disabled: ${category}`);
      });
    });
  }
}

function readFamilyShieldConfig() {
  try { return JSON.parse(fs.readFileSync(FAMILY_SHIELD_FILE, "utf8")); }
  catch { return { categories: { adult: false, gambling: false, facebook: false, tiktok: false, twitter: false } }; }
}

function writeFamilyShieldConfig(config) {
  fs.writeFileSync(FAMILY_SHIELD_FILE, JSON.stringify(config, null, 2));
  try { fs.chmodSync(FAMILY_SHIELD_FILE, 0o600); } catch {}
}

// Restore IP blocks from saved config on startup
// (must be after FAMILY_SHIELD_IP_RANGES, FS_NFT_TABLE, fsIpBlock, readFamilyShieldConfig are defined)
try {
  const fsConfig = readFamilyShieldConfig();
  for (const [cat, enabled] of Object.entries(fsConfig.categories || {})) {
    if (enabled) fsIpBlock(cat, true);
  }
} catch(e) {
  console.error("[FamilyShield] Failed to restore IP blocks on startup:", e.message);
}

async function getFamilyShieldGroup() {
  const r = await piholeApi("GET", "/groups");
  if (r.status !== 200 || !r.data?.groups) return null;
  return r.data.groups.find(g => g.name === FAMILY_SHIELD_GROUP) || null;
}

async function ensureFamilyShieldGroup() {
  let group = await getFamilyShieldGroup();
  if (!group) {
    const r = await piholeApi("POST", "/groups", {
      name: FAMILY_SHIELD_GROUP,
      comment: "GhostPort Family Shield — parental controls",
      enabled: true,
    });
    if (r.status !== 201 && r.status !== 200) {
      console.error("[FamilyShield] Failed to create group:", r.data);
      return null;
    }
    group = await getFamilyShieldGroup();
  }
  return group;
}

/**
 * GET /api/family-shield
 * Returns Family Shield state: enabled, categories, devices
 */
app.get("/api/family-shield", async (req, res) => {
  try {
    const config = readFamilyShieldConfig();
    const group = await getFamilyShieldGroup();
    const enabled = group ? group.enabled : false;

    const clientsRes = await piholeApi("GET", "/clients");
    const clients = clientsRes.data?.clients || [];

    const suggestRes = await piholeApi("GET", "/clients/_suggestions");
    // Pi-hole v6 returns {clients: [{hwaddr, addresses: "ip1,...", names: "name"}, ...]}
    const suggestList = suggestRes.data?.clients || [];
    const suggestMap = {};
    for (const s of suggestList) {
      const addrs = (s.addresses || "").split(",");
      for (const addr of addrs) {
        const a = addr.trim();
        if (a) suggestMap[a] = { hwaddr: s.hwaddr || "", name: s.names || a };
      }
    }

    const groupId = group ? group.id : null;

    const devices = [];
    for (const client of clients) {
      const ip = client.client || "";
      if (!ip.startsWith("192.168.50.")) continue;
      const shielded = groupId !== null && Array.isArray(client.groups) && client.groups.includes(groupId);
      let mac = "";
      let name = ip;
      if (suggestMap[ip]) {
        mac = suggestMap[ip].hwaddr || "";
        name = suggestMap[ip].name || ip;
      }
      devices.push({ ip, mac, name, shielded });
    }

    for (const [ip, info] of Object.entries(suggestMap)) {
      if (!ip.startsWith("192.168.50.")) continue;
      if (devices.find(d => d.ip === ip)) continue;
      devices.push({
        ip,
        mac: info.hwaddr || "",
        name: info.name || ip,
        shielded: false,
      });
    }

    res.json({ ok: true, enabled, categories: config.categories, devices });
  } catch (e) {
    console.error("[FamilyShield] Status error:", e.message);
    res.status(500).json({ ok: false, error: "Failed to get Family Shield status" });
  }
});

/**
 * POST /api/family-shield/toggle
 * Body: { enabled: true/false }
 */
app.post("/api/family-shield/toggle", async (req, res) => {
  try {
    const { enabled } = req.body || {};
    if (typeof enabled !== "boolean") {
      return res.status(400).json({ ok: false, error: "enabled must be a boolean" });
    }

    if (enabled) {
      const group = await ensureFamilyShieldGroup();
      if (!group) return res.status(500).json({ ok: false, error: "Failed to create FamilyShield group" });
      if (!group.enabled) {
        await piholeApi("PUT", `/groups/${encodeURIComponent(FAMILY_SHIELD_GROUP)}`, { enabled: true });
      }
    } else {
      const group = await getFamilyShieldGroup();
      if (group) {
        await piholeApi("PUT", `/groups/${encodeURIComponent(FAMILY_SHIELD_GROUP)}`, { enabled: false });
      }
      // Remove all IP blocks when Family Shield is disabled
      const fsConfig = readFamilyShieldConfig();
      for (const cat of Object.keys(fsConfig.categories || {})) {
        fsIpBlock(cat, false);
      }
    }

    console.log(`[FamilyShield] ${enabled ? "Enabled" : "Disabled"}`);
    res.json({ ok: true, enabled });
  } catch (e) {
    console.error("[FamilyShield] Toggle error:", e.message);
    res.status(500).json({ ok: false, error: "Failed to toggle Family Shield" });
  }
});

/**
 * POST /api/family-shield/categories
 * Body: { adult: true, gambling: false, ... }
 */
app.post("/api/family-shield/categories", async (req, res) => {
  try {
    const categories = req.body || {};
    const validKeys = Object.keys(FAMILY_SHIELD_LISTS);
    for (const key of Object.keys(categories)) {
      if (!validKeys.includes(key)) {
        return res.status(400).json({ ok: false, error: "Unknown category: " + key.replace(/[^a-z_]/g, "") });
      }
      if (typeof categories[key] !== "boolean") {
        return res.status(400).json({ ok: false, error: `Category ${key} must be a boolean` });
      }
    }

    const group = await ensureFamilyShieldGroup();
    if (!group) return res.status(500).json({ ok: false, error: "Failed to create FamilyShield group" });
    const groupId = group.id;

    const listsRes = await piholeApi("GET", "/lists");
    const existingLists = listsRes.data?.lists || [];

    const allFamilyUrls = new Set();
    for (const urls of Object.values(FAMILY_SHIELD_LISTS)) {
      for (const url of urls) allFamilyUrls.add(url);
    }

    const config = readFamilyShieldConfig();
    for (const [category, enabled] of Object.entries(categories)) {
      const urls = FAMILY_SHIELD_LISTS[category] || [];
      config.categories[category] = enabled;

      for (const url of urls) {
        const existing = existingLists.find(l => l.address === url);

        if (enabled) {
          if (existing) {
            const groups = Array.isArray(existing.groups) ? [...existing.groups] : [0];
            if (!groups.includes(groupId)) groups.push(groupId);
            await piholeApi("PUT", `/lists/${encodeURIComponent(url)}?type=block`, { enabled: true, groups });
          } else {
            await piholeApi("POST", "/lists?type=block", {
              address: url,
              groups: [groupId],
              comment: `FamilyShield: ${category}`,
              enabled: true,
            });
          }
        } else {
          if (existing) {
            const groups = Array.isArray(existing.groups) ? existing.groups.filter(g => g !== groupId) : [];
            if (groups.length === 0) {
              await piholeApi("PUT", `/lists/${encodeURIComponent(url)}?type=block`, { enabled: false, groups: [0] });
            } else {
              await piholeApi("PUT", `/lists/${encodeURIComponent(url)}?type=block`, { groups });
            }
          }
        }
      }
    }

    writeFamilyShieldConfig(config);

    // Apply IP-based blocking for categories that need it (e.g., TikTok)
    for (const [category, enabled] of Object.entries(categories)) {
      fsIpBlock(category, enabled);
    }

    piholeApi("POST", "/action/gravity").then(() => {
      console.log("[FamilyShield] Gravity update complete");
    }).catch(e => {
      console.error("[FamilyShield] Gravity update failed:", e.message);
    });

    console.log("[FamilyShield] Categories updated:", JSON.stringify(config.categories));
    res.json({ ok: true, categories: config.categories });
  } catch (e) {
    console.error("[FamilyShield] Categories error:", e.message, e.stack);
    res.status(500).json({ ok: false, error: "Failed to update categories" });
  }
});

/**
 * POST /api/family-shield/devices
 * Body: { ip: "192.168.50.164", shielded: true/false }
 */
app.post("/api/family-shield/devices", async (req, res) => {
  try {
    const { ip, shielded } = req.body || {};
    if (!ip || typeof shielded !== "boolean") {
      return res.status(400).json({ ok: false, error: "ip (string) and shielded (boolean) required" });
    }
    if (!/^192\.168\.50\.\d{1,3}$/.test(ip)) {
      return res.status(400).json({ ok: false, error: "Invalid IP — must be on 192.168.50.x subnet" });
    }
    const lastOctet = parseInt(ip.split(".")[3], 10);
    if (lastOctet < 1 || lastOctet > 254) {
      return res.status(400).json({ ok: false, error: "Invalid IP — last octet must be 1-254" });
    }

    const group = await ensureFamilyShieldGroup();
    if (!group) return res.status(500).json({ ok: false, error: "Failed to create FamilyShield group" });
    const groupId = group.id;

    const clientsRes = await piholeApi("GET", "/clients");
    const clients = clientsRes.data?.clients || [];
    const existing = clients.find(c => c.client === ip);

    if (shielded) {
      const groups = [0, groupId];
      if (existing) {
        await piholeApi("PUT", `/clients/${encodeURIComponent(ip)}`, { groups });
      } else {
        const suggestRes = await piholeApi("GET", "/clients/_suggestions");
        const suggestList = suggestRes.data?.clients || [];
        let name = ip;
        for (const s of suggestList) {
          if ((s.addresses || "").split(",").map(a => a.trim()).includes(ip)) {
            name = s.names || ip;
            break;
          }
        }
        await piholeApi("POST", "/clients", {
          client: ip,
          groups,
          comment: `FamilyShield: ${name}`,
        });
      }
    } else {
      if (existing) {
        await piholeApi("PUT", `/clients/${encodeURIComponent(ip)}`, { groups: [0] });
      }
    }

    console.log(`[FamilyShield] Device ${ip} ${shielded ? "shielded" : "unshielded"}`);
    res.json({ ok: true, ip, shielded });
  } catch (e) {
    console.error("[FamilyShield] Device error:", e.message);
    res.status(500).json({ ok: false, error: "Failed to update device" });
  }
});

/**
 * GET /api/family-shield/discover
 * Returns devices Pi-hole has seen but aren't configured
 */
app.get("/api/family-shield/discover", async (req, res) => {
  try {
    const suggestRes = await piholeApi("GET", "/clients/_suggestions");
    // Pi-hole v6: {clients: [{hwaddr, addresses: "ip,...", names: "name"}, ...]}
    const suggestList = suggestRes.data?.clients || [];

    const clientsRes = await piholeApi("GET", "/clients");
    const configuredIps = new Set((clientsRes.data?.clients || []).map(c => c.client));

    const devices = [];
    for (const s of suggestList) {
      const addrs = (s.addresses || "").split(",");
      for (const addr of addrs) {
        const ip = addr.trim();
        if (!ip.startsWith("192.168.50.")) continue;
        if (configuredIps.has(ip)) continue;
        if (devices.find(d => d.ip === ip)) continue;
        devices.push({
          ip,
          mac: s.hwaddr || "",
          name: s.names || ip,
        });
      }
    }

    res.json({ ok: true, devices });
  } catch (e) {
    console.error("[FamilyShield] Discover error:", e.message);
    res.status(500).json({ ok: false, error: "Failed to discover devices" });
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

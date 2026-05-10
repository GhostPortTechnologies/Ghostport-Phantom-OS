/**
 * GhostPort — Command Deck API Server
 * Runs on the Pi, controls gp-mode, serves live status
 * Start with: sudo node ghostport-server.js
 */

const express = require("express");
const { exec, execSync } = require("child_process");
const path = require("path");
const crypto = require("crypto");
const https = require("https");
const fs = require("fs");
const http = require("http");
const { TOTP, Secret } = require("otpauth");
const QRCode = require("qrcode");

const app = express();
app.disable("x-powered-by");
app.set("trust proxy", false);
const PORT = 4200;

// ── Device Activity Log ─────────────────────────────────
const ACTIVITY_FILE = "/etc/phantom/activity.json";
const ACTIVITY_MAX = 200;
let activityLog = [];

function loadActivity() {
  try { activityLog = JSON.parse(fs.readFileSync(ACTIVITY_FILE, "utf8")); }
  catch { activityLog = []; }
}

function saveActivity() {
  try { fs.writeFileSync(ACTIVITY_FILE, JSON.stringify(activityLog), "utf8"); }
  catch (e) { console.error("[Activity] Save failed:", e.message); }
}

function logActivity(type, message, detail) {
  const entry = { ts: new Date().toISOString(), type, message };
  if (detail) entry.detail = detail;
  activityLog.unshift(entry);
  if (activityLog.length > ACTIVITY_MAX) activityLog.length = ACTIVITY_MAX;
  saveActivity();
}

loadActivity();

// ── Pi-hole API integration ─────────────────────────────
const PIHOLE_FILE = "/etc/phantom/pihole.json";
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

let piholeReauthPromise = null;
async function piholeApi(method, apiPath, body) {
  let res = await piholeRequest(method, apiPath, body);
  // Re-auth on 401 (mutex prevents stampeding herd)
  if (res.status === 401) {
    if (!piholeReauthPromise) {
      piholeReauthPromise = piholeAuth().finally(() => { piholeReauthPromise = null; });
    }
    if (await piholeReauthPromise) {
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
const TALLY_FILE = "/etc/phantom/ads-tally.json";

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

// ── Control tag sanitization (Claude Code source leak hardening) ──
// Strips XML control tags that could hijack Claude agent behavior if injected into data
const CONTROL_TAG_RE = /<\/?(?:system-reminder|command-name|command-message|antml:function_calls|antml:invoke|antml:parameter|tool_use_error|persisted-output|functions|function)\b[^>]*>/gi;
const INJECTION_PATTERNS = [
  /ignore\s+(?:all\s+)?previous\s+instructions/i,
  /you\s+are\s+now/i,
  /disregard\s+(?:all\s+)?prior/i,
  /override\s+instructions/i,
  /new\s+instructions:/i,
  /\bsystem:\s/i,
];

function sanitizeString(str) {
  if (typeof str !== "string") return str;
  const cleaned = str.replace(CONTROL_TAG_RE, "");
  // Log injection attempts (don't block — false positive risk)
  for (const pat of INJECTION_PATTERNS) {
    if (pat.test(str)) {
      console.warn(`[SECURITY] PROMPT_INJECTION_ATTEMPT detected in input: ${str.substring(0, 120)}`);
      logActivity("security", "Prompt injection pattern detected in request input");
      break;
    }
  }
  return cleaned;
}

function sanitizeObject(obj) {
  if (typeof obj === "string") return sanitizeString(obj);
  if (Array.isArray(obj)) return obj.map(sanitizeObject);
  if (obj && typeof obj === "object") {
    const out = {};
    for (const [k, v] of Object.entries(obj)) out[sanitizeString(k)] = sanitizeObject(v);
    return out;
  }
  return obj;
}

app.use((req, res, next) => {
  if (req.body && typeof req.body === "object") {
    req.body = sanitizeObject(req.body);
  }
  next();
});

// Security headers
app.use((req, res, next) => {
  res.setHeader("X-Content-Type-Options", "nosniff");
  res.setHeader("X-Frame-Options", "DENY");
  res.setHeader("Referrer-Policy", "no-referrer");
  res.setHeader("X-XSS-Protection", "0");
  res.setHeader("Permissions-Policy", "camera=(), microphone=(), geolocation=()");
  res.setHeader("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'");
  if (req.secure) res.setHeader("Strict-Transport-Security", "max-age=31536000; includeSubDomains");
  next();
});

// ── authentication ────────────────────────────────────────

const AUTH_FILE = "/etc/phantom/auth.json";
const SESSION_TTL = 24 * 60 * 60 * 1000;
const LOCKOUT_TIERS = [60000, 120000, 300000, 900000]; // 1m, 2m, 5m, 15m
const MAX_ATTEMPTS = 5;
const LOCKOUT_STATE_FILE = "/etc/phantom/lockout.json";
const sessions = new Map();
const failedAttempts = new Map();

// ── Local widget token (localhost-only auth bypass) ───────
const LOCAL_TOKEN_FILE = "/run/ghostport/widget-token";
let localWidgetToken = null;
function generateLocalToken() {
  try {
    localWidgetToken = crypto.randomBytes(32).toString("hex");
    fs.writeFileSync(LOCAL_TOKEN_FILE, localWidgetToken, { mode: 0o600 });
  } catch (e) { console.error("[Auth] Local widget token write failed:", e.message); }
}
generateLocalToken();

// Persist lockout state across restarts
function loadLockoutState() {
  try {
    const data = JSON.parse(fs.readFileSync(LOCKOUT_STATE_FILE, "utf8"));
    for (const [ip, record] of Object.entries(data)) {
      // Only restore if lockout hasn't expired
      const tier = Math.min(record.lockouts || 0, LOCKOUT_TIERS.length - 1);
      const lockoutMs = LOCKOUT_TIERS[tier];
      if (Date.now() - record.lastAttempt < lockoutMs + 60000) {
        failedAttempts.set(ip, record);
      }
    }
  } catch { /* no state file yet */ }
}
function saveLockoutState() {
  try {
    const obj = {};
    for (const [ip, record] of failedAttempts) obj[ip] = record;
    fs.writeFileSync(LOCKOUT_STATE_FILE, JSON.stringify(obj));
    fs.chmodSync(LOCKOUT_STATE_FILE, 0o600);
  } catch { /* best effort */ }
}
loadLockoutState();

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

function getLockoutMs(ip) {
  const record = failedAttempts.get(ip);
  if (!record) return 0;
  const tier = Math.min(record.lockouts || 0, LOCKOUT_TIERS.length - 1);
  return LOCKOUT_TIERS[tier];
}

function isLockedOut(ip) {
  const record = failedAttempts.get(ip);
  if (!record) return false;
  const lockoutMs = getLockoutMs(ip);
  if (record.count >= MAX_ATTEMPTS && (Date.now() - record.lastAttempt) < lockoutMs) return true;
  if ((Date.now() - record.lastAttempt) >= lockoutMs) {
    // Don't delete — preserve lockout tier for escalation
    if (record.count >= MAX_ATTEMPTS) {
      record.count = 0; // Reset attempt count but keep tier
    }
    return false;
  }
  return false;
}

function recordFailedAttempt(ip) {
  const record = failedAttempts.get(ip) || { count: 0, lastAttempt: 0, lockouts: 0 };
  record.count++;
  record.lastAttempt = Date.now();
  if (record.count >= MAX_ATTEMPTS) {
    record.lockouts = (record.lockouts || 0) + 1;
    saveLockoutState();
  }
  failedAttempts.set(ip, record);
}

function getLockoutMessage(ip) {
  const ms = getLockoutMs(ip);
  if (ms >= 60000) return `Too many attempts. Locked out for ${Math.ceil(ms / 60000)} minute${ms > 60000 ? "s" : ""}.`;
  return "Too many attempts. Try again in 60 seconds.";
}

// Generate passcode on first boot (provisioned=false until activation)
(function initAuth() {
  let auth = readAuth();
  if (auth && auth.hash && auth.salt) {
    console.log(`[Auth] Passcode configured (provisioned: ${auth.provisioned === true})`);
    return;
  }
  const passcode = generatePasscode();
  const salt = crypto.randomBytes(32).toString("hex");
  const hash = hashPasscode(passcode, salt);
  // No plaintext passcode stored — only hash and salt
  writeAuth({ hash, salt, provisioned: false });
  // Write passcode to a temp file readable only by root (auto-deleted after 5 min)
  try {
    fs.writeFileSync("/etc/phantom/initial-passcode.txt", passcode + "\n", { mode: 0o600 });
    exec("(sleep 300 && rm -f /etc/phantom/initial-passcode.txt) &");
  } catch {}
  console.log("");
  console.log("  ╔═══════════════════════════════════════════╗");
  console.log("  ║   DEVICE PASSCODE GENERATED               ║");
  console.log("  ║   View: sudo cat /etc/phantom/initial-passcode.txt  ║");
  console.log("  ╚═══════════════════════════════════════════╝");
  console.log("  Reset via SSH: sudo gp-passcode reset");
  console.log("");
})();

// Helper: check if device has been provisioned (activated by customer)
function isProvisioned() {
  const auth = readAuth();
  return auth && auth.provisioned === true;
}

// ── TOTP (Two-Factor Authentication) ─────────────────────
const TOTP_ISSUER = "GhostPort";
const TOTP_LABEL = "CommandDeck";
const BACKUP_CODE_COUNT = 8;
const pendingTotp = new Map(); // partial auth tokens awaiting TOTP verification

function createTOTPInstance(secret) {
  return new TOTP({ issuer: TOTP_ISSUER, label: TOTP_LABEL, algorithm: "SHA1", digits: 6, period: 30, secret });
}

function generateTOTPSecret() {
  return new Secret({ size: 20 });
}

const usedTOTPCodes = new Map(); // code -> expiry timestamp (replay prevention)
function validateTOTPCode(code, secret) {
  // Prevent replay attacks — reject codes already used in this window
  const codeKey = code + ":" + Math.floor(Date.now() / 30000);
  if (usedTOTPCodes.has(codeKey)) return false;
  const totp = createTOTPInstance(Secret.fromBase32(secret));
  // Allow 1 step window (90s total) for clock drift
  const delta = totp.validate({ token: code, window: 1 });
  if (delta !== null) {
    usedTOTPCodes.set(codeKey, Date.now() + 120000);
    // Prune expired entries
    for (const [k, exp] of usedTOTPCodes) { if (Date.now() > exp) usedTOTPCodes.delete(k); }
    return true;
  }
  return false;
}

function generateBackupCodes() {
  const codes = [];
  const chars = "ABCDEFGHJKMNPQRSTUVWXYZ23456789";
  for (let i = 0; i < BACKUP_CODE_COUNT; i++) {
    let code = "";
    for (let j = 0; j < 12; j++) code += chars[crypto.randomInt(chars.length)];
    codes.push({ code, used: false });
  }
  return codes;
}

function isTOTPConfigured() {
  const auth = readAuth();
  return auth && auth.totp && auth.totp.setupComplete === true && auth.totp.secret;
}

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
app.get("/offline.html", (req, res) => res.sendFile(path.join(__dirname, "public", "offline.html")));
app.get(/^\/login\/?$/, (req, res) => res.redirect("/login.html"));
app.get("/install.html", (req, res) => res.sendFile(path.join(__dirname, "public", "install.html")));
app.get(/^\/install\/?$/, (req, res) => res.redirect("/install.html"));
app.use("/qr", express.static(path.join(__dirname, "public", "qr")));

app.post("/api/auth/login", (req, res) => {
  const ip = req.ip;
  if (isLockedOut(ip)) {
    return res.status(429).json({ ok: false, error: getLockoutMessage(ip) });
  }

  const { passcode } = req.body;
  if (!passcode || typeof passcode !== "string" || passcode.length > 128) return res.status(400).json({ ok: false, error: "Passcode required" });

  const auth = readAuth();
  if (!auth || !auth.hash) return res.status(500).json({ ok: false, error: "Auth not configured" });

  const hash = hashPasscode(passcode.toUpperCase().trim(), auth.salt);
  if (!crypto.timingSafeEqual(Buffer.from(hash, "hex"), Buffer.from(auth.hash, "hex"))) {
    recordFailedAttempt(ip);
    const record = failedAttempts.get(ip);
    const remaining = MAX_ATTEMPTS - record.count;
    console.log(`[Auth] Failed login from ${ip} (${remaining} attempts remaining)`);
    logActivity("auth", "Failed login attempt", ip);
    if (remaining <= 0) {
      logActivity("security", "Account locked — too many attempts", ip);
      return res.status(429).json({ ok: false, error: getLockoutMessage(ip) });
    }
    return res.status(401).json({ ok: false, error: `Invalid passcode. ${remaining} attempt${remaining !== 1 ? "s" : ""} remaining.` });
  }

  // Successful login — reset attempt count but preserve escalation tier
  const lockRecord = failedAttempts.get(ip);
  if (lockRecord) { lockRecord.count = 0; }

  // If TOTP is configured, require second factor
  if (auth.totp && auth.totp.setupComplete && auth.totp.secret) {
    const partialToken = crypto.randomBytes(32).toString("hex");
    pendingTotp.set(partialToken, { ip, created: Date.now() });
    console.log(`[Auth] Passcode OK from ${ip} — awaiting TOTP`);
    return res.json({ ok: true, requireTotp: true, partialToken });
  }

  // No TOTP — complete login
  const token = crypto.randomBytes(32).toString("hex");
  const csrfToken = crypto.randomBytes(32).toString("hex");
  sessions.set(token, { created: Date.now(), ip, absoluteExpiry: Date.now() + 7 * 24 * 60 * 60 * 1000, csrf: csrfToken });
  console.log(`[Auth] Successful login from ${ip}`);
  logActivity("auth", "Login successful", ip);

  res.setHeader("Set-Cookie", `gp-session=${token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=86400${req.secure ? "; Secure" : ""}`);
  res.json({ ok: true, csrfToken });
});

// TOTP verification (second step of login)
app.post("/api/auth/totp/verify", (req, res) => {
  const ip = req.ip;
  if (isLockedOut(ip)) {
    return res.status(429).json({ ok: false, error: getLockoutMessage(ip) });
  }

  const { partialToken, code } = req.body;
  if (!partialToken || !code) return res.status(400).json({ ok: false, error: "Token and code required" });

  const pending = pendingTotp.get(partialToken);
  if (!pending || Date.now() - pending.created > 300000) {
    pendingTotp.delete(partialToken);
    return res.status(401).json({ ok: false, error: "Session expired. Please start over." });
  }

  // Verify TOTP completion from same IP as passcode entry
  if (pending.ip && pending.ip !== ip) {
    pendingTotp.delete(partialToken);
    return res.status(401).json({ ok: false, error: "Session expired. Please start over." });
  }

  const auth = readAuth();
  if (!auth || !auth.totp || !auth.totp.secret) {
    return res.status(500).json({ ok: false, error: "TOTP not configured" });
  }

  // Check TOTP code
  const codeStr = String(code).replace(/\s/g, "");
  let valid = false;

  if (/^\d{6}$/.test(codeStr)) {
    valid = validateTOTPCode(codeStr, auth.totp.secret);
  }

  // Check backup codes if TOTP didn't match (constant-time comparison)
  if (!valid && auth.totp.backupCodes) {
    const upper = codeStr.toUpperCase();
    const upperBuf = Buffer.from(upper);
    let matchIdx = -1;
    for (let i = 0; i < auth.totp.backupCodes.length; i++) {
      const bc = auth.totp.backupCodes[i];
      if (bc.used) continue;
      const codeBuf = Buffer.from(bc.code);
      if (upperBuf.length === codeBuf.length && crypto.timingSafeEqual(upperBuf, codeBuf)) {
        matchIdx = i;
      }
    }
    if (matchIdx >= 0) {
      auth.totp.backupCodes[matchIdx].used = true;
      writeAuth(auth);
      valid = true;
      console.log(`[Auth] Backup code used from ${ip} (${auth.totp.backupCodes.filter(bc => !bc.used).length} remaining)`);
    }
  }

  if (!valid) {
    recordFailedAttempt(ip);
    const record = failedAttempts.get(ip);
    const remaining = MAX_ATTEMPTS - record.count;
    if (remaining <= 0) {
      pendingTotp.delete(partialToken);
      return res.status(429).json({ ok: false, error: getLockoutMessage(ip) });
    }
    return res.status(401).json({ ok: false, error: `Invalid code. ${remaining} attempt${remaining !== 1 ? "s" : ""} remaining.` });
  }

  // TOTP valid — complete login
  pendingTotp.delete(partialToken);
  const lockRec = failedAttempts.get(ip);
  if (lockRec) { lockRec.count = 0; }
  const token = crypto.randomBytes(32).toString("hex");
  const csrfToken = crypto.randomBytes(32).toString("hex");
  sessions.set(token, { created: Date.now(), ip, absoluteExpiry: Date.now() + 7 * 24 * 60 * 60 * 1000, csrf: csrfToken });
  console.log(`[Auth] TOTP verified — login complete from ${ip}`);
  logActivity("auth", "Login successful (2FA)", ip);

  res.setHeader("Set-Cookie", `gp-session=${token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=86400${req.secure ? "; Secure" : ""}`);
  res.json({ ok: true, csrfToken });
});

// TOTP status (public — lets login page know which UI to show)
app.get("/api/auth/totp/status", (req, res) => {
  const auth = readAuth();
  res.json({
    ok: true,
    totpConfigured: !!(auth && auth.totp && auth.totp.setupComplete),
    provisioned: !!(auth && auth.provisioned)
  });
});

// Fleet TOTP reset request (public — consumer "forgot access?" flow)
let resetRequestAttempts = { count: 0, resetAt: 0 };
app.post("/api/auth/totp/reset-request", async (req, res) => {
  // Rate limit: 3 per hour
  const now = Date.now();
  if (now > resetRequestAttempts.resetAt) { resetRequestAttempts = { count: 0, resetAt: now + 3600000 }; }
  resetRequestAttempts.count++;
  if (resetRequestAttempts.count > 3) {
    return res.status(429).json({ ok: false, error: "Too many reset requests. Try again in an hour." });
  }

  const fleet = getFleetConfig();
  if (!fleet || !fleet.fleet_server || !fleet.device_id) {
    return res.status(503).json({ ok: false, error: "Device not registered with fleet. Contact support." });
  }

  try {
    const body = JSON.stringify({ device_id: fleet.device_id });
    const result = await new Promise((resolve, reject) => {
      const req = http.request(`${fleet.fleet_server}/fleet/devices/${encodeURIComponent(fleet.device_id)}/reset-totp`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(body),
          "Authorization": "Bearer " + fleet.fleet_token
        },
        timeout: 15000
      }, response => {
        let data = "";
        response.on("data", c => data += c);
        response.on("end", () => {
          try { resolve({ status: response.statusCode, body: JSON.parse(data) }); }
          catch { resolve({ status: response.statusCode, body: {} }); }
        });
      });
      req.on("error", reject);
      req.on("timeout", () => { req.destroy(); reject(new Error("timeout")); });
      req.write(body);
      req.end();
    });

    if (result.status === 200) {
      console.log("[Fleet] TOTP reset request submitted");
      res.json({ ok: true, message: "Reset request submitted. Your TOTP will be cleared within 60 seconds." });
    } else {
      console.error("[Fleet] Reset request failed:", result.status, result.body);
      res.status(result.status >= 400 ? result.status : 500).json({
        ok: false,
        error: result.body.error || "Reset request failed. Contact support."
      });
    }
  } catch (e) {
    console.error("[Fleet] Reset request error:", e.message);
    res.status(503).json({ ok: false, error: "Could not reach fleet server. Check your connection." });
  }
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
  // Use /run/ghostport/ for temp files — PrivateTmp=true makes /tmp invisible to sudo
  const dir = "/run/ghostport";
  try { fs.mkdirSync(dir, { recursive: true, mode: 0o700 }); } catch {}
  return `${dir}/${prefix}-${crypto.randomBytes(8).toString("hex")}`;
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

    // Check if device is already activated (provisioned flag, not hash existence)
    if (isProvisioned()) {
      return res.status(409).json({ ok: false, error: "Device already activated. Use the login page." });
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

    // Hash and save passcode (including plaintext for gp-passcode show)
    const salt = crypto.randomBytes(32).toString("hex");
    const hash = crypto.scryptSync(passcode, salt, 64).toString("hex");
    const authFile = "/etc/phantom/auth.json";
    fs.mkdirSync("/etc/phantom", { recursive: true });
    // Preserve existing TOTP/2FA config if user set it up before activation
    let existingAuth = {};
    try { existingAuth = JSON.parse(fs.readFileSync(authFile, "utf8")); } catch {}
    const newAuth = { hash, salt, provisioned: true };
    if (existingAuth.totp) newAuth.totp = existingAuth.totp;
    fs.writeFileSync(authFile, JSON.stringify(newAuth, null, 2));
    fs.chmodSync(authFile, 0o600);
    // Clear any existing sessions from before activation
    sessions.clear();

    // Auto-configure Pi-hole password
    const phChars = "ABCDEFGHJKMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789";
    let piholePw = "";
    for (let i = 0; i < 24; i++) piholePw += phChars[crypto.randomInt(phChars.length)];
    exec("sudo pihole setpassword " + JSON.stringify(piholePw), () => {});
    fs.writeFileSync("/etc/phantom/pihole.json", JSON.stringify({ password: piholePw }));
    fs.chmodSync("/etc/phantom/pihole.json", 0o600);

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
      // Use provisioning auth (baked during manufacturing) — fleet.json doesn't exist yet at activation time
      const fleetToken = (() => {
        // Try fleet-auth.json first (manufacturing provisioned), then fleet.json as fallback
        for (const f of ["/etc/phantom/fleet-auth.json", "/etc/phantom/fleet.json"]) {
          try { const t = JSON.parse(fs.readFileSync(f, "utf8")).fleet_token; if (t) return t; } catch {}
        }
        throw new Error("Fleet token not found — device needs fleet-auth.json from provisioning");
      })();
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
        fs.writeFileSync("/etc/phantom/fleet.json", JSON.stringify(fleetConf, null, 2));
        fs.chmodSync("/etc/phantom/fleet.json", 0o600);
        fleetStatus = "registered";
        console.log("[Fleet] Device registered:", regResult.device.id);
      } else {
        console.log("[Fleet] Registration response: status", regResult.status || "unknown");
        fleetStatus = "registration pending";
      }
    } catch (e) {
      console.error("[Fleet] Registration error:", e.message);
      fleetStatus = "offline — will retry";
    }

    // Auto-generate TOTP for new activations
    const totpSecret = generateTOTPSecret();
    const totpInstance = createTOTPInstance(totpSecret);
    const totpBackupCodes = generateBackupCodes();
    const authData = JSON.parse(fs.readFileSync(authFile, "utf8"));
    authData.totp = { secret: totpSecret.base32, setupComplete: false, backupCodes: totpBackupCodes };
    fs.writeFileSync(authFile, JSON.stringify(authData, null, 2));
    fs.chmodSync(authFile, 0o600);

    let totpQr = null;
    try {
      totpQr = await QRCode.toDataURL(totpInstance.toString(), { width: 300, margin: 2, errorCorrectionLevel: "H" });
    } catch {}

    console.log("[Setup] Device activated with passcode GP-****-****-****");

    logActivity("system", "Device activated via fleet", fleetStatus);

    res.json({
      ok: true,
      passcode,
      wifi_ssid: "GhostPort Router",
      wifi_password: wifiPw,
      fleet_status: fleetStatus,
      totp: {
        qrDataUri: totpQr,
        secret: totpSecret.base32,
        backupCodes: totpBackupCodes.map(bc => bc.code)
      }
    });
  } catch (e) {
    console.error("[Setup] Activation error:", e.message);
    res.status(500).json({ ok: false, error: "Activation failed" });
  }
});

// ── skip activation (use without subscription) ───────────
let skipActivationLast = 0;
app.post("/api/fleet/skip-activation", (req, res) => {
  if (Date.now() - skipActivationLast < 60000) return res.status(429).json({ ok: false, error: "Too many attempts" });
  skipActivationLast = Date.now();
  if (isProvisioned()) {
    return res.status(409).json({ ok: false, error: "Device already activated" });
  }
  const auth = readAuth();
  if (!auth) return res.status(500).json({ ok: false, error: "Auth not configured" });
  auth.provisioned = false; // keep as unprovisioned but mark as skipped
  auth.skippedActivation = true;
  writeAuth(auth);
  console.log("[Setup] Activation skipped — using without subscription");
  res.json({ ok: true });
});

// ── auto-redirect to install.html if not provisioned ──────
app.use((req, res, next) => {
  if (!isProvisioned() && req.path === "/" && req.method === "GET") {
    const auth = readAuth();
    if (auth && auth.skippedActivation) return next(); // skip was chosen — let them through
    return res.redirect("/install.html");
  }
  next();
});

// ── public read-only API routes (no auth, used by waybar/widgets) ────
const PUBLIC_API_ROUTES = ["/api/status", "/api/threat/summary", "/api/system/version"];

// ── session middleware (everything below requires auth) ────

app.use((req, res, next) => {
  // Allow public read-only endpoints through without auth
  if (req.method === "GET" && PUBLIC_API_ROUTES.includes(req.path)) {
    return next();
  }
  // Local widget token bypass (localhost only, no CSRF needed for bearer auth)
  if (localWidgetToken && (req.ip === "127.0.0.1" || req.ip === "::1" || req.ip === "::ffff:127.0.0.1")) {
    const authHeader = req.headers["authorization"];
    if (authHeader && authHeader === "Bearer " + localWidgetToken) {
      return next();
    }
  }
  const token = getCookie(req, "gp-session");
  if (token && sessions.has(token)) {
    const session = sessions.get(token);
    if (Date.now() - session.created < SESSION_TTL && (!session.absoluteExpiry || Date.now() < session.absoluteExpiry)) {
      session.lastActivity = Date.now(); // track activity for logging
      // CSRF: validate token on state-changing requests (defense-in-depth alongside SameSite=Strict)
      if (req.method !== "GET" && req.method !== "HEAD" && req.method !== "OPTIONS") {
        const csrfHeader = req.headers["x-csrf-token"];
        if (!csrfHeader || csrfHeader !== session.csrf) {
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

// ── TOTP setup endpoints (protected — requires session) ──

app.post("/api/auth/totp/setup", async (req, res) => {
  const auth = readAuth();
  if (!auth) return res.status(500).json({ ok: false, error: "Auth not configured" });
  if (auth.totp && auth.totp.setupComplete) {
    return res.status(409).json({ ok: false, error: "TOTP already configured. Disable it first." });
  }

  const secret = generateTOTPSecret();
  const totp = createTOTPInstance(secret);
  const otpauthUri = totp.toString();
  const backupCodes = generateBackupCodes();

  // Store secret with setupComplete=false until verified
  auth.totp = {
    secret: secret.base32,
    setupComplete: false,
    backupCodes
  };
  writeAuth(auth);

  try {
    const qrDataUri = await QRCode.toDataURL(otpauthUri, { width: 300, margin: 2, errorCorrectionLevel: "H" });
    res.json({
      ok: true,
      qrDataUri,
      secret: secret.base32,
      backupCodes: backupCodes.map(bc => bc.code),
      otpauthUri
    });
  } catch (e) {
    res.status(500).json({ ok: false, error: "Failed to generate QR code" });
  }
});

app.post("/api/auth/totp/confirm-setup", (req, res) => {
  const { code } = req.body;
  if (!code) return res.status(400).json({ ok: false, error: "Verification code required" });

  const auth = readAuth();
  if (!auth || !auth.totp || !auth.totp.secret) {
    return res.status(400).json({ ok: false, error: "No TOTP setup in progress" });
  }
  if (auth.totp.setupComplete) {
    return res.status(409).json({ ok: false, error: "TOTP already active" });
  }

  const codeStr = String(code).replace(/\s/g, "");
  if (!validateTOTPCode(codeStr, auth.totp.secret)) {
    return res.status(401).json({ ok: false, error: "Invalid code. Check your authenticator app and try again." });
  }

  auth.totp.setupComplete = true;
  auth.totp.setupAt = new Date().toISOString();
  writeAuth(auth);
  console.log("[Auth] TOTP setup confirmed");
  logActivity("security", "Two-factor authentication enabled");

  res.json({ ok: true });
});

app.post("/api/auth/totp/disable", (req, res) => {
  const { code } = req.body;
  if (!code) return res.status(400).json({ ok: false, error: "Current TOTP code required to disable" });

  const auth = readAuth();
  if (!auth || !auth.totp || !auth.totp.setupComplete) {
    return res.status(400).json({ ok: false, error: "TOTP is not configured" });
  }

  const codeStr = String(code).replace(/\s/g, "");
  if (!validateTOTPCode(codeStr, auth.totp.secret)) {
    return res.status(401).json({ ok: false, error: "Invalid TOTP code" });
  }

  delete auth.totp;
  writeAuth(auth);
  console.log("[Auth] TOTP disabled");
  logActivity("security", "Two-factor authentication disabled");

  res.json({ ok: true });
});

app.post("/api/auth/totp/resync", async (req, res) => {
  const auth = readAuth();
  if (!auth) return res.status(500).json({ ok: false, error: "Auth not configured" });

  // Require passcode re-verification for resync (prevents session-theft account takeover)
  const { passcode } = req.body;
  if (!passcode) return res.status(400).json({ ok: false, error: "Passcode required to resync 2FA" });
  const hash = hashPasscode(passcode.toUpperCase().trim(), auth.salt);
  if (!crypto.timingSafeEqual(Buffer.from(hash, "hex"), Buffer.from(auth.hash, "hex"))) {
    return res.status(401).json({ ok: false, error: "Invalid passcode" });
  }

  // Clear old TOTP and generate fresh secret
  const secret = generateTOTPSecret();
  const totp = createTOTPInstance(secret);
  const otpauthUri = totp.toString();
  const backupCodes = generateBackupCodes();

  auth.totp = {
    secret: secret.base32,
    setupComplete: false,
    backupCodes
  };
  writeAuth(auth);

  try {
    const qrDataUri = await QRCode.toDataURL(otpauthUri, { width: 300, margin: 2, errorCorrectionLevel: "H" });
    console.log("[Auth] TOTP resync — new secret generated");
    res.json({
      ok: true,
      qrDataUri,
      secret: secret.base32,
      backupCodes: backupCodes.map(bc => bc.code),
      otpauthUri
    });
  } catch (e) {
    res.status(500).json({ ok: false, error: "Failed to generate QR code" });
  }
});

app.get("/api/auth/totp/backup-codes", (req, res) => {
  const auth = readAuth();
  if (!auth || !auth.totp || !auth.totp.setupComplete) {
    return res.status(400).json({ ok: false, error: "TOTP is not configured" });
  }
  const remaining = (auth.totp.backupCodes || []).filter(bc => !bc.used).length;
  res.json({ ok: true, remaining, total: BACKUP_CODE_COUNT });
});

app.post("/api/auth/totp/regenerate-backup", (req, res) => {
  const { code } = req.body;
  if (!code) return res.status(400).json({ ok: false, error: "Current TOTP code required" });

  const auth = readAuth();
  if (!auth || !auth.totp || !auth.totp.setupComplete) {
    return res.status(400).json({ ok: false, error: "TOTP is not configured" });
  }

  const codeStr = String(code).replace(/\s/g, "");
  if (!validateTOTPCode(codeStr, auth.totp.secret)) {
    return res.status(401).json({ ok: false, error: "Invalid TOTP code" });
  }

  const newCodes = generateBackupCodes();
  auth.totp.backupCodes = newCodes;
  writeAuth(auth);
  console.log("[Auth] Backup codes regenerated");

  res.json({ ok: true, backupCodes: newCodes.map(bc => bc.code) });
});

app.use(express.static(path.join(__dirname, "public")));

// Prune expired sessions + pending TOTP tokens every 10 minutes
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
  // Prune expired pending TOTP tokens (5min TTL)
  for (const [tok, pending] of pendingTotp) {
    if (now - pending.created > 300000) pendingTotp.delete(tok);
  }
  // Prune expired failedAttempts entries (lockout expired + 15min grace)
  let prunedLockouts = 0;
  for (const [ip, record] of failedAttempts) {
    const tier = Math.min(record.lockouts || 0, LOCKOUT_TIERS.length - 1);
    const lockoutMs = LOCKOUT_TIERS[tier];
    if (now - record.lastAttempt > lockoutMs + 900000) {
      failedAttempts.delete(ip);
      prunedLockouts++;
    }
  }
  if (prunedLockouts > 0) {
    console.log(`[Auth] Pruned ${prunedLockouts} expired lockout(s)`);
    saveLockoutState();
  }
}, 600000);

// ── change passcode (protected) ───────────────────────────

app.post("/api/auth/change-passcode", (req, res) => {
  const { currentPasscode, newPasscode } = req.body;
  if (!currentPasscode || typeof currentPasscode !== "string" || currentPasscode.length > 128) return res.status(400).json({ ok: false, error: "Current passcode required" });
  if (newPasscode && (typeof newPasscode !== "string" || newPasscode.length > 128)) return res.status(400).json({ ok: false, error: "Passcode too long (max 128 characters)" });

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
  // No plaintext passcode stored — only hash and salt
  writeAuth({ hash, salt, provisioned: auth.provisioned === true, totp: auth.totp || undefined, skippedActivation: auth.skippedActivation || undefined });

  // Invalidate all other sessions and pending TOTP tokens
  const myToken = getCookie(req, "gp-session");
  for (const [tok] of sessions) {
    if (tok !== myToken) sessions.delete(tok);
  }
  pendingTotp.clear();

  const isGenerated = !newPasscode || newPasscode.trim().length < 6;
  console.log(`[Auth] Passcode changed from ${req.ip}`);
  logActivity("security", "Device passcode changed", req.ip);
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

// Atomic nftables ruleset loader. Single entry point for "replace a
// dedicated Phantom table from a multi-line nft script." Previously this
// Promise-wrapped spawn was duplicated at every call site; consolidated
// here so behaviour (timeout, stderr capture, idempotent delete) stays
// consistent.
//
//   opts.deleteTable: "ip phantom_scrub" | "inet phantom_brokers" | null
//     If given, `nft delete table <spec>` runs first (errors swallowed —
//     absent table is the normal idempotent case).
//
// Rejects with the nft stderr on non-zero exit so the caller can log or
// return a useful message to the user.
const { spawn } = require("child_process");
async function applyNftRuleset(ruleset, opts = {}) {
  const deleteTable = opts.deleteTable || null;
  if (deleteTable) {
    await run(`sudo nft delete table ${deleteTable} 2>/dev/null || true`);
  }
  await new Promise((resolve, reject) => {
    const child = spawn("sudo", ["nft", "-f", "-"]);
    let stderr = "";
    child.stderr.on("data", (d) => { stderr += d; });
    child.on("close", (code) => (code === 0 ? resolve() : reject(new Error(stderr.trim() || `nft load failed (exit ${code})`))));
    child.stdin.write(ruleset);
    child.stdin.end();
  });
}

// Shared ruleset for the TCP/IP fingerprint scrub Arsenal toggle. Defined
// once so the POST handler and the startup-restore block can't drift.
const TCP_SCRUB_RULESET = [
  "table ip phantom_scrub {",
  "  chain scrub {",
  "    type filter hook postrouting priority mangle; policy accept;",
  "    ip ttl set 64",
  "    tcp flags syn tcp option maxseg size set 1360",
  "  }",
  "}",
].join("\n") + "\n";
const TCP_SCRUB_TABLE = "ip phantom_scrub";

// Fast interface state check via sysfs (no process spawn)
function ifaceUp(name) {
  try {
    const state = fs.readFileSync(`/sys/class/net/${name}/operstate`, "utf8").trim();
    return { ok: true, out: (state === "up" || state === "unknown") ? "up" : "down" };
  } catch { return { ok: true, out: "down" }; }
}

// Fast file read (no process spawn)
function readFileOr(path, fallback) {
  try { return { ok: true, out: fs.readFileSync(path, "utf8").trim() }; }
  catch { return { ok: true, out: fallback }; }
}

// Canonical DHCP lease source. Always go through `sudo gp-leases` — it
// auto-discovers the current lease file (dnsmasq legacy vs. pihole-FTL) and
// falls back to ARP on wlan0 when the lease file goes stale. Never read
// /var/lib/misc/dnsmasq.leases or /etc/pihole/dhcp.leases directly; paths
// have moved silently before.
let _leaseCache = { ts: 0, out: "" };
async function getDhcpLeases() {
  const now = Date.now();
  if (now - _leaseCache.ts < 5000) return _leaseCache.out;
  const r = await run("sudo gp-leases 2>/dev/null", 5000);
  _leaseCache = { ts: now, out: r.ok ? r.out : "" };
  return _leaseCache.out;
}

function formatUptime(seconds) {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

// ── version tracking ─────────────────────────────────────
const VERSION_FILE = "/etc/phantom/version.json";

function readVersion() {
  try { return JSON.parse(fs.readFileSync(VERSION_FILE, "utf8")); }
  catch { return { version: "0.1.0", buildDate: null, updateAvailable: null }; }
}

// ── rollback state ────────────────────────────────────────

let rollbackTimer = null;
let rollbackTarget = null;
let rollbackDeadline = null;
// T-0067: arsenal.json contents at rollback-arm time. Restored on rollback
// fire so user can't strand a Kill Switch toggle in a mode where it no-ops.
let rollbackArsenalSnapshot = null;

function cancelRollback() {
  if (rollbackTimer) {
    clearTimeout(rollbackTimer);
    rollbackTimer = null;
    rollbackTarget = null;
    rollbackDeadline = null;
    rollbackArsenalSnapshot = null;
    // Also cancel the shell-level rollback
    run("sudo gp-mode confirm");
  }
}

function scheduleRollback(previousMode, timeoutSec = 60) {
  cancelRollback();
  rollbackTarget = previousMode;
  rollbackDeadline = Date.now() + timeoutSec * 1000;
  // T-0067: snapshot arsenal state so we can restore it if the rollback fires.
  // Read-only snapshot — no lock needed; writes are serialized by withArsenal().
  try {
    rollbackArsenalSnapshot = readArsenal();
  } catch (e) {
    rollbackArsenalSnapshot = null;
    console.warn("[GhostPort] rollback snapshot failed:", e.message);
  }

  rollbackTimer = setTimeout(async () => {
    console.log(`[GhostPort] Rollback timer expired — reverting to ${previousMode}`);
    // T-0067: restore arsenal.json BEFORE the mode revert so the new (old)
    // mode comes up with the correct arsenal state. Serialize through
    // withArsenal() to avoid clobbering an in-flight toggle.
    if (rollbackArsenalSnapshot) {
      try {
        await withArsenal((arsenal) => {
          for (const k of Object.keys(arsenal)) delete arsenal[k];
          Object.assign(arsenal, rollbackArsenalSnapshot);
        });
        console.log("[GhostPort] Arsenal state reverted to pre-switch snapshot");
      } catch (e) {
        console.error("[GhostPort] Arsenal restore failed:", e.message);
      }
    }
    await run(`sudo gp-mode ${previousMode} --no-rollback`);
    rollbackTimer = null;
    rollbackTarget = null;
    rollbackDeadline = null;
    rollbackArsenalSnapshot = null;
  }, timeoutSec * 1000);
}

// ── routes ─────────────────────────────────────────────────

/**
 * GET /api/activity
 * Returns device activity log (last 200 events)
 */
app.get("/api/activity", (req, res) => {
  const limit = Math.min(parseInt(req.query.limit) || 50, ACTIVITY_MAX);
  const type = req.query.type || null;
  let entries = type ? activityLog.filter(e => e.type === type) : activityLog;
  res.json({ ok: true, events: entries.slice(0, limit) });
});

// ── Cached public IP (refresh every 60s, not every 5s poll) ───
let cachedPublicIp = { ip: "unknown", ts: 0 };
async function getPublicIp() {
  if (Date.now() - cachedPublicIp.ts < 60000) return { ok: true, out: cachedPublicIp.ip };
  const result = await run("curl -s --max-time 5 https://icanhazip.com || echo unknown");
  cachedPublicIp = { ip: (result.out || "unknown").trim(), ts: Date.now() };
  return { ok: result.ok, out: cachedPublicIp.ip };
}

/**
 * GET /api/status
 * Returns live system status
 */
app.get("/api/status", async (req, res) => {
  try {
    // Use fs reads where possible to avoid spawning ~8 processes every 5s poll
    const wg = ifaceUp("wg0");
    const wg1 = ifaceUp("wg1");
    const ts = ifaceUp("tailscale0");
    const uptimeRaw = readFileOr("/proc/uptime", "0");
    const uptime = { ok: true, out: uptimeRaw.out.split(" ")[0] };
    const modeFile = readFileOr("/etc/phantom/current-mode", "isp");

    const [gpStatus, ip, pihole, wgHandshake] = await Promise.all([
      run("sudo gp-mode status"),
      getPublicIp(),
      piholeApi("GET", "/stats/summary").then(r => {
        const live = r.data?.queries?.blocked || 0;
        const tally = readTally();
        const delta = Math.max(0, live - tallyBaseline.blocked);
        return { out: String(live), allTime: String(tally.blocked + delta), ok: true };
      }).catch(() => ({ out: "0", allTime: "0", ok: false })),
      run("sudo wg show wg1 latest-handshakes 2>/dev/null | awk '{print $2}' | head -1"),
    ]);
    const activeMode = ["isp", "zerotrust", "doublehop", "zhop"].includes(modeFile.out.trim())
      ? modeFile.out.trim()
      : "isp";

    // Rollback info
    const rollbackInfo = {
      pending: rollbackTimer !== null,
      target: rollbackTarget,
      remainingSec: rollbackDeadline ? Math.max(0, Math.round((rollbackDeadline - Date.now()) / 1000)) : 0,
    };

    // ── Security Posture Score (0-100) ──
    const arsenal = readArsenal();
    const tsUp = ts.out.trim() === "up";
    const wg1Up = wg1.out.trim() === "up";
    const handshakeAge = (() => {
      const ts = parseInt(wgHandshake.out?.trim());
      return ts ? Math.floor(Date.now() / 1000) - ts : 9999;
    })();

    let score = 0;
    const scoreBreakdown = [];
    // Mode weight (base score)
    const modeScores = { isp: 20, zerotrust: 50, doublehop: 75, zhop: 100 };
    const modeBase = modeScores[activeMode] || 20;
    score += modeBase;
    scoreBreakdown.push({ name: "mode", value: modeBase, label: activeMode });
    // Tunnel-only features only earn points when the underlying defense is
    // actually active. The arsenal.* flags persist across mode switches as
    // user intent, so honoring them unconditionally inflates the score in
    // ISP/ZeroTrust where the watcher / nft rules / cover-traffic loop are
    // all no-ops. T-0075.
    const inTunnel = activeMode === "doublehop" || activeMode === "zhop";
    // Encrypted DNS — works in all modes (tunnel via wg1→EC2 unbound, non-tunnel via cloudflared DoH)
    if (arsenal.encryptedDns) { score += 10; scoreBreakdown.push({ name: "encryptedDns", value: 10 }); }
    // WireGuard wg1 handshake fresh (<3min)
    if (wg1Up && handshakeAge < 180) { score += 5; scoreBreakdown.push({ name: "wgHandshake", value: 5 }); }
    else if (wg1Up && handshakeAge >= 180) { score -= 10; scoreBreakdown.push({ name: "wgHandshake", value: -10, label: "stale" }); }
    // Tailscale connected
    if (tsUp) { score += 5; scoreBreakdown.push({ name: "tailscale", value: 5 }); }
    // Pi-hole — only credit when /stats/summary actually responded this poll
    if (pihole.ok) { score += 5; scoreBreakdown.push({ name: "pihole", value: 5 }); }
    else { scoreBreakdown.push({ name: "pihole", value: 0, label: "down" }); }
    // Kill switch — wg0-down watcher only runs in tunnel modes (see startKillSwitch)
    if (arsenal.killSwitch && inTunnel) { score += 5; scoreBreakdown.push({ name: "killSwitch", value: 5 }); }
    else if (arsenal.killSwitch) { scoreBreakdown.push({ name: "killSwitch", value: 0, label: `n/a in ${activeMode}` }); }
    // MAC randomization — wlan0 AP, mode-independent (applies on next reboot)
    if (arsenal.macRandomization) { score += 3; scoreBreakdown.push({ name: "macRandomization", value: 3 }); }
    // WebRTC blocked — STUN/TURN drop rules only exist in doublehop.nft / zhop.nft
    if (arsenal.webrtcBlock && inTunnel) { score += 3; scoreBreakdown.push({ name: "webrtcBlock", value: 3 }); }
    else if (arsenal.webrtcBlock) { scoreBreakdown.push({ name: "webrtcBlock", value: 0, label: `n/a in ${activeMode}` }); }
    // Anti-fingerprint DNS — Pi-hole reads /etc/dnsmasq.d/30-anti-fingerprint.conf in all modes
    if (arsenal.antiFingerprint) { score += 3; scoreBreakdown.push({ name: "antiFingerprint", value: 3 }); }
    // Cover traffic — gp-noise check_mode() no-ops outside doublehop/zhop
    if (arsenal.coverTraffic && inTunnel) { score += 2; scoreBreakdown.push({ name: "coverTraffic", value: 2 }); }
    else if (arsenal.coverTraffic) { scoreBreakdown.push({ name: "coverTraffic", value: 0, label: `n/a in ${activeMode}` }); }
    // Normalize to 0-100
    const securityScore = Math.max(0, Math.min(100, score));

    res.json({
      ok: true,
      activeMode,
      tunnels: {
        wg0: wg.out.trim() === "up" ? "up" : "down",
        wg1: wg1Up ? "up" : "down",
        tailscale: tsUp ? "up" : "down",
        pihole: "up",
      },
      ip: ip.out.trim() || "unknown",
      uptime: formatUptime(parseFloat(uptime.out) || 0),
      adsBlocked: parseInt(pihole.out.trim()) || 0,
      adsBlockedAllTime: parseInt(pihole.allTime?.trim()) || 0,
      rollback: rollbackInfo,
      securityScore,
      scoreBreakdown,
      version: readVersion(),
      wan: (() => {
        try {
          const w = JSON.parse(fs.readFileSync("/etc/phantom/wan.json", "utf8"));
          return { type: w.type || "ethernet", ssid: w.ssid || "" };
        } catch { return { type: "ethernet", ssid: "" }; }
      })(),
      raw: gpStatus.out,
    });
  } catch (e) {
    console.error("[GhostPort] Error:", e.message);
    res.status(500).json({ ok: false, error: "Operation failed" });
  }
});

/**
 * GET /api/system/version
 * Returns firmware version and update availability
 */
app.get("/api/system/version", (req, res) => {
  const ver = readVersion();
  res.json({ ok: true, ...ver });
});

/**
 * GET /api/system/health
 * Returns system health state from gp-health-guard
 */
app.get("/api/system/health", (req, res) => {
  try {
    const state = JSON.parse(fs.readFileSync("/etc/phantom/health-state.json", "utf8"));
    res.json({ ok: true, ...state });
  } catch {
    res.json({ ok: true, temp_c: null, mem_used_pct: null, disk_used_pct: null, conntrack_pct: null });
  }
});

/**
 * GET /api/system/updates
 * Returns auto-update state for all managed components
 */
app.get("/api/system/updates", (req, res) => {
  try {
    const state = JSON.parse(fs.readFileSync("/etc/phantom/auto-update-state.json", "utf8"));
    res.json({ ok: true, ...state });
  } catch {
    res.json({ ok: true, last_run: null, last_success: null, components: {}, errors: [] });
  }
});

/**
 * POST /api/system/updates/run
 * Triggers an immediate auto-update run
 */
app.post("/api/system/updates/run", async (req, res) => {
  try {
    const r = await run("sudo gp-auto-update run 2>&1 | tail -20");
    res.json({ ok: true, output: r.out || "" });
  } catch (e) {
    res.status(500).json({ ok: false, error: "Auto-update failed" });
  }
});

/**
 * GET /api/threat/summary
 * Aggregates threat intelligence: blocked DNS, firewall drops, auth failures
 */
let threatCache = { data: null, ts: 0 };
app.get("/api/threat/summary", async (req, res) => {
  try {
    // Cache for 60s (journalctl parsing is expensive)
    if (threatCache.data && Date.now() - threatCache.ts < 60000) {
      return res.json({ ok: true, ...threatCache.data, cached: true });
    }

    const [piholeStats, fwDrops, authFails, topBlocked] = await Promise.all([
      // Pi-hole blocked count (last 24h)
      piholeApi("GET", "/stats/summary").then(r => ({
        blocked: r.data?.queries?.blocked || 0,
        total: r.data?.queries?.total || 0,
        pct: r.data?.queries?.percent_blocked || 0,
      })).catch(() => ({ blocked: 0, total: 0, pct: 0 })),

      // Firewall drops in last 24h
      run("journalctl -k --since '24 hours ago' --no-pager 2>/dev/null | grep -c 'GhostPort-DROP' || echo 0"),

      // Auth failures in last 24h
      run("journalctl -u ghostport --since '24 hours ago' --no-pager 2>/dev/null | grep -ci 'invalid passcode\\|attempt.*failed\\|lockout' || echo 0"),

      // Top 5 blocked domains from Pi-hole
      piholeApi("GET", "/stats/top_domains?blocked=true&count=5").then(r => {
        const domains = r.data?.top_domains || r.data?.domains || {};
        return Object.entries(domains).slice(0, 5).map(([domain, count]) => ({ domain, count }));
      }).catch(() => []),
    ]);

    const summary = {
      dns: {
        blocked24h: piholeStats.blocked,
        total24h: piholeStats.total,
        blockedPct: piholeStats.pct,
        topBlocked,
      },
      firewall: {
        drops24h: parseInt(fwDrops.out?.trim()) || 0,
      },
      auth: {
        failures24h: parseInt(authFails.out?.trim()) || 0,
      },
      timestamp: new Date().toISOString(),
    };

    threatCache = { data: summary, ts: Date.now() };
    res.json({ ok: true, ...summary });
  } catch (e) {
    console.error("[Threat] Summary failed:", e.message);
    res.status(500).json({ ok: false, error: "Threat summary failed" });
  }
});

/**
 * GET /api/security/scan
 * Returns latest security scan results from gp-security-loop
 */
app.get("/api/security/scan", (req, res) => {
  try {
    const scan = JSON.parse(fs.readFileSync("/etc/phantom/security-scan.json", "utf8"));
    res.json({ ok: true, ...scan });
  } catch {
    res.json({ ok: true, timestamp: null, issues: -1, verdict: "NO_SCAN_YET" });
  }
});

/**
 * POST /api/security/scan/run
 * Triggers an immediate security scan
 */
app.post("/api/security/scan/run", async (req, res) => {
  try {
    const r = await run("sudo gp-security-loop 2>&1 | tail -20");
    const scan = JSON.parse(fs.readFileSync("/etc/phantom/security-scan.json", "utf8"));
    res.json({ ok: true, ...scan });
  } catch (e) {
    res.status(500).json({ ok: false, error: "Security scan failed" });
  }
});

/**
 * POST /api/mode
 * Body: { mode: "isp" | "zerotrust" | "doublehop" | "zhop" }
 * Switches the active gp-mode with automatic rollback safety
 */
let modeSwitchInProgress = false;
app.post("/api/mode", async (req, res) => {
  const { mode, skipRollback } = req.body;
  const valid = ["isp", "zerotrust", "doublehop", "zhop"];

  if (modeSwitchInProgress) {
    return res.status(409).json({ ok: false, error: "Mode switch already in progress" });
  }

  if (!valid.includes(mode)) {
    return res.status(400).json({ ok: false, error: `Invalid mode: ${mode}` });
  }

  // Get current mode before switching (fs read, no process spawn)
  const prevFile = readFileOr("/etc/phantom/current-mode", "isp");
  const previousMode = valid.includes(prevFile.out.trim()) ? prevFile.out.trim() : "isp";

  modeSwitchInProgress = true;
  console.log(`[GhostPort] Switching from ${previousMode} to ${mode}`);

  try {
    const result = await run(`sudo gp-mode ${mode}`);

    if (!result.ok) {
      console.error(
        `[GhostPort] Mode switch failed (ok=${result.ok}): ` +
        `stderr="${result.err || "<empty>"}" stdout="${result.out || "<empty>"}"`
      );
      return res.status(500).json({ ok: false, error: "Mode switch failed" });
    }

    // Schedule rollback (ISP is always safe, no rollback needed)
    const needsRollback = mode !== "isp" && !skipRollback;
    if (needsRollback) {
      scheduleRollback(previousMode, 60);
    } else if (skipRollback && mode !== "isp") {
      cancelRollback();
      await run("sudo gp-mode confirm 2>/dev/null || true");
    }

    // Remove QUIC block rules if toggle is off
    const arsenalAfter = readArsenal();
    if (arsenalAfter.quicBlock === false && mode !== "isp") {
      await run('sudo nft -a list chain inet filter forward 2>/dev/null | grep gp-quic-block | sed -n "s/.*handle \\([0-9]*\\)/\\1/p" | while read h; do sudo nft delete rule inet filter forward handle $h; done');
      console.log("[Arsenal] QUIC block disabled — removed rules after mode switch");
    }

    // Re-apply WebRTC block rules if toggle is ON (Path A: rules are no longer
    // hardcoded in mode profiles — toggle is authoritative). Tunnel modes only.
    const inTunnelNow = mode === "doublehop" || mode === "zhop";
    if (arsenalAfter.webrtcBlock === true && inTunnelNow) {
      // Idempotent: clear any existing then re-add. Quiet on already-absent.
      await run('sudo nft -a list chain inet filter forward 2>/dev/null | grep gp-webrtc-block | sed -n "s/.*handle \\([0-9]*\\)/\\1/p" | while read h; do sudo nft delete rule inet filter forward handle $h; done');
      await run('sudo nft add rule inet filter forward iifname "wlan0" udp dport \\{ 3478, 3479, 5349 \\} drop comment \\"gp-webrtc-block\\"');
      await run('sudo nft add rule inet filter forward iifname "wlan0" tcp dport \\{ 3478, 3479, 5349 \\} drop comment \\"gp-webrtc-block\\"');
      console.log("[Arsenal] WebRTC block restored after mode switch");
    }

    console.log(`[GhostPort] Mode switched: ${result.out}`);
    logActivity("mode", `Mode switched to ${mode.toUpperCase()}`, `from ${previousMode.toUpperCase()}`);
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
  } finally {
    modeSwitchInProgress = false;
  }
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

// ── Region (wg1 EC2 endpoint) toggle — backed by /usr/local/bin/gp-region ──
const REGION_STATE_FILE = "/etc/ghostport/active-region.json";
const REGION_VALID_ID = /^[a-z]{2}-[a-z]+-[0-9]+$/;
let regionSwitchInProgress = false;

app.get("/api/region", (req, res) => {
  try {
    const raw = fs.readFileSync(REGION_STATE_FILE, "utf8");
    const state = JSON.parse(raw);
    res.json({ ok: true, ...state });
  } catch (e) {
    res.json({ ok: false, error: "state-file-missing-or-invalid", id: null });
  }
});

app.get("/api/region/list", async (req, res) => {
  const result = await run("curl -s --max-time 5 http://10.66.66.1:8080/api/regions", 8000);
  if (!result.ok || !result.out) {
    return res.status(502).json({ ok: false, error: "fleet-api-unreachable" });
  }
  try {
    const data = JSON.parse(result.out);
    res.json({ ok: true, ...data });
  } catch (e) {
    res.status(502).json({ ok: false, error: "fleet-api-bad-json" });
  }
});

app.post("/api/region/switch", async (req, res) => {
  const { region } = req.body || {};
  if (!region || typeof region !== "string" || !REGION_VALID_ID.test(region)) {
    return res.status(400).json({ ok: false, error: "invalid-region-id" });
  }
  if (regionSwitchInProgress) {
    return res.status(409).json({ ok: false, error: "switch-already-in-progress" });
  }
  regionSwitchInProgress = true;
  try {
    const result = await run(`sudo gp-region switch ${region}`, 30000);
    res.json({ ok: result.ok, region, output: result.out, error: result.err || null });
  } finally {
    regionSwitchInProgress = false;
  }
});

app.post("/api/region/confirm", async (req, res) => {
  const result = await run("sudo gp-region confirm", 8000);
  res.json({ ok: result.ok, output: result.out, error: result.err || null });
});

app.post("/api/region/rollback", async (req, res) => {
  const result = await run("sudo gp-region rollback", 30000);
  res.json({ ok: result.ok, output: result.out, error: result.err || null });
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
    if (!confR.ok) { console.error("[Hostapd] sudo cat failed:", confR.err); throw new Error("Could not read hostapd config"); }
    const conf = confR.out;
    const ssid = conf.match(/^ssid=(.+)$/m)?.[1] || "";
    const pass = conf.match(/^wpa_passphrase=(.+)$/m)?.[1] || "";
    const keyMgmt = conf.match(/^wpa_key_mgmt=(.+)$/m)?.[1] || "WPA-PSK";
    let wpaMode = "wpa2";
    if (keyMgmt.includes("SAE") && keyMgmt.includes("WPA-PSK")) wpaMode = "wpa2wpa3";
    else if (keyMgmt.includes("SAE")) wpaMode = "wpa3";
    res.json({ ok: true, ssid, passphrase: pass, wpaMode });
  } catch (e) {
    res.status(500).json({ ok: false, error: "Could not read hostapd config" });
  }
});

app.post("/api/hostapd/config", async (req, res) => {
  const { ssid, passphrase, wpaMode } = req.body;
  if (!ssid && !passphrase && !wpaMode) {
    return res.status(400).json({ ok: false, error: "Provide ssid, passphrase, and/or wpaMode" });
  }
  if (wpaMode && !["wpa2", "wpa2wpa3", "wpa3"].includes(wpaMode)) {
    return res.status(400).json({ ok: false, error: "wpaMode must be wpa2, wpa2wpa3, or wpa3" });
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
    if (passphrase) {
      conf = conf.replace(/^wpa_passphrase=.+$/m, `wpa_passphrase=${passphrase}`);
      // Also update SAE password (WPA3) if present
      if (/^sae_password=.+$/m.test(conf)) {
        conf = conf.replace(/^sae_password=.+$/m, `sae_password=${passphrase}`);
      }
    }

    // WPA protocol mode switching
    if (wpaMode) {
      const currentPass = passphrase || conf.match(/^wpa_passphrase=(.+)$/m)?.[1] || "";
      if (wpaMode === "wpa2") {
        conf = conf.replace(/^wpa_key_mgmt=.+$/m, "wpa_key_mgmt=WPA-PSK");
        conf = conf.replace(/^ieee80211w=.+$/m, "ieee80211w=0");
        // Remove SAE lines if present
        conf = conf.replace(/^sae_password=.+\n?/m, "");
        conf = conf.replace(/^sae_require_mfp=.+\n?/m, "");
      } else if (wpaMode === "wpa2wpa3") {
        conf = conf.replace(/^wpa_key_mgmt=.+$/m, "wpa_key_mgmt=WPA-PSK SAE");
        conf = conf.replace(/^ieee80211w=.+$/m, "ieee80211w=1");
        if (!/^sae_password=/m.test(conf)) {
          conf += `\nsae_password=${currentPass}\nsae_require_mfp=1\n`;
        }
        if (!/^sae_require_mfp=/m.test(conf)) {
          conf += "sae_require_mfp=1\n";
        }
      } else if (wpaMode === "wpa3") {
        conf = conf.replace(/^wpa_key_mgmt=.+$/m, "wpa_key_mgmt=SAE");
        conf = conf.replace(/^ieee80211w=.+$/m, "ieee80211w=2");
        if (!/^sae_password=/m.test(conf)) {
          conf += `\nsae_password=${currentPass}\nsae_require_mfp=1\n`;
        }
        if (!/^sae_require_mfp=/m.test(conf)) {
          conf += "sae_require_mfp=1\n";
        }
      }
    }

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
  const modeFile = await run("cat /etc/phantom/current-mode 2>/dev/null || echo isp");
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
    await run("sudo gp-factory-reset");
    res.json({ ok: true, status: "Factory reset complete. Rebooting..." });
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
    const tmpFd = fs.openSync(tmpFile, "w", 0o600);
    fs.writeSync(tmpFd, sanitizedConfig);
    fs.closeSync(tmpFd);
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
      { name: "Gateway", cmd: "ip route | awk '/default via/{print $3}' | head -1" },
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
    const modeFile = await run("cat /etc/phantom/current-mode 2>/dev/null || echo isp");
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

// ── Enemy List (data-broker + surveillance-partner block counts) ─────
//
// Maps blocked Pi-hole queries to known broker / data-harvesting orgs.
// Broker list is editable at /etc/phantom/blocklists/data-brokers.json —
// ships with repo seed, hot-reloaded on file change.

const BROKER_MAP_PATH = "/etc/phantom/blocklists/data-brokers.json";
let _brokerMapCache = { mtime: 0, inverse: null, meta: null };

function loadBrokerMap() {
  try {
    const stat = fs.statSync(BROKER_MAP_PATH);
    if (stat.mtimeMs === _brokerMapCache.mtime && _brokerMapCache.inverse) {
      return _brokerMapCache;
    }
    const raw = JSON.parse(fs.readFileSync(BROKER_MAP_PATH, "utf8"));
    const inverse = new Map(); // domain → broker
    for (const [broker, domains] of Object.entries(raw.brokers || {})) {
      for (const d of domains) inverse.set(String(d).toLowerCase(), broker);
    }
    _brokerMapCache = {
      mtime: stat.mtimeMs,
      inverse,
      meta: { version: raw.version, updated: raw.updated, count: inverse.size },
    };
    return _brokerMapCache;
  } catch (e) {
    return { mtime: 0, inverse: new Map(), meta: { error: e.message } };
  }
}

function matchBroker(domain, inverse) {
  if (!domain || !inverse || !inverse.size) return null;
  const d = String(domain).toLowerCase();
  // Exact match first (fastest)
  if (inverse.has(d)) return inverse.get(d);
  // Suffix match: block `sub.acxiom.com` against `acxiom.com`
  const parts = d.split(".");
  for (let i = 1; i < parts.length; i++) {
    const suffix = parts.slice(i).join(".");
    if (inverse.has(suffix)) return inverse.get(suffix);
  }
  return null;
}

// Enemy List data source hierarchy:
//   1. /etc/phantom/broker-counters.json  — nftables-counter-based (primary)
//      Written every 30s by `gp-broker-counters read` via phantom-broker-counters.timer.
//      Counts egress packets/bytes to resolved broker IPs. Privacy-preserving — no
//      per-query or per-client data. Works regardless of Pi-hole privacy level.
//   2. Pi-hole `/stats/top_domains`       — fallback (DNS-layer)
//      Only populated if Pi-hole privacy level is 0–2. GhostPort ships at 3,
//      so this path typically returns empty. Kept as a fallback for operator
//      setups that lowered privacy level manually.

const BROKER_COUNTERS_FILE = "/etc/phantom/broker-counters.json";

function readBrokerCounters() {
  try {
    const raw = fs.readFileSync(BROKER_COUNTERS_FILE, "utf8");
    const parsed = JSON.parse(raw);
    // Ignore stale files (>5 min old) — timer is 30s, so >5 min means timer stopped.
    const ageSec = Math.floor(Date.now() / 1000) - (parsed.updated || 0);
    if (ageSec > 300) return null;
    return parsed;
  } catch {
    return null;
  }
}

app.get("/api/tools/enemies", async (req, res) => {
  try {
    const { inverse, meta } = loadBrokerMap();
    if (!inverse.size) {
      return res.json({ ok: false, error: "Broker list not loaded", meta });
    }

    // Primary source: nftables counters
    const counters = readBrokerCounters();
    if (counters && counters.brokers) {
      const brokers = counters.brokers
        .filter(b => b.packets > 0)
        .map(b => ({ name: b.name, packets: b.packets, bytes: b.bytes }))
        .sort((a, b) => b.packets - a.packets);
      const packetsTotal = brokers.reduce((s, b) => s + b.packets, 0);
      const bytesTotal   = brokers.reduce((s, b) => s + b.bytes,   0);
      return res.json({
        ok: true,
        source: "nftables",
        brokers,
        packetsTotal,
        bytesTotal,
        updated: counters.updated,
        listVersion: meta.version,
        listUpdated: meta.updated,
      });
    }

    // Fallback: Pi-hole top_domains (only works at privacy level ≤ 2)
    const N = 500;
    const [rBlocked, rAll] = await Promise.all([
      piholeApi("GET", `/stats/top_domains?blocked=true&count=${N}`),
      piholeApi("GET", `/stats/top_domains?blocked=false&count=${N}`),
    ]);
    if (rBlocked.status !== 200 || rAll.status !== 200) {
      return res.json({ ok: false, error: "Primary counter source unavailable (nftables service may be down) and Pi-hole fallback returned an error" });
    }
    const parseDomains = (r) => {
      const list = r.data?.domains || r.data?.top_domains || [];
      if (Array.isArray(list)) return list.map(x => [x.domain, x.count || 0]);
      return Object.entries(list);
    };
    const blocked   = new Map();
    const attempted = new Map();
    let blockedTotal = 0, attemptedTotal = 0;
    for (const [domain, count] of parseDomains(rBlocked)) {
      const broker = matchBroker(domain, inverse);
      if (!broker) continue;
      blocked.set(broker, (blocked.get(broker) || 0) + count);
      attempted.set(broker, (attempted.get(broker) || 0) + count);
      blockedTotal += count;
      attemptedTotal += count;
    }
    for (const [domain, count] of parseDomains(rAll)) {
      const broker = matchBroker(domain, inverse);
      if (!broker) continue;
      attempted.set(broker, (attempted.get(broker) || 0) + count);
      attemptedTotal += count;
    }
    const brokers = [...attempted.entries()]
      .map(([name, att]) => ({ name, attempted: att, blocked: blocked.get(name) || 0 }))
      .sort((a, b) => b.attempted - a.attempted);
    res.json({
      ok: true,
      source: "pihole-fallback",
      brokers,
      attemptedTotal,
      blockedTotal,
      listVersion: meta.version,
      listUpdated: meta.updated,
    });
  } catch (e) {
    console.error("[GhostPort] Enemy list error:", e.message);
    res.status(500).json({ ok: false, error: "Operation failed" });
  }
});

// ── bandwidth monitor ────────────────────────────────────

// DECISION: Use fs.readFileSync instead of shelling out — faster, no shell injection risk
const BANDWIDTH_IFACES = ["eth0", "wlan0", "wg0", "wg1", "tailscale0"];
const BANDWIDTH_STATS = ["rx_bytes", "tx_bytes", "rx_packets", "tx_packets", "rx_errors", "tx_errors", "rx_dropped", "tx_dropped"];

function readIfaceStat(iface, stat) {
  // Security: iface and stat are always from whitelists, never user input
  try {
    return parseInt(fs.readFileSync(`/sys/class/net/${iface}/statistics/${stat}`, "utf8").trim()) || 0;
  } catch { return null; }
}

function readIfaceStats(iface) {
  const result = {};
  for (const stat of BANDWIDTH_STATS) {
    const val = readIfaceStat(iface, stat);
    if (val === null) return null; // interface doesn't exist
    result[stat] = val;
  }
  return result;
}

function humanRate(bps) {
  const bits = bps * 8;
  if (bits >= 1000000) return `${(bits / 1000000).toFixed(1)} Mbps`;
  if (bits >= 1000) return `${(bits / 1000).toFixed(1)} Kbps`;
  return `${bits.toFixed(0)} bps`;
}

// T-0070: /api/tools/bandwidth (raw-counter endpoint) deleted — sole consumer was
// arsenal.js fetchBandwidth(), now migrated to /rate. Both UIs (TRAFFIC MONITOR in
// index.html inline block + Arsenal bandwidth card) share the rate endpoint. Lock
// below provides soft serialization; clients tolerate 429 and retry on next poll.

// 1-second delay done with setTimeout/Promise, not blocking the event loop
let bandwidthInProgress = false;
app.get("/api/tools/bandwidth/rate", async (req, res) => {
  if (bandwidthInProgress) return res.status(429).json({ ok: false, error: "Bandwidth test already running" });
  bandwidthInProgress = true;
  try {
    // Snapshot 1
    const snap1 = {};
    for (const iface of BANDWIDTH_IFACES) {
      const s = readIfaceStats(iface);
      if (s) snap1[iface] = s;
    }

    // Wait 1 second
    await new Promise(r => setTimeout(r, 1000));

    // Snapshot 2 + compute deltas
    const interfaces = {};
    let totalRx = 0, totalTx = 0;
    for (const iface of Object.keys(snap1)) {
      const s2 = readIfaceStats(iface);
      if (!s2) continue;
      const s1 = snap1[iface];
      const rxRate = Math.max(0, s2.rx_bytes - s1.rx_bytes);
      const txRate = Math.max(0, s2.tx_bytes - s1.tx_bytes);
      interfaces[iface] = {
        rx_bytes: s2.rx_bytes,
        tx_bytes: s2.tx_bytes,
        rx_packets: s2.rx_packets,
        tx_packets: s2.tx_packets,
        rx_errors: s2.rx_errors,
        tx_errors: s2.tx_errors,
        rx_dropped: s2.rx_dropped,
        tx_dropped: s2.tx_dropped,
        rx_rate_bps: rxRate,
        tx_rate_bps: txRate,
        rx_rate_human: humanRate(rxRate),
        tx_rate_human: humanRate(txRate),
      };
      totalRx += rxRate;
      totalTx += txRate;
    }

    res.json({
      ok: true,
      interfaces,
      total: {
        rx_rate_bps: totalRx,
        tx_rate_bps: totalTx,
        rx_rate_human: humanRate(totalRx),
        tx_rate_human: humanRate(totalTx),
      },
    });
  } catch (e) {
    console.error("[GhostPort] Error:", e.message);
    res.status(500).json({ ok: false, error: "Operation failed" });
  } finally {
    bandwidthInProgress = false;
  }
});

// ── TLS fingerprint (JA3) diagnostic ─────────────────────
// Restored 2026-05-01 — planned feature, not stub. Reports the Pi's own TLS
// fingerprint via ja3er.com. Per-device fingerprints would require server-side
// TLS interception or a downloadable browser test page (future work).
app.get("/api/tools/ja3check", async (req, res) => {
  try {
    const result = await run("curl -s --max-time 10 https://ja3er.com/json 2>/dev/null || echo '{}'");
    let ja3Data = {};
    try { ja3Data = JSON.parse(result.out.trim()); } catch { ja3Data = { error: "Could not reach ja3er.com" }; }

    res.json({
      ok: true,
      ja3_hash: ja3Data.ja3_hash || ja3Data.ja3 || null,
      ja3_text: ja3Data.ja3_text || null,
      user_agent: ja3Data.User_Agent || ja3Data.user_agent || null,
      source: "ja3er.com",
      note: "This is the Pi's own TLS fingerprint via curl. Connected devices will have different fingerprints based on their browser.",
    });
  } catch (e) {
    res.status(500).json({ ok: false, error: "JA3 check failed" });
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
      arsenal: "/etc/phantom/arsenal.json",
      currentMode: "/etc/phantom/current-mode",
      // wireguard: excluded — contains PrivateKey (security risk in backups)
      hostapd: "/etc/hostapd/hostapd.conf",
    };
    for (const [key, filePath] of Object.entries(files)) {
      try {
        const r = await run("sudo cat " + filePath + " 2>/dev/null");
        backup[key] = r.ok ? r.out.trim() : null;
      } catch { backup[key] = null; }
    }
    // Redact WiFi passphrase from hostapd config
    if (backup.hostapd) {
      backup.hostapd = backup.hostapd.replace(/^wpa_passphrase=.*$/m, 'wpa_passphrase=REDACTED');
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
      const tmpWgFd = fs.openSync(tmpWg, "w", 0o600);
      fs.writeSync(tmpWgFd, safeWgConfig);
      fs.closeSync(tmpWgFd);
      await run(`sudo cp ${tmpWg} /etc/wireguard/wg0.conf`);
      await run("sudo chmod 600 /etc/wireguard/wg0.conf");
      try { fs.unlinkSync(tmpWg); } catch {}
      // Don't use wg-quick service — gp-mode manages wg0 directly
      const currentMode = fs.readFileSync("/etc/phantom/current-mode", "utf8").trim();
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
    const modeFile = await run("cat /etc/phantom/current-mode 2>/dev/null || echo isp");
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

// ── WiFi sensing detection ────────────────────────────────

const ISP_SSID_PATTERNS = [
  { pattern: /^(cox|CoxWiFi)/i, isp: "Cox" },
  { pattern: /^(XFINITY|xfinitywifi|HOME-.*-(2\.4|5))/i, isp: "Comcast/Xfinity" },
  { pattern: /^(ATT|att-wifi|2WIRE)/i, isp: "AT&T" },
  { pattern: /^(SPECTRUM|SpectrumSetup|MySpectrum)/i, isp: "Spectrum" },
  { pattern: /^(Verizon|Fios-|VerizonFiOS)/i, isp: "Verizon" },
  { pattern: /^(CenturyLink)/i, isp: "CenturyLink" },
  { pattern: /^(FRONTIER)/i, isp: "Frontier" },
  { pattern: /^(T-Mobile|TMOBILE)/i, isp: "T-Mobile" },
  { pattern: /^(Optimum|OPTIMUM)/i, isp: "Optimum" },
  { pattern: /^(TWC|BrightHouse)/i, isp: "TWC/Bright House" },
];

const ISP_OUI_MAP = {
  "00:1a:77": "Arris (Cox/Spectrum)", "00:1d:cf": "Arris", "00:26:b8": "Arris",
  "f8:0b:be": "Arris", "20:3d:66": "Arris", "e8:65:d4": "Arris",
  "28:c6:8e": "Technicolor (ISP)", "34:8a:ae": "Technicolor", "50:39:55": "Technicolor",
  "7c:03:ab": "Technicolor", "00:1e:c1": "Pace/Arris (AT&T)", "2c:30:33": "Pace/Arris",
  "00:19:70": "Sagemcom (ISP)", "90:4d:4a": "Sagemcom",
  "a0:c5:62": "Hitron (Cox)", "cc:03:fa": "Hitron",
  "00:19:c0": "Ubee (ISP)", "64:68:0c": "Ubee",
  "44:e1:37": "Actiontec (Verizon)", "f8:e4:fb": "Actiontec",
  "b0:c7:45": "Xfinity Gateway", "84:a0:6e": "Xfinity Gateway",
  "58:d5:6e": "Cox Panoramic", "10:86:8c": "Cox Panoramic",
};

app.get("/api/wifi-sensing-check", async (req, res) => {
  try {
    let scanOutput = "";
    let scanSource = "none";

    // Try wlan1 (station interface) first — won't disrupt AP
    try {
      const r1 = await run("sudo iw dev wlan1 scan 2>/dev/null || true");
      if (r1.out && r1.out.includes("BSS ")) { scanOutput = r1.out; scanSource = "wlan1-scan"; }
    } catch {}

    // Fall back to wlan0 scan (works on some drivers even in AP mode)
    if (scanSource === "none") {
      try {
        const r2 = await run("sudo iw dev wlan0 scan 2>/dev/null || true", 30000);
        if (r2.out && r2.out.includes("BSS ")) { scanOutput = r2.out; scanSource = "wlan0-scan"; }
      } catch {}
    }

    // Last resort: cached scan dump
    if (scanSource === "none") {
      try {
        const r3 = await run("sudo iw dev wlan0 scan dump 2>/dev/null || true");
        if (r3.out && r3.out.includes("BSS ")) { scanOutput = r3.out; scanSource = "wlan0-dump"; }
      } catch {}
    }

    // Parse scan results
    const networks = [];
    if (scanOutput) {
      const blocks = scanOutput.split(/^BSS /m);
      for (const block of blocks) {
        if (!block.trim()) continue;
        const bssidMatch = block.match(/^([0-9a-f:]{17})/i);
        const ssidMatch = block.match(/SSID:\s*(.+)/);
        if (!bssidMatch) continue;
        const bssid = bssidMatch[1].toLowerCase();
        const ssid = ssidMatch ? ssidMatch[1].trim() : "";
        if (!ssid) continue;

        // Check SSID against ISP patterns
        for (const p of ISP_SSID_PATTERNS) {
          if (p.pattern.test(ssid)) {
            networks.push({ ssid, bssid, ispName: p.isp, matchType: "ssid" });
            break;
          }
        }

        // Check MAC OUI if no SSID match
        if (!networks.find(n => n.bssid === bssid)) {
          const oui = bssid.substring(0, 8);
          if (ISP_OUI_MAP[oui]) {
            networks.push({ ssid, bssid, ispName: ISP_OUI_MAP[oui], matchType: "mac-oui" });
          }
        }
      }
    }

    // Check default gateway MAC
    let gatewayInfo = null;
    try {
      const gwResult = await run("ip route | grep default | awk '{print $3}'");
      const gwIp = gwResult.out ? gwResult.out.trim() : "";
      if (gwIp && /^[0-9.]+$/.test(gwIp)) {
        const arpResult = await run("ip neigh show " + gwIp + " 2>/dev/null || true");
        const macMatch = (arpResult.out || "").match(/([0-9a-f:]{17})/i);
        if (macMatch) {
          const gwMac = macMatch[1].toLowerCase();
          const gwOui = gwMac.substring(0, 8);
          gatewayInfo = {
            ip: gwIp,
            mac: gwMac,
            vendor: ISP_OUI_MAP[gwOui] || null,
            ispLikely: !!ISP_OUI_MAP[gwOui]
          };
        }
      }
    } catch {}

    const ispDetected = networks.length > 0 || (gatewayInfo && gatewayInfo.ispLikely);
    let recommendation = "No ISP WiFi networks detected. Your home is clear of WiFi sensing risk.";
    if (ispDetected) {
      recommendation = "ISP WiFi detected. Disable your ISP router's WiFi or replace it with a standalone modem. Visit /modem-guide.html for instructions.";
    }

    res.json({
      ispWifiDetected: ispDetected,
      detectedNetworks: networks,
      gatewayInfo,
      scanSource,
      recommendation
    });
  } catch (e) {
    res.status(500).json({ ok: false, error: "WiFi scan failed" });
  }
});

// ── trouble tickets ───────────────────────────────────────

app.post("/api/ticket", async (req, res) => {
  const { description, contact } = req.body || {};
  if (!description || description.trim().length < 10 || description.length > 5000) {
    return res.status(400).json({ ok: false, error: "Description must be 10-5000 characters" });
  }

  let config;
  try {
    config = JSON.parse(fs.readFileSync("/etc/phantom/support.json", "utf8"));
  } catch {
    return res.status(500).json({ ok: false, error: "Support config not found" });
  }

  if (!config.webhookUrl) {
    return res.status(400).json({ ok: false, error: "Webhook not configured — ask your admin to set webhookUrl in /etc/phantom/support.json" });
  }

  // Gather system snapshot
  const [modeSnap, wgStatus, tsStatus, ipResult, uptimeResult] = await Promise.all([
    run("cat /etc/phantom/current-mode 2>/dev/null || echo isp"),
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

const ARSENAL_FILE = "/etc/phantom/arsenal.json";

function readArsenal() {
  try { return JSON.parse(fs.readFileSync(ARSENAL_FILE, "utf8")); }
  catch { return { killSwitch: false, encryptedDns: false, macRandomization: false, blocklistFreq: "weekly", schedules: [], tcpScrub: false, ghostMode: false }; }
}

function writeArsenal(data) {
  fs.writeFileSync(ARSENAL_FILE, JSON.stringify(data, null, 2));
}

// Mutex to prevent concurrent read-modify-write races on arsenal.json
let arsenalLock = Promise.resolve();
function withArsenal(fn) {
  const op = arsenalLock.then(async () => {
    const arsenal = readArsenal();
    const result = await fn(arsenal);
    writeArsenal(arsenal);
    return result;
  });
  // Chain recovery: next withArsenal call should proceed even if this one failed
  arsenalLock = op.catch(() => {});
  return op;
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
    const modeFile = await run("cat /etc/phantom/current-mode 2>/dev/null || echo isp");
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
    run("cat /etc/phantom/current-mode 2>/dev/null || echo isp").then(async (modeFile) => {
      const mode = modeFile.out.trim();
      if (!["isp", "zerotrust", "doublehop", "zhop"].includes(mode)) return;
      await run(`sudo gp-mode ${mode} --no-rollback`);
    }).catch(e => console.error("[Arsenal] Kill switch restore failed:", e.message));
  } else {
    console.log("[Arsenal] Kill switch disabled");
  }
}

// DNS leak monitor — runs every 30s, checks if DNS resolver matches ISP
async function checkDnsLeak() {
  try {
    const modeFile = await run("cat /etc/phantom/current-mode 2>/dev/null || echo isp");
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
  // Restore TCP fingerprint scrub if previously enabled
  if (arsenal.tcpScrub) {
    try {
      await applyNftRuleset(TCP_SCRUB_RULESET, { deleteTable: TCP_SCRUB_TABLE });
      console.log("[Arsenal] TCP scrub restored on startup");
    } catch (e) {
      console.error("[Arsenal] TCP scrub restore failed:", e.message);
    }
  }
  // Restore WebRTC block if previously enabled (Path A: not in mode profiles).
  if (arsenal.webrtcBlock === true) {
    try {
      const modeNow = readFileOr("/etc/phantom/current-mode", "isp").out.trim();
      const inTunnelNow = modeNow === "doublehop" || modeNow === "zhop";
      if (inTunnelNow) {
        await run('sudo nft -a list chain inet filter forward 2>/dev/null | grep gp-webrtc-block | sed -n "s/.*handle \\([0-9]*\\)/\\1/p" | while read h; do sudo nft delete rule inet filter forward handle $h; done');
        await run('sudo nft add rule inet filter forward iifname "wlan0" udp dport \\{ 3478, 3479, 5349 \\} drop comment \\"gp-webrtc-block\\"');
        await run('sudo nft add rule inet filter forward iifname "wlan0" tcp dport \\{ 3478, 3479, 5349 \\} drop comment \\"gp-webrtc-block\\"');
        console.log("[Arsenal] WebRTC block restored on startup (tunnel mode)");
      }
    } catch (e) {
      console.error("[Arsenal] WebRTC block restore failed:", e.message);
    }
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
 * GET /api/arsenal/status
 */
// Dreadnought readiness changes rarely (install/uninstall only). Cache 60s
// so /api/arsenal/status (polled every 10s) doesn't spawn a subprocess chain
// 6x per minute forever.
let _dreadnoughtReadyCache = { ts: 0, ready: false };
async function getDreadnoughtReady() {
  const now = Date.now();
  if (now - _dreadnoughtReadyCache.ts < 60000) return _dreadnoughtReadyCache.ready;
  const r = await run("sudo gp-dreadnought-install --status", 5000);
  const ready = r.out.trim() === "ready";
  _dreadnoughtReadyCache = { ts: now, ready };
  return ready;
}

app.get("/api/arsenal/status", async (req, res) => {
  try {
    const arsenal = readArsenal();
    const [wg, dnsIntent, leaseOut, macService, dreadnoughtReady, modeFile] = await Promise.all([
      run("ip link show wg0 2>/dev/null | grep -q 'state UNKNOWN\\|state UP' && echo up || echo down"),
      run("sudo gp-dns-switch status"),
      getDhcpLeases(),
      run("systemctl is-enabled gp-mac-random.service 2>/dev/null"),
      getDreadnoughtReady(),
      Promise.resolve(readFileOr("/etc/phantom/current-mode", "isp")),
    ]);

    // Compute the actual DNS path the system is serving, not just the user's intent.
    // In tunnel modes (doublehop/zhop) DNS goes through WireGuard to the EC2 unbound —
    // encrypted even if gp-dns-switch intent is "off". Showing CLEARTEXT there was a lie.
    const intent = dnsIntent.out.trim();          // on | off | dreadnought
    const activeMode = modeFile.out.trim();
    const inTunnel = activeMode === "doublehop" || activeMode === "zhop";
    let dnsStatus;
    if (inTunnel)                     dnsStatus = "tunnel";          // encrypted via WG, EC2 unbound recursive
    else if (intent === "dreadnought") dnsStatus = "dreadnought";    // local unbound, plaintext to roots
    else if (intent === "off")         dnsStatus = "plaintext";      // 1.1.1.1 / 8.8.8.8 direct
    else                               dnsStatus = "doh";            // cloudflared DoH (default)

    res.json({
      ok: true,
      mode: activeMode,                         // expose current mode for UI gating (used by Ghost Mode + Kill Switch + QUIC Block)
      killSwitch: arsenal.killSwitch,
      killSwitchTripped,
      killSwitchAuto: arsenal.killSwitchAuto !== false, // default true
      killSwitchApplicable: inTunnel,           // monitor only runs in tunnel modes (doublehop/zhop)
      dnsLeakDetected,
      encryptedDns: (dnsStatus === "doh" || dnsStatus === "tunnel"),
      dnsStatus,
      dnsIntent: intent,
      dreadnought: dnsStatus === "dreadnought",
      dreadnoughtReady,
      dreadnoughtApplicable: !inTunnel,          // UI hides control in tunnel modes
      macRandomization: macService.out.trim() === "enabled",
      quicBlock: arsenal.quicBlock !== false, // default true
      // QUIC block rule is hardcoded in zerotrust/doublehop/zhop .nft profiles, so the
      // user-facing toggle is only authoritative in ISP mode. Other modes force-enable.
      quicBlockManaged: activeMode !== "isp",
      tcpScrub:  !!arsenal.tcpScrub,          // default false
      // Anti-fingerprint DNS blocklist toggle. Backend renames the
      // /etc/dnsmasq.d/30-anti-fingerprint.conf file to .disabled when off.
      antiFingerprint: arsenal.antiFingerprint !== false,  // default ON
      // WebRTC leak block (Path A 2026-05-02): toggle is authoritative,
      // rules added at runtime. Tunnel modes only.
      webrtcBlock: !!arsenal.webrtcBlock,                  // default OFF
      webrtcBlockApplicable: inTunnel,                     // tunnel modes only
      ghostMode: !!arsenal.ghostMode,         // default false
      ghostReady: (() => {
        try {
          const cfg = JSON.parse(fs.readFileSync("/etc/phantom/ghost-endpoints.json", "utf8"));
          return (cfg.endpoints || []).length >= 2;
        } catch { return false; }
      })(),
      piholeConnected: piholeSid !== null,
      blocklistFreq: arsenal.blocklistFreq,
      schedules: arsenal.schedules || [],
      wg0: wg.out.trim(),
      clientCount: leaseOut ? leaseOut.split("\n").filter(Boolean).length : 0,
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
    const leaseOut = await getDhcpLeases();
    const clients = leaseOut.split("\n").filter(Boolean).filter(line => line.split(/\s+/).length >= 4).map(line => {
      const parts = line.split(/\s+/);
      const mac = parts[1];
      // Detect MAC randomization: locally administered bit (2nd hex char is 2,6,A,E)
      const secondHex = mac.charAt(1).toUpperCase();
      const macRandomized = ["2","6","A","E"].includes(secondHex);
      return {
        ip: parts[2],
        mac,
        hostname: (!parts[3] || parts[3] === "*") ? "Unknown" : parts[3],
        expiry: parts[0] === "0" ? "static" : new Date(parseInt(parts[0]) * 1000).toLocaleTimeString(),
        macRandomized,
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
      run("cat /etc/phantom/current-mode 2>/dev/null || echo isp"),
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
 * GET /api/arsenal/ghostmode/status
 * Returns Ghost Mode config — endpoint count, current index, last rotation.
 * Used by UI to decide between [ENABLE], [CONFIGURE], [INSTALL] states.
 */
app.get("/api/arsenal/ghostmode/status", async (req, res) => {
  try {
    const result = await run("sudo gp-ghost-rotate status", 5000);
    try {
      const parsed = JSON.parse(result.out);
      const arsenal = readArsenal();
      res.json({ ok: true, enabled: !!arsenal.ghostMode, ...parsed });
    } catch {
      res.json({ ok: false, error: "ghost-rotate status parse failed" });
    }
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message.slice(0, 300) });
  }
});

/**
 * POST /api/arsenal/ghostmode — { enabled: true|false }
 * Enabling: turns on phantom-ghost-rotate.timer (rotates wg1 endpoint every 4h).
 * Requires ≥2 endpoints in /etc/phantom/ghost-endpoints.json. If fewer,
 * returns 400 with a clear message so the UI can prompt the operator.
 * Sticky: state persists in arsenal.json. Timer runs regardless of mode;
 * the rotator itself no-ops in non-tunnel modes.
 */
app.post("/api/arsenal/ghostmode", async (req, res) => {
  const { enabled } = req.body;
  try {
    if (enabled) {
      // Gate on endpoint count so we don't enable a no-op timer.
      const status = await run("sudo gp-ghost-rotate status", 5000);
      try {
        const parsed = JSON.parse(status.out);
        if (!parsed.ready) {
          return res.status(400).json({
            ok: false,
            error: `Ghost Mode needs ≥2 endpoints (${parsed.configured_endpoints} configured). See /etc/phantom/ghost-endpoints.json`,
            status: parsed,
          });
        }
      } catch {
        return res.status(500).json({ ok: false, error: "ghost-rotate status parse failed" });
      }
      await run("sudo systemctl enable --now phantom-ghost-rotate.timer", 10000);
    } else {
      await run("sudo systemctl disable --now phantom-ghost-rotate.timer 2>/dev/null || true", 10000);
    }
    await withArsenal(arsenal => { arsenal.ghostMode = !!enabled; });
    console.log(`[Arsenal] Ghost Mode ${enabled ? "enabled" : "disabled"}`);
    res.json({ ok: true, ghostMode: !!enabled });
  } catch (e) {
    console.error("[GhostPort] Ghost Mode error:", e.message);
    res.status(500).json({ ok: false, error: e.message.slice(0, 500) });
  }
});

/**
 * POST /api/arsenal/tcpscrub — { enabled: true|false }
 * TCP/IP fingerprint scrubbing — normalizes packet fields that would otherwise
 * leak OS/device identity to passive observers.
 *   - TTL set to 64 (the Linux default, masks Windows/iOS/Android signatures)
 *   - TCP MSS clamped to 1360 on SYN (masks per-device max-segment variance)
 * Runs as a standalone nftables table (ip phantom_scrub, postrouting/mangle)
 * so it is independent of mode switches.
 */
app.post("/api/arsenal/tcpscrub", async (req, res) => {
  const { enabled } = req.body;
  try {
    await withArsenal(arsenal => { arsenal.tcpScrub = !!enabled; });
    if (enabled) {
      await applyNftRuleset(TCP_SCRUB_RULESET, { deleteTable: TCP_SCRUB_TABLE });
    } else {
      await run(`sudo nft delete table ${TCP_SCRUB_TABLE} 2>/dev/null || true`);
    }
    console.log(`[Arsenal] TCP scrub ${enabled ? "enabled" : "disabled"}`);
    res.json({ ok: true, tcpScrub: !!enabled });
  } catch (e) {
    console.error("[GhostPort] TCP scrub error:", e.message);
    res.status(500).json({ ok: false, error: e.message.slice(0, 500) });
  }
});

/**
 * POST /api/arsenal/dreadnought — { enabled: true|false }
 * Enabling: sets Pi-hole upstream to local unbound recursive-to-roots.
 *   Requires gp-dreadnought-install to have completed first (unbound must be active).
 * Disabling: reverts to encrypted DoH via cloudflared.
 * Sticky: state persists in arsenal.json across mode switches (gp-dns-upstream honors it).
 */
app.post("/api/arsenal/dreadnought", async (req, res) => {
  const { enabled } = req.body;
  try {
    const action = enabled ? "dreadnought" : "on";
    const result = await run(`sudo gp-dns-switch ${action}`, 20000);
    if (result.out.trim() === "ok") {
      res.json({ ok: true, dreadnought: !!enabled });
    } else {
      const msg = result.err || result.out || "DNS switch failed";
      res.json({ ok: false, error: msg.includes("unbound not running") ? "Dreadnought not installed — click Install first." : "DNS switch failed — rolled back automatically" });
    }
  } catch (e) {
    console.error("[GhostPort] Error:", e.message);
    res.status(500).json({ ok: false, error: "Operation failed" });
  }
});

/**
 * POST /api/arsenal/dreadnought/install — no body
 * Installs + configures unbound for recursive-to-roots DNS. Idempotent.
 * Does not flip Dreadnought on — caller must POST /api/arsenal/dreadnought { enabled: true } afterwards.
 */
app.post("/api/arsenal/dreadnought/install", async (req, res) => {
  try {
    const result = await run("sudo gp-dreadnought-install", 120000);
    _dreadnoughtReadyCache = { ts: 0, ready: false }; // force fresh status next poll
    if (result.out.trim().endsWith("ok")) {
      res.json({ ok: true, dreadnoughtReady: true });
    } else {
      res.json({ ok: false, error: (result.err || result.out || "install failed").slice(0, 500) });
    }
  } catch (e) {
    console.error("[GhostPort] Error:", e.message);
    res.status(500).json({ ok: false, error: "Install failed" });
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

    const modeFile = await run("cat /etc/phantom/current-mode 2>/dev/null || echo isp");
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
 * POST /api/arsenal/antifingerprint — { enabled: true|false }
 * Toggles anti-fingerprint DNS blocklist (35 tracking/fingerprinting domains).
 * Restored 2026-05-01 — backend assets (/etc/dnsmasq.d/30-anti-fingerprint.conf)
 * remained installed and active after the original delete; only the API toggle was lost.
 */
app.post("/api/arsenal/antifingerprint", async (req, res) => {
  const { enabled } = req.body;
  try {
    const confFile = "/etc/dnsmasq.d/30-anti-fingerprint.conf";
    const disabledFile = confFile + ".disabled";

    if (enabled) {
      await run(`sudo test -f ${disabledFile} && sudo mv ${disabledFile} ${confFile} || true`);
    } else {
      await run(`sudo test -f ${confFile} && sudo mv ${confFile} ${disabledFile} || true`);
    }

    await run("sudo systemctl restart pihole-FTL");
    await withArsenal(arsenal => { arsenal.antiFingerprint = enabled; });

    console.log(`[Arsenal] Anti-fingerprint DNS ${enabled ? "enabled" : "disabled"}`);
    res.json({ ok: true, antiFingerprint: enabled });
  } catch (e) {
    console.error("[Arsenal] Anti-fingerprint toggle failed:", e.message);
    res.status(500).json({ ok: false, error: "Operation failed" });
  }
});

/**
 * POST /api/arsenal/webrtcblock — { enabled: true|false }
 * Toggles WebRTC STUN/TURN port blocking (3478/3479/5349).
 * Restored 2026-05-01 — gp-webrtc-block rules in doublehop.nft / zhop.nft are live.
 */
// Path A (2026-05-02): rules are NO LONGER hardcoded in mode .nft profiles.
// Toggle is the authoritative source. Default state is OFF — customer opts in.
// Mechanism: drops UDP+TCP STUN/TURN ports (3478, 3479, 5349) at the LAN→WAN
// forward stage. Tunnel modes only (rules have no effect outside tunnel forwarding).
app.post("/api/arsenal/webrtcblock", async (req, res) => {
  const { enabled } = req.body;
  try {
    const modeFile = await run("cat /etc/phantom/current-mode 2>/dev/null || echo isp");
    const mode = modeFile.out.trim();
    const inTunnel = mode === "doublehop" || mode === "zhop";

    if (inTunnel) {
      // Idempotent: clear any existing rules first regardless of enable/disable.
      await run('sudo nft -a list chain inet filter forward 2>/dev/null | grep gp-webrtc-block | sed -n "s/.*handle \\([0-9]*\\)/\\1/p" | while read h; do sudo nft delete rule inet filter forward handle $h; done');
      if (enabled) {
        await run('sudo nft add rule inet filter forward iifname "wlan0" udp dport \\{ 3478, 3479, 5349 \\} drop comment \\"gp-webrtc-block\\"');
        await run('sudo nft add rule inet filter forward iifname "wlan0" tcp dport \\{ 3478, 3479, 5349 \\} drop comment \\"gp-webrtc-block\\"');
      }
    }

    await withArsenal(arsenal => { arsenal.webrtcBlock = !!enabled; });
    console.log(`[Arsenal] WebRTC block ${enabled ? "enabled" : "disabled"} (mode=${mode})`);
    res.json({ ok: true, webrtcBlock: !!enabled, applicable: inTunnel });
  } catch (e) {
    console.error("[Arsenal] WebRTC block toggle failed:", e.message);
    res.status(500).json({ ok: false, error: "Operation failed" });
  }
});

/**
 * POST /api/arsenal/covertraffic — { enabled: true|false }
 * Toggles cover traffic generation (traffic-analysis resistance).
 * Restored 2026-05-01 — ghostport-noise.service still installed.
 */
app.post("/api/arsenal/covertraffic", async (req, res) => {
  const { enabled } = req.body;
  try {
    if (enabled) {
      await run("sudo systemctl enable --now ghostport-noise.service");
    } else {
      await run("sudo systemctl disable --now ghostport-noise.service");
    }
    await withArsenal(arsenal => { arsenal.coverTraffic = enabled; });
    console.log(`[Arsenal] Cover traffic ${enabled ? "enabled" : "disabled"}`);
    res.json({ ok: true, coverTraffic: enabled });
  } catch (e) {
    console.error("[Arsenal] Cover traffic toggle failed:", e.message);
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


// ── Theme color sync (for desktop widget) ───────────────
const THEME_FILE = "/etc/phantom/theme.json";

app.get("/api/theme", (req, res) => {
  try {
    const data = JSON.parse(fs.readFileSync(THEME_FILE, "utf8"));
    res.json({ ok: true, color: data.color || "#39ff8f" });
  } catch {
    res.json({ ok: true, color: "#39ff8f" });
  }
});

app.post("/api/theme", (req, res) => {
  const { color } = req.body;
  if (!color || !/^#[0-9a-fA-F]{6}$/.test(color)) {
    return res.status(400).json({ ok: false, error: "Invalid hex color" });
  }
  try {
    fs.writeFileSync(THEME_FILE, JSON.stringify({ color }), "utf8");
    res.json({ ok: true, color });
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message });
  }
});

// ── Privacy Paycheck (money saved from blocked ads) ──────
// Restored 2026-05-01 — planned feature with documented methodology.
// Industry avg CPM (cost per 1000 impressions):
// Display ads: $2-5, Video ads: $10-30, Native ads: $5-15
// Conservative: $3.50 display CPM. Data costs ~$0.001/ad load (avg 150KB payload).
// Time saved: ~2.5 seconds per blocked ad (page load improvement).

app.get("/api/tools/paycheck", (req, res) => {
  try {
    const tally = readTally();
    const blocked = tally.blocked || 0;
    const total = tally.total || 0;

    // Money saved from not seeing ads (advertiser value = your attention value)
    const cpmDisplay = 3.50;   // conservative display ad CPM
    const adRevenueSaved = (blocked / 1000) * cpmDisplay;

    // Data saved (avg ad payload ~150KB including trackers, scripts, pixels)
    const bytesPerAd = 150 * 1024;
    const dataSavedBytes = blocked * bytesPerAd;
    const dataSavedMB = dataSavedBytes / (1024 * 1024);
    const dataSavedGB = dataSavedMB / 1024;

    // Time saved (avg 2.5 seconds per ad load — DNS + fetch + render)
    const secondsPerAd = 2.5;
    const timeSavedSeconds = blocked * secondsPerAd;
    const timeSavedMinutes = timeSavedSeconds / 60;
    const timeSavedHours = timeSavedMinutes / 60;

    // Data cost savings (avg US mobile data: ~$10/GB)
    const dataCostPerGB = 10;
    const dataCostSaved = dataSavedGB * dataCostPerGB;

    // Total estimated savings
    const totalSaved = adRevenueSaved + dataCostSaved;

    res.json({
      ok: true,
      blocked,
      total,
      blockRate: total > 0 ? ((blocked / total) * 100).toFixed(1) : "0.0",
      savings: {
        total: +totalSaved.toFixed(2),
        adValue: +adRevenueSaved.toFixed(2),
        dataCost: +dataCostSaved.toFixed(2),
        dataSavedMB: +dataSavedMB.toFixed(1),
        dataSavedGB: +dataSavedGB.toFixed(2),
        timeSavedMinutes: +timeSavedMinutes.toFixed(1),
        timeSavedHours: +timeSavedHours.toFixed(1),
      },
      methodology: "CPM $3.50 display, 150KB/ad payload, 2.5s/ad load, $10/GB data"
    });
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message });
  }
});

// ── security scan (Lynis) ────────────────────────────────

let lynisScanRunning = false;

app.get("/api/security/lynis", async (req, res) => {
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
    try { if (reportFile) fs.unlinkSync(reportFile); } catch {}
  }
});


// ── Family Shield (parental controls) ─────────────────────
const FAMILY_SHIELD_FILE = "/etc/phantom/family-shield.json";
const FAMILY_SHIELD_GROUP = "FamilyShield";

const FAMILY_SHIELD_LISTS = {
  adult:    ["https://blocklistproject.github.io/Lists/porn.txt"],
  gambling: ["https://blocklistproject.github.io/Lists/gambling.txt"],
  facebook: ["https://blocklistproject.github.io/Lists/facebook.txt"],
  tiktok:   ["https://blocklistproject.github.io/Lists/tiktok.txt"],
  twitter:  ["https://blocklistproject.github.io/Lists/twitter.txt"],
  // ── Palantir-countermeasures categories (2026-04-24) ──
  // Locally-shipped curated lists. Generated from /etc/phantom/blocklists/data-brokers.json.
  acr:          ["file:///etc/phantom/blocklists/acr-hosts.txt"],
  dataBrokers:  ["file:///etc/phantom/blocklists/data-brokers-hosts.txt"],
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

// ── SNI Inspection (Layer 3 — catches DoH/hardcoded IP bypasses) ──
const SNI_INSPECT_CMD = "/usr/local/bin/gp-ssl-inspect";
const SNI_BLOCKLIST_FILE = "/etc/phantom/sni-blocklist.json";

function sniInspectStart() {
  return run(`${SNI_INSPECT_CMD} start`, 10000).then(r => {
    if (!r.ok) console.error("[SNI-Inspector] Start error:", r.err);
    else console.log("[SNI-Inspector] Started");
  });
}

function sniInspectStop() {
  return run(`${SNI_INSPECT_CMD} stop`, 10000).then(r => {
    if (!r.ok) console.error("[SNI-Inspector] Stop error:", r.err);
    else console.log("[SNI-Inspector] Stopped");
  });
}

function sniInspectAddDevice(ip) {
  // Defense-in-depth: re-validate IP before passing to shell
  if (!/^192\.168\.50\.\d{1,3}$/.test(ip)) return Promise.resolve();
  const octet = parseInt(ip.split(".")[3], 10);
  if (octet < 1 || octet > 254) return Promise.resolve();
  return run(`${SNI_INSPECT_CMD} add-device ${ip}`, 10000).then(r => {
    if (!r.ok) console.error(`[SNI-Inspector] Add device ${ip} error:`, r.err);
    else console.log(`[SNI-Inspector] Inspecting ${ip}`);
  });
}

function sniInspectRemoveDevice(ip) {
  if (!/^192\.168\.50\.\d{1,3}$/.test(ip)) return Promise.resolve();
  const octet = parseInt(ip.split(".")[3], 10);
  if (octet < 1 || octet > 254) return Promise.resolve();
  return run(`${SNI_INSPECT_CMD} remove-device ${ip}`, 10000).then(r => {
    if (!r.ok) console.error(`[SNI-Inspector] Remove device ${ip} error:`, r.err);
    else console.log(`[SNI-Inspector] Stopped inspecting ${ip}`);
  });
}

function sniUpdateBlocklist() {
  // Export current blocked domains to a cache file for the SNI inspector
  const config = readFamilyShieldConfig();
  const activeCats = Object.entries(config.categories || {}).filter(([, v]) => v).map(([k]) => k);
  if (activeCats.length === 0) {
    try { fs.writeFileSync(SNI_BLOCKLIST_FILE, JSON.stringify({ domains: [] })); } catch {}
    return;
  }
  // The SNI inspector reads gravity DB directly, but we also provide a hint file
  // with the blocklist URLs so it knows which categories are active
  try {
    fs.writeFileSync(SNI_BLOCKLIST_FILE, JSON.stringify({
      active_categories: activeCats,
      domains: [], // populated by SNI inspector from gravity DB
    }));
  } catch {}
}

// Manage nftables IP blocking for Family Shield categories
const FS_NFT_TABLE = "inet ghostport_fs";

function fsIpBlock(category, enabled) {
  // Validate category is safe for shell (alphanumeric + underscore only)
  if (!/^[a-z_]+$/.test(category)) return;
  const ranges = FAMILY_SHIELD_IP_RANGES[category] || [];
  if (ranges.length === 0) return; // DNS-only category

  const setName = `fs_${category}`;
  const execOpts = { timeout: 10000 };

  if (enabled) {
    // Ensure table and chain exist
    exec(`sudo nft add table ${FS_NFT_TABLE} 2>/dev/null; sudo nft add chain ${FS_NFT_TABLE} forward '{ type filter hook forward priority 0; policy accept; }' 2>/dev/null`, execOpts, () => {});

    // Build nft commands: delete old set, create new, add elements, add rule
    const elements = ranges.join(", ");
    const cmds = [
      `sudo nft delete set ${FS_NFT_TABLE} ${setName} 2>/dev/null; true`,
      `sudo nft add set ${FS_NFT_TABLE} ${setName} '{ type ipv4_addr; flags interval; }'`,
      `sudo nft add element ${FS_NFT_TABLE} ${setName} '{ ${elements} }'`,
    ].join(" && ");

    exec(cmds, execOpts, (err) => {
      if (err) {
        console.error(`[FamilyShield] IP block ${category} set error:`, err.message);
        return;
      }
      // Add forward drop rule if not exists
      exec(`sudo nft -a list chain ${FS_NFT_TABLE} forward 2>/dev/null | grep '${setName}'`, execOpts, (err2, stdout) => {
        if (!stdout || !stdout.trim()) {
          exec(`sudo nft add rule ${FS_NFT_TABLE} forward iifname "wlan0" ip daddr @${setName} drop comment "${setName}"`, execOpts, (err3) => {
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
    exec(`sudo nft -a list chain ${FS_NFT_TABLE} forward 2>/dev/null | grep '${setName}' | awk '{print $NF}'`, execOpts, (err, stdout) => {
      const handle = (stdout || "").trim();
      if (handle) {
        exec(`sudo nft delete rule ${FS_NFT_TABLE} forward handle ${handle}`, execOpts, () => {});
      }
      exec(`sudo nft delete set ${FS_NFT_TABLE} ${setName} 2>/dev/null`, execOpts, () => {
        console.log(`[FamilyShield] IP block disabled: ${category}`);
      });
    });
  }
}

function readFamilyShieldConfig() {
  try {
    const parsed = JSON.parse(fs.readFileSync(FAMILY_SHIELD_FILE, "utf8"));
    // Backfill defaults for categories added after first-boot
    parsed.categories = parsed.categories || {};
    for (const k of ["adult","gambling","facebook","tiktok","twitter","acr","dataBrokers"]) {
      if (!(k in parsed.categories)) parsed.categories[k] = false;
    }
    return parsed;
  }
  catch { return { categories: { adult: false, gambling: false, facebook: false, tiktok: false, twitter: false, acr: false, dataBrokers: false } }; }
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

    // Only show devices with active DHCP leases (currently connected)
    let activeLeases = [];
    try {
      const leaseOut = await getDhcpLeases();
      for (const line of leaseOut.trim().split("\n")) {
        if (!line) continue;
        const parts = line.split(/\s+/);
        if (parts.length >= 4 && parts[2].startsWith("192.168.50.")) {
          activeLeases.push({ ip: parts[2], mac: parts[1], name: parts[3] === "*" ? parts[2] : parts[3] });
        }
      }
    } catch {}

    const devices = [];
    for (const lease of activeLeases) {
      const piholeClient = clients.find(c => c.client === lease.ip);
      const shielded = piholeClient && groupId !== null && Array.isArray(piholeClient.groups) && piholeClient.groups.includes(groupId);
      const name = (suggestMap[lease.ip] && suggestMap[lease.ip].name) || lease.name || lease.ip;
      devices.push({ ip: lease.ip, mac: lease.mac, name, shielded: !!shielded });
    }

    // Check SNI inspector status
    let sniActive = false;
    try {
      const sniStatus = execSync(`${SNI_INSPECT_CMD} status 2>/dev/null`, { timeout: 3000 }).toString().trim();
      sniActive = sniStatus.startsWith("running");
    } catch {}

    res.json({ ok: true, enabled, categories: config.categories, devices, sniInspection: sniActive });
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
      // Start SNI inspector when Family Shield is enabled
      sniInspectStart();
      sniUpdateBlocklist();
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
      // Stop SNI inspector when Family Shield is disabled
      sniInspectStop();
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

    // Update SNI inspector blocklist cache
    sniUpdateBlocklist();

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

    // Sync SNI inspection for this device
    if (shielded) {
      sniInspectAddDevice(ip);
    } else {
      sniInspectRemoveDevice(ip);
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


// ── Bedtime Torpedo (per-device internet kill schedule) ────
const BEDTIME_FILE = "/etc/phantom/bedtime.json";
const BEDTIME_CRON = "/etc/cron.d/ghostport-bedtime";

function readBedtime() {
  try { return JSON.parse(fs.readFileSync(BEDTIME_FILE, "utf8")); }
  catch { return { rules: [] }; }
}

function writeBedtime(data) {
  fs.writeFileSync(BEDTIME_FILE, JSON.stringify(data, null, 2));
  fs.chmodSync(BEDTIME_FILE, 0o644);
}

// Sync all bedtime rules to cron + nftables
async function syncBedtimeCron(rules) {
  const lines = ["# Managed by GhostPort Bedtime Torpedo — do not edit manually",
                  "SHELL=/bin/bash", "PATH=/usr/local/bin:/usr/bin:/bin", ""];
  for (const rule of rules) {
    if (!rule.enabled) continue;
    const mac = rule.mac;
    if (!/^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$/.test(mac)) continue;

    // Parse times
    const [onH, onM] = rule.bedtime.split(":").map(Number);
    const [offH, offM] = rule.wakeup.split(":").map(Number);
    const dayStr = (rule.days || [0,1,2,3,4,5,6]).join(",");

    // Block at bedtime
    lines.push(`${onM} ${onH} * * ${dayStr} root /sbin/nft add rule inet filter forward ether saddr ${mac} drop comment "bedtime-${rule.id}" 2>/dev/null # bedtime-on-${rule.id}`);
    // Also block DNS so device sees immediate effect
    lines.push(`${onM} ${onH} * * ${dayStr} root /sbin/nft add rule inet filter input ether saddr ${mac} udp dport 53 drop comment "bedtime-dns-${rule.id}" 2>/dev/null # bedtime-dns-on-${rule.id}`);

    // Unblock at wakeup
    lines.push(`${offM} ${offH} * * ${dayStr} root /sbin/nft delete rule inet filter forward handle $(/sbin/nft -a list chain inet filter forward 2>/dev/null | grep 'bedtime-${rule.id}' | grep -oP 'handle \\K[0-9]+' | head -1) 2>/dev/null; /sbin/nft delete rule inet filter input handle $(/sbin/nft -a list chain inet filter input 2>/dev/null | grep 'bedtime-dns-${rule.id}' | grep -oP 'handle \\K[0-9]+' | head -1) 2>/dev/null # bedtime-off-${rule.id}`);
    lines.push("");
  }
  const tmpCron = gpTmpFile("gp-bedtime-cron") + ".tmp";
  fs.writeFileSync(tmpCron, lines.join("\n") + "\n");
  await run(`cat ${tmpCron} | sudo tee ${BEDTIME_CRON} > /dev/null && sudo chmod 644 ${BEDTIME_CRON}`);
  fs.unlinkSync(tmpCron);
}

// Enforce current bedtime rules NOW (for rules that should be active right now)
async function enforceBedtimeNow(rules) {
  const now = new Date();
  const hour = now.getHours();
  const min = now.getMinutes();
  const nowMin = hour * 60 + min;
  const dow = now.getDay();

  for (const rule of rules) {
    if (!rule.enabled) continue;
    const mac = rule.mac;
    if (!/^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$/.test(mac)) continue;
    const days = rule.days || [0,1,2,3,4,5,6];
    if (!days.includes(dow)) continue;

    const [bH, bM] = rule.bedtime.split(":").map(Number);
    const [wH, wM] = rule.wakeup.split(":").map(Number);
    const bedMin = bH * 60 + bM;
    const wakeMin = wH * 60 + wM;

    let shouldBlock = false;
    if (bedMin > wakeMin) {
      // Cross-midnight (most common): bedtime 21:00, wakeup 07:00
      shouldBlock = (nowMin >= bedMin || nowMin < wakeMin);
    } else {
      // Same-day: bedtime 08:00, wakeup 15:00
      shouldBlock = (nowMin >= bedMin && nowMin < wakeMin);
    }

    if (shouldBlock) {
      // Check if rule already in nftables
      const check = await run(`sudo nft -a list chain inet filter forward 2>/dev/null | grep 'bedtime-${rule.id}'`);
      if (!check.ok || !check.out.includes(`bedtime-${rule.id}`)) {
        await run(`sudo nft add rule inet filter forward ether saddr ${mac} drop comment '"bedtime-${rule.id}"'`);
        await run(`sudo nft add rule inet filter input ether saddr ${mac} udp dport 53 drop comment '"bedtime-dns-${rule.id}"'`);
        console.log(`[Bedtime] Enforced block for ${rule.name || mac} (rule ${rule.id})`);
      }
    } else {
      // Remove if present
      const check = await run(`sudo nft -a list chain inet filter forward 2>/dev/null | grep 'bedtime-${rule.id}' | grep -oP 'handle \\K[0-9]+' | head -1`);
      if (check.ok && check.out.trim()) {
        await run(`sudo nft delete rule inet filter forward handle ${check.out.trim()}`);
        const dnsCheck = await run(`sudo nft -a list chain inet filter input 2>/dev/null | grep 'bedtime-dns-${rule.id}' | grep -oP 'handle \\K[0-9]+' | head -1`);
        if (dnsCheck.ok && dnsCheck.out.trim()) {
          await run(`sudo nft delete rule inet filter input handle ${dnsCheck.out.trim()}`);
        }
        console.log(`[Bedtime] Removed block for ${rule.name || mac} (rule ${rule.id})`);
      }
    }
  }
}

/**
 * GET /api/bedtime — list all bedtime rules
 */
app.get("/api/bedtime", (req, res) => {
  const data = readBedtime();
  res.json({ ok: true, rules: data.rules || [] });
});

/**
 * POST /api/bedtime — create a new bedtime rule
 * Body: { mac, name, bedtime: "HH:MM", wakeup: "HH:MM", days: [0-6], enabled: true }
 */
app.post("/api/bedtime", async (req, res) => {
  try {
    const { mac, name, bedtime, wakeup, days } = req.body;

    if (!mac || !/^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$/.test(mac)) {
      return res.status(400).json({ ok: false, error: "Invalid MAC address" });
    }
    if (!bedtime || !/^\d{2}:\d{2}$/.test(bedtime)) {
      return res.status(400).json({ ok: false, error: "Invalid bedtime (use HH:MM)" });
    }
    if (!wakeup || !/^\d{2}:\d{2}$/.test(wakeup)) {
      return res.status(400).json({ ok: false, error: "Invalid wakeup time (use HH:MM)" });
    }

    const data = readBedtime();
    const id = crypto.randomBytes(4).toString("hex");
    const rule = {
      id,
      mac: mac.toLowerCase(),
      name: (name || "").slice(0, 64).replace(/[^a-zA-Z0-9 _-]/g, ""),
      bedtime,
      wakeup,
      days: Array.isArray(days) ? days.filter(d => d >= 0 && d <= 6) : [0,1,2,3,4,5,6],
      enabled: true,
      created: new Date().toISOString(),
    };
    data.rules.push(rule);
    writeBedtime(data);
    await syncBedtimeCron(data.rules);
    await enforceBedtimeNow(data.rules);

    console.log(`[Bedtime] Rule created: ${rule.name || rule.mac} ${rule.bedtime}-${rule.wakeup}`);
    res.json({ ok: true, rule });
  } catch (e) {
    console.error("[Bedtime] Create error:", e.message);
    res.status(500).json({ ok: false, error: "Failed to create bedtime rule" });
  }
});

/**
 * PUT /api/bedtime/:id — update a bedtime rule
 */
app.put("/api/bedtime/:id", async (req, res) => {
  try {
    const data = readBedtime();
    const rule = data.rules.find(r => r.id === req.params.id);
    if (!rule) return res.status(404).json({ ok: false, error: "Rule not found" });

    const { bedtime, wakeup, days, enabled, name } = req.body;
    if (bedtime && /^\d{2}:\d{2}$/.test(bedtime)) rule.bedtime = bedtime;
    if (wakeup && /^\d{2}:\d{2}$/.test(wakeup)) rule.wakeup = wakeup;
    if (Array.isArray(days)) rule.days = days.filter(d => d >= 0 && d <= 6);
    if (typeof enabled === "boolean") rule.enabled = enabled;
    if (name !== undefined) rule.name = (name || "").slice(0, 64).replace(/[^a-zA-Z0-9 _-]/g, "");

    writeBedtime(data);
    await syncBedtimeCron(data.rules);
    await enforceBedtimeNow(data.rules);

    res.json({ ok: true, rule });
  } catch (e) {
    console.error("[Bedtime] Update error:", e.message);
    res.status(500).json({ ok: false, error: "Failed to update rule" });
  }
});

/**
 * DELETE /api/bedtime/:id — delete a bedtime rule
 */
app.delete("/api/bedtime/:id", async (req, res) => {
  try {
    const data = readBedtime();
    const idx = data.rules.findIndex(r => r.id === req.params.id);
    if (idx === -1) return res.status(404).json({ ok: false, error: "Rule not found" });

    const rule = data.rules[idx];
    // Remove nftables rules for this device
    const fwdCheck = await run(`sudo nft -a list chain inet filter forward 2>/dev/null | grep 'bedtime-${rule.id}' | grep -oP 'handle \\K[0-9]+' | head -1`);
    if (fwdCheck.ok && fwdCheck.out.trim()) {
      await run(`sudo nft delete rule inet filter forward handle ${fwdCheck.out.trim()}`);
    }
    const dnsCheck = await run(`sudo nft -a list chain inet filter input 2>/dev/null | grep 'bedtime-dns-${rule.id}' | grep -oP 'handle \\K[0-9]+' | head -1`);
    if (dnsCheck.ok && dnsCheck.out.trim()) {
      await run(`sudo nft delete rule inet filter input handle ${dnsCheck.out.trim()}`);
    }

    data.rules.splice(idx, 1);
    writeBedtime(data);
    await syncBedtimeCron(data.rules);

    console.log(`[Bedtime] Rule deleted: ${rule.name || rule.mac}`);
    res.json({ ok: true });
  } catch (e) {
    console.error("[Bedtime] Delete error:", e.message);
    res.status(500).json({ ok: false, error: "Failed to delete rule" });
  }
});

/**
 * POST /api/bedtime/:id/test — immediately block/unblock for testing (30-second torpedo)
 */
app.post("/api/bedtime/:id/test", async (req, res) => {
  try {
    const data = readBedtime();
    const rule = data.rules.find(r => r.id === req.params.id);
    if (!rule) return res.status(404).json({ ok: false, error: "Rule not found" });

    const mac = rule.mac;
    // Fire torpedo for 30 seconds
    await run(`sudo nft add rule inet filter forward ether saddr ${mac} drop comment '"bedtime-test-${rule.id}"'`);
    await run(`sudo nft add rule inet filter input ether saddr ${mac} udp dport 53 drop comment '"bedtime-test-dns-${rule.id}"'`);

    // Auto-remove after 30 seconds
    setTimeout(async () => {
      const fwd = await run(`sudo nft -a list chain inet filter forward 2>/dev/null | grep 'bedtime-test-${rule.id}' | grep -oP 'handle \\K[0-9]+' | head -1`);
      if (fwd.ok && fwd.out.trim()) await run(`sudo nft delete rule inet filter forward handle ${fwd.out.trim()}`);
      const dns = await run(`sudo nft -a list chain inet filter input 2>/dev/null | grep 'bedtime-test-dns-${rule.id}' | grep -oP 'handle \\K[0-9]+' | head -1`);
      if (dns.ok && dns.out.trim()) await run(`sudo nft delete rule inet filter input handle ${dns.out.trim()}`);
      console.log(`[Bedtime] Test torpedo expired for ${rule.name || rule.mac}`);
    }, 30000);

    console.log(`[Bedtime] Test torpedo fired at ${rule.name || rule.mac} (30s)`);
    res.json({ ok: true, message: `Internet killed for ${rule.name || rule.mac} for 30 seconds` });
  } catch (e) {
    res.status(500).json({ ok: false, error: "Torpedo misfired" });
  }
});

// Enforce bedtime rules on server start
(async () => {
  try {
    const data = readBedtime();
    if (data.rules && data.rules.length > 0) {
      await syncBedtimeCron(data.rules);
      await enforceBedtimeNow(data.rules);
      console.log(`[Bedtime] ${data.rules.length} rules loaded, enforcement checked`);
    }
  } catch (e) {
    console.error("[Bedtime] Init error:", e.message);
  }
})();

// ── WiFi WAN management ───────────────────────────────────

const WAN_CONF = "/etc/phantom/wan.json";

function readWanConfig() {
  try { return JSON.parse(fs.readFileSync(WAN_CONF, "utf8")); }
  catch { return { type: "ethernet", ssid: "", password: "" }; }
}

function writeWanConfig(config) {
  fs.writeFileSync(WAN_CONF, JSON.stringify(config, null, 2), "utf8");
}

/**
 * GET /api/wan/status
 * Returns current WAN type and interface state
 */
app.get("/api/wan/status", async (req, res) => {
  try {
    const config = readWanConfig();
    const result = await run("sudo gp-wan status");
    const lines = (result.out || "").split("\n");
    const parsed = {};
    for (const line of lines) {
      const [k, v] = line.split("=", 2);
      if (k && v !== undefined) parsed[k.trim()] = v.trim();
    }
    res.json({
      ok: true,
      type: config.type || "ethernet",
      ssid: config.ssid || "",
      interface: parsed.wan_interface || "eth0",
      wlan1State: parsed.wlan1_state || "down",
      wlan1Ip: parsed.wlan1_ip || null,
      connectedSsid: parsed.connected_ssid || null,
      eth0Ip: parsed.eth0_ip || null,
    });
  } catch (e) {
    res.status(500).json({ ok: false, error: "Failed to get WAN status" });
  }
});

/**
 * POST /api/wan/scan
 * Scans for nearby WiFi networks (may take up to 30s)
 */
app.post("/api/wan/scan", async (req, res) => {
  try {
    const result = await run("sudo gp-wan scan", 30000);
    try {
      const networks = JSON.parse(result.out || "[]");
      res.json({ ok: true, networks });
    } catch {
      res.json({ ok: true, networks: [] });
    }
  } catch (e) {
    res.status(500).json({ ok: false, error: "Scan failed" });
  }
});

/**
 * POST /api/wan/ethernet
 * Switch back to Ethernet WAN
 */
app.post("/api/wan/ethernet", async (req, res) => {
  try {
    console.log("[WAN] Switching to Ethernet");
    const result = await run("sudo gp-wan ethernet", 30000);
    logActivity("wan", "Switched to Ethernet WAN");
    res.json({ ok: result.ok, message: result.out, error: result.err || null });
  } catch (e) {
    res.status(500).json({ ok: false, error: "Failed to switch to Ethernet" });
  }
});

/**
 * POST /api/wan/wireless
 * Body: { ssid: string, password: string }
 * Save creds and connect to WiFi WAN
 */
app.post("/api/wan/wireless", async (req, res) => {
  try {
    const { ssid, password } = req.body;

    // Validate SSID: 1-32 chars, no control characters
    if (!ssid || typeof ssid !== "string" || ssid.length < 1 || ssid.length > 32) {
      return res.status(400).json({ ok: false, error: "SSID must be 1-32 characters" });
    }
    if (/[\x00-\x1f\x7f]/.test(ssid)) {
      return res.status(400).json({ ok: false, error: "SSID contains invalid characters" });
    }

    // Validate password: 8-63 chars, no control characters
    if (!password || typeof password !== "string" || password.length < 8 || password.length > 63) {
      return res.status(400).json({ ok: false, error: "Password must be 8-63 characters" });
    }
    if (/[\x00-\x1f\x7f]/.test(password)) {
      return res.status(400).json({ ok: false, error: "Password contains invalid characters" });
    }

    // Write creds to wan.json (avoids shell injection)
    const config = readWanConfig();
    config.ssid = ssid;
    config.password = password;
    writeWanConfig(config);

    console.log(`[WAN] Connecting to WiFi: ${ssid}`);
    const result = await run("sudo gp-wan wireless", 60000);

    if (result.ok) {
      logActivity("wan", `Connected to WiFi WAN: ${ssid}`);
    } else {
      logActivity("wan", `WiFi WAN connection failed: ${ssid}`, result.err);
    }

    res.json({ ok: result.ok, message: result.out, error: result.err || null });
  } catch (e) {
    res.status(500).json({ ok: false, error: "Failed to connect to WiFi" });
  }
});


// ── passthrough trusted devices (T-0207 Layer A / T-0211) ──────
// Exposes the gp-allow CLI to the dashboard so non-CLI operators
// can flip a per-device route between tunnel and ISP.
//
// GET  /api/passthrough           — read /etc/ghostport/passthrough.json
// POST /api/passthrough/via       — { mac, target } → gp-allow via
// POST /api/passthrough/apply     — gp-allow apply

const PASSTHROUGH_FILE = "/etc/ghostport/passthrough.json";
const MAC_RE = /^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}$/;

app.get("/api/passthrough", (req, res) => {
  try {
    const raw = fs.readFileSync(PASSTHROUGH_FILE, "utf8");
    const data = JSON.parse(raw);
    res.json({ ok: true, devices: Array.isArray(data.devices) ? data.devices : [] });
  } catch (e) {
    if (e.code === "ENOENT") {
      // No passthrough configured yet — empty list, not an error
      return res.json({ ok: true, devices: [] });
    }
    res.status(500).json({ ok: false, error: "Cannot read passthrough.json" });
  }
});

app.post("/api/passthrough/via", async (req, res) => {
  try {
    const { mac, target } = req.body || {};
    if (!mac || !MAC_RE.test(mac)) {
      return res.status(400).json({ ok: false, error: "Invalid MAC" });
    }
    if (target !== "tunnel" && target !== "isp") {
      return res.status(400).json({ ok: false, error: "target must be 'tunnel' or 'isp'" });
    }
    // Shell-quoted MAC (already validated to MAC shape — no shell metacharacters possible)
    const result = await run(`sudo -n gp-allow via ${mac} ${target}`, 10000);
    if (!result.ok) {
      return res.status(500).json({ ok: false, error: (result.err || result.out || "").slice(0, 200) });
    }
    logActivity("passthrough", `route_via ${target} for ${mac}`);
    res.json({ ok: true, message: result.out });
  } catch (e) {
    res.status(500).json({ ok: false, error: "Failed to update route_via" });
  }
});

app.post("/api/passthrough/apply", async (req, res) => {
  try {
    const result = await run("sudo -n gp-allow apply", 30000);
    if (!result.ok) {
      return res.status(500).json({ ok: false, error: (result.err || result.out || "").slice(0, 200) });
    }
    logActivity("passthrough", "applied to firewall");
    res.json({ ok: true, message: result.out });
  } catch (e) {
    res.status(500).json({ ok: false, error: "Failed to apply passthrough" });
  }
});


// ── fleet checkin (periodic command polling) ────────────────

const FLEET_CHECKIN_INTERVAL = 60000; // 1 minute

function getFleetConfig() {
  for (const f of ["/etc/phantom/fleet.json", "/etc/phantom/fleet-auth.json"]) {
    try {
      const data = JSON.parse(fs.readFileSync(f, "utf8"));
      if (data.fleet_token || data.fleet_server) return data;
    } catch {}
  }
  return null;
}

async function fleetCheckin() {
  const fleet = getFleetConfig();
  if (!fleet || !fleet.fleet_server || !fleet.fleet_token) return;

  const deviceId = fleet.device_id || "unknown";
  const url = `${fleet.fleet_server}/fleet/devices/${encodeURIComponent(deviceId)}/commands`;

  try {
    const result = await new Promise((resolve, reject) => {
      const req = http.request(url, {
        method: "GET",
        headers: { "Authorization": "Bearer " + fleet.fleet_token },
        timeout: 10000
      }, res => {
        let data = "";
        res.on("data", c => data += c);
        res.on("end", () => {
          try { resolve({ status: res.statusCode, body: JSON.parse(data) }); }
          catch { resolve({ status: res.statusCode, body: {} }); }
        });
      });
      req.on("error", reject);
      req.on("timeout", () => { req.destroy(); reject(new Error("timeout")); });
      req.end();
    });

    if (result.status !== 200 || !result.body.commands) return;

    for (const cmd of result.body.commands) {
      await handleFleetCommand(cmd, fleet);
    }
  } catch {
    // Silent — fleet server may be unreachable (e.g. WireGuard down)
  }
}

function verifyCommandHMAC(cmd, fleet) {
  if (!fleet.hmac_secret) {
    console.error("[Fleet] No hmac_secret configured — rejecting all commands for security");
    return false;
  }
  if (!cmd.signature || typeof cmd.signature !== "string" || !/^[0-9a-f]{64}$/i.test(cmd.signature)) {
    console.warn(`[Fleet] Command ${cmd.id} has invalid/missing signature — rejecting`);
    return false;
  }
  const message = `${cmd.id}|${cmd.type}|${cmd.payload || ""}|${cmd.created_at}`;
  const expected = crypto.createHmac("sha256", fleet.hmac_secret).update(message).digest("hex");
  if (!crypto.timingSafeEqual(Buffer.from(expected, "hex"), Buffer.from(cmd.signature, "hex"))) {
    console.error(`[Fleet] HMAC MISMATCH on command ${cmd.id} (type: ${cmd.type}) — possible tampering`);
    logActivity("security", `Fleet command HMAC verification failed: ${cmd.type} (${cmd.id})`);
    return false;
  }
  return true;
}

async function handleFleetCommand(cmd, fleet) {
  console.log(`[Fleet] Processing command: ${cmd.type} (id: ${cmd.id})`);

  // Verify HMAC signature before executing ANY command
  if (!verifyCommandHMAC(cmd, fleet)) {
    console.error(`[Fleet] REJECTED unsigned/tampered command: ${cmd.type} (${cmd.id})`);
    return;
  }

  try {
    if (cmd.type === "totp-reset") {
      // Clear TOTP config — user can re-setup from dashboard
      const auth = readAuth();
      if (auth && auth.totp) {
        delete auth.totp;
        writeAuth(auth);
        sessions.clear();
        console.log("[Fleet] TOTP reset via fleet command — all sessions cleared");
        logActivity("security", "TOTP reset via fleet command");
      }
    } else if (cmd.type === "totp-regen-backup") {
      // Regenerate backup codes without touching the TOTP secret
      const auth = readAuth();
      if (auth && auth.totp && auth.totp.setupComplete && auth.totp.secret) {
        auth.totp.backupCodes = generateBackupCodes(8);
        writeAuth(auth);
        console.log("[Fleet] Backup codes regenerated via fleet command");
        logActivity("security", "Backup codes regenerated via fleet command");
      } else {
        console.log("[Fleet] totp-regen-backup skipped — TOTP not configured");
      }
    }

    // Acknowledge command
    const ackUrl = `${fleet.fleet_server}/fleet/devices/${encodeURIComponent(fleet.device_id)}/commands/${encodeURIComponent(cmd.id)}/ack`;
    const ackBody = JSON.stringify({ status: "completed" });
    await new Promise((resolve) => {
      const req = http.request(ackUrl, {
        method: "POST",
        headers: {
          "Authorization": "Bearer " + fleet.fleet_token,
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(ackBody)
        },
        timeout: 10000
      }, res => {
        let d = ""; res.on("data", c => d += c); res.on("end", () => resolve());
      });
      req.on("error", () => resolve());
      req.on("timeout", () => { req.destroy(); resolve(); });
      req.write(ackBody);
      req.end();
    });

    console.log(`[Fleet] Command ${cmd.id} acknowledged`);
  } catch (e) {
    console.error(`[Fleet] Command ${cmd.type} failed:`, e.message);
  }
}

// Start checkin loop after 10s delay (let server boot)
setTimeout(() => {
  fleetCheckin();
  setInterval(fleetCheckin, FLEET_CHECKIN_INTERVAL);
}, 10000);

// ── crash protection ────────────────────────────────────────
// Prevent silent crashes — log and let systemd restart us
process.on("uncaughtException", (err) => {
  console.error("[FATAL] Uncaught exception:", err.message);
  console.error(err.stack);
  logActivity("error", "Server crash: uncaught exception", err.message);
  try { saveActivity(); } catch {}
  process.exit(1);
});
process.on("unhandledRejection", (reason) => {
  console.error("[ERROR] Unhandled promise rejection:", reason);
  logActivity("error", "Unhandled promise rejection", String(reason));
});

// ── start ──────────────────────────────────────────────────
// HTTP — primary, no SSL warnings for customers
http.createServer(app).listen(PORT, "0.0.0.0", () => {
  console.log(`  ☠  HTTP  running on port ${PORT}`);
  logActivity("system", "GhostPort server started");
});

// HTTPS — try Tailscale certs first (valid Let's Encrypt), fall back to self-signed
try {
  let sslKey, sslCert, certSource;
  // Priority 1: Tailscale certs (valid LE certs from `tailscale cert`)
  const tsHostname = (() => { try { return require("os").hostname(); } catch { return "ghostport"; } })();
  const tsCertPaths = [
    { key: `/opt/phantom/ssl/${tsHostname}.key`, cert: `/opt/phantom/ssl/${tsHostname}.crt` },
    { key: "/opt/phantom/ssl/tailscale.key", cert: "/opt/phantom/ssl/tailscale.crt" },
  ];
  for (const p of tsCertPaths) {
    try {
      sslKey = fs.readFileSync(p.key);
      sslCert = fs.readFileSync(p.cert);
      certSource = `Tailscale (${p.cert})`;
      break;
    } catch {}
  }
  // Priority 2: Self-signed fallback
  if (!sslKey) {
    sslKey = fs.readFileSync("/opt/phantom/ssl/ghostport.key");
    sslCert = fs.readFileSync("/opt/phantom/ssl/ghostport.crt");
    certSource = "self-signed";
  }
  https.createServer({ key: sslKey, cert: sslCert, minVersion: "TLSv1.2", ciphers: "ECDHE+AESGCM:ECDHE+CHACHA20:!aNULL:!MD5:!DSS" }, app).listen(4201, "0.0.0.0", () => {
    console.log(`  ☠  HTTPS running on port 4201 (${certSource})`);
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

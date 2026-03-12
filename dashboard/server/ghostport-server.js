/**
 * GhostPort — Command Deck API Server
 * Runs on the Pi, controls gp-mode, serves live status
 * Start with: sudo node ghostport-server.js
 */

const express = require("express");
const { exec } = require("child_process");
const path = require("path");
const cors = require("cors");

const app = express();
const PORT = 4200;

app.use(cors());
app.use(express.json());
app.use(express.static(path.join(__dirname, "public")));

// ── helpers ────────────────────────────────────────────────

function run(cmd) {
  return new Promise((resolve) => {
    exec(cmd, { timeout: 10000 }, (err, stdout, stderr) => {
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

    // Parse current active mode from nft ruleset
    const nft = await run("sudo nft list ruleset 2>/dev/null");
    let activeMode = "isp";
    if (nft.out.includes("tailscale0") && nft.out.includes("wg0")) activeMode = "zhop";
    else if (nft.out.includes("wg0")) activeMode = "doublehop";
    else if (nft.out.includes("tailscale0")) activeMode = "zerotrust";

    res.json({
      ok: true,
      activeMode,
      tunnels: {
        wg0: wg.out.trim() === "up" ? "up" : "down",
        tailscale: ts.out.trim() === "up" ? "up" : "down",
        pihole: "up", // Pi-hole is always on
      },
      ip: ip.out.trim() || "unknown",
      uptime: formatUptime(parseFloat(uptime.out) || 0),
      adsBlocked: parseInt(pihole.out.trim()) || 0,
      raw: gpStatus.out,
    });
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message });
  }
});

/**
 * POST /api/mode
 * Body: { mode: "isp" | "zerotrust" | "doublehop" | "zhop" }
 * Switches the active gp-mode
 */
app.post("/api/mode", async (req, res) => {
  const { mode } = req.body;
  const valid = ["isp", "zerotrust", "doublehop", "zhop"];

  if (!valid.includes(mode)) {
    return res.status(400).json({ ok: false, error: `Invalid mode: ${mode}` });
  }

  console.log(`[GhostPort] Switching to mode: ${mode}`);
  const result = await run(`sudo gp-mode ${mode}`);

  if (!result.ok) {
    console.error(`[GhostPort] Mode switch failed: ${result.err}`);
    return res.status(500).json({ ok: false, error: result.err || "Mode switch failed" });
  }

  console.log(`[GhostPort] Mode switched: ${result.out}`);
  res.json({ ok: true, mode, message: result.out });
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

// ── start ──────────────────────────────────────────────────

app.listen(PORT, "0.0.0.0", () => {
  console.log(`
  ☠  GhostPort Command Deck  ☠
  ─────────────────────────────
  API running at http://0.0.0.0:${PORT}
  Access from any device on your network:
  → http://<PI_IP>:${PORT}
  `);
});

  const API = "";

  // CSRF protection
  let _csrfToken = localStorage.getItem("gp-csrf") || "";
  const _origFetch = window.fetch;
  window.fetch = function(url, opts = {}) {
    if (opts.method && ["POST","PUT","DELETE","PATCH"].includes(opts.method.toUpperCase())) {
      opts.headers = { ...(opts.headers || {}), "X-CSRF-Token": _csrfToken };
    }
    return _origFetch.call(this, url, opts);
  };
  _origFetch(API + "/api/auth/csrf").then(r => r.ok ? r.json() : null).then(d => {
    if (d && d.csrfToken) { _csrfToken = d.csrfToken; localStorage.setItem("gp-csrf", _csrfToken); }
  }).catch(() => {});

  const MODES = {
    isp:       { icon: "⚓", label: "ISP — Open Waters",              enc: "None",    encDetail: "No encryption" },
    zerotrust: { icon: "👻", label: "Zero Trust — Ghost Cloak",       enc: "AES-256", encDetail: "Tailscale" },
    doublehop: { icon: "💀", label: "Double Hop — Dead Man's Route",  enc: "AES-256", encDetail: "WireGuard" },
    zhop:      { icon: "🏴‍☠️", label: "Z-HOP — Davy Jones",            enc: "AES-256", encDetail: "WG + DNS Lock" },
  };

  const TUNNEL_ROLES = {
    isp:       { wg: "INACTIVE", ts: "MANAGEMENT" },
    zerotrust: { wg: "INACTIVE", ts: "MANAGEMENT" },
    doublehop: { wg: "DATA TUNNEL", ts: "MANAGEMENT" },
    zhop:      { wg: "DATA TUNNEL", ts: "MANAGEMENT" },
  };

  let currentMode = null;
  let switching = false;
  let rollbackCountdownInterval = null;

  function esc(s) { return s == null ? "" : String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#39;"); }

  function log(msg, type = "info") {
    const box = document.getElementById("log-box");
    const now = new Date().toTimeString().slice(0, 8);
    const safe = String(msg).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
    const entry = document.createElement("div");
    entry.className = "log-entry";
    entry.innerHTML = `<span class="log-time">${now}</span><span class="log-msg ${type}">${safe}</span>`;
    box.appendChild(entry);
    box.scrollTop = box.scrollHeight;
    // Keep log trimmed
    while (box.children.length > 50) box.removeChild(box.firstChild);
  }

  function setTunnel(id, state) {
    const dot = document.getElementById("dot-" + id);
    const val = document.getElementById("val-" + id);
    const isUp = state === "up";
    dot.className = "tunnel-dot " + (isUp ? "up" : "down");
    val.className = "tunnel-val " + (isUp ? "up" : "down");
    val.textContent = isUp ? "UP" : "DOWN";

    if (id === "ts") {
      const toggle = document.getElementById("ts-toggle");
      const label = document.getElementById("ts-toggle-label");
      toggle.classList.toggle("on", isUp);
      label.textContent = isUp ? "ON" : "OFF";
      label.style.color = isUp ? "var(--green)" : "var(--text-faint)";
    }
  }

  function updateRollbackBar(rollback) {
    const bar = document.getElementById("rollback-bar");
    const desc = document.getElementById("rollback-desc");
    const countdown = document.getElementById("rollback-countdown");

    if (rollback && rollback.pending) {
      bar.classList.add("visible");
      desc.textContent = `Will revert to ${(rollback.target || "ISP").toUpperCase()} if not confirmed`;
      countdown.textContent = rollback.remainingSec;

      // Disable mode cards while rollback is pending
      document.querySelectorAll(".mode-card").forEach(c => {
        if (!c.classList.contains("active")) c.classList.add("disabled");
      });
    } else {
      bar.classList.remove("visible");
      document.querySelectorAll(".mode-card").forEach(c => c.classList.remove("disabled"));
    }
  }

  function updateUI(data) {
    const mode = data.activeMode;
    const info = MODES[mode] || MODES.isp;
    const roles = TUNNEL_ROLES[mode] || TUNNEL_ROLES.isp;

    // Banner
    document.getElementById("banner-icon").textContent = info.icon;
    document.getElementById("banner-mode").textContent = info.label;
    document.getElementById("banner-ip").textContent = data.ip;
    document.getElementById("banner-ip-label").textContent = mode === "isp" ? "ISP IP (exposed)" : "Masked IP";

    // Header
    document.getElementById("status-dot").className = "status-dot";
    document.getElementById("status-text").textContent = "ALL SYSTEMS OPERATIONAL";
    document.getElementById("uptime").textContent = data.uptime;

    // Cards
    ["isp", "zerotrust", "doublehop", "zhop"].forEach(m => {
      const card = document.getElementById("card-" + m);
      card.classList.toggle("active", m === mode);
      const old = card.querySelector(".active-badge");
      if (old) old.remove();
      if (m === mode) {
        const badge = document.createElement("div");
        badge.className = "active-badge";
        badge.textContent = "ACTIVE";
        card.appendChild(badge);
      }
    });

    // Tunnels
    setTunnel("wg", data.tunnels.wg0);
    setTunnel("ts", data.tunnels.tailscale);

    // Tunnel roles
    document.getElementById("role-wg").textContent = roles.wg;
    document.getElementById("role-ts").textContent = roles.ts;

    // Stats
    adsCache.session = data.adsBlocked || 0;
    adsCache.allTime = data.adsBlockedAllTime || adsCache.session;
    const currentRange = document.getElementById("ads-range").value;
    document.getElementById("stat-ads").textContent = (adsCache[currentRange] || 0).toLocaleString();
    document.getElementById("stat-ip").textContent = data.ip;
    document.getElementById("stat-ip-label").textContent = mode === "isp" ? "your ISP IP" : "VPN/tunnel IP";
    document.getElementById("stat-enc").textContent = info.enc;
    document.getElementById("stat-enc-detail").textContent = info.encDetail;

    // Rollback bar
    updateRollbackBar(data.rollback);

    currentMode = mode;
  }

  let adsCache = {};
  async function fetchAdsRange() {
    const range = document.getElementById("ads-range").value;
    const labels = { session: "blocked this session", allTime: "blocked all time" };
    document.getElementById("stat-ads-label").textContent = labels[range];

    // Use cached data if available (session updates every poll, allTime on demand)
    if (range === "session" && adsCache.session !== undefined) {
      document.getElementById("stat-ads").textContent = adsCache.session.toLocaleString();
      return;
    }

    try {
      const res = await fetch(API + "/api/pihole/stats");
      const data = await res.json();
      if (data.ok) {
        adsCache.session = data.session.blocked;
        adsCache.allTime = data.allTime.blocked;
        document.getElementById("stat-ads").textContent = (adsCache[range] || 0).toLocaleString();
      }
    } catch (e) { /* silent */ }
  }

  async function fetchStatus() {
    try {
      const res = await fetch(API + "/api/status");
      const data = await res.json();
      if (data.ok) updateUI(data);
    } catch (e) {
      document.getElementById("status-text").textContent = "CONNECTION LOST";
      document.getElementById("status-dot").className = "status-dot error";
    }
  }

  async function confirmMode() {
    log("Confirming mode switch...", "info");
    try {
      const res = await fetch(API + "/api/mode/confirm", { method: "POST" });
      const data = await res.json();
      if (data.ok) {
        log("Mode confirmed. Rollback cancelled.", "success");
        updateRollbackBar({ pending: false });
        await fetchStatus();
      }
    } catch (e) {
      log("ERROR: Could not confirm — " + e.message, "error");
    }
  }

  async function rollbackMode() {
    log("Initiating manual rollback...", "warn");
    try {
      const res = await fetch(API + "/api/mode/rollback", { method: "POST" });
      const data = await res.json();
      if (data.ok) {
        log(`Rolled back to ${data.mode.toUpperCase()}`, "success");
        updateRollbackBar({ pending: false });
        await fetchStatus();
      } else {
        log("ERROR: Rollback failed — " + (data.error || "unknown"), "error");
      }
    } catch (e) {
      log("ERROR: Rollback request failed — " + e.message, "error");
    }
  }

  async function toggleTailscale() {
    const toggle = document.getElementById("ts-toggle");
    const isOn = toggle.classList.contains("on");
    const action = isOn ? "stop" : "start";

    toggle.classList.add("loading");
    log(`Tailscale ${action}ing...`, "warn");

    try {
      const res = await fetch(API + "/api/tailscale", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action })
      });
      const data = await res.json();
      if (data.ok) {
        log(`Tailscale ${action === "start" ? "activated" : "deactivated"}`, "success");
        await fetchStatus();
      } else {
        log(`ERROR: ${data.error}`, "error");
      }
    } catch (e) {
      log(`ERROR: Tailscale toggle failed — ${e.message}`, "error");
    }

    toggle.classList.remove("loading");
  }

  async function switchMode(mode) {
    if (mode === currentMode || switching) return;
    switching = true;

    // UI feedback
    document.getElementById("status-dot").className = "status-dot warn";
    document.getElementById("status-text").innerHTML = `<span class="spinner"></span>SWITCHING TO ${mode.toUpperCase()}...`;
    document.getElementById("card-" + mode).classList.add("switching");
    log(`Switching to ${mode.toUpperCase()} mode...`, "warn");

    try {
      const res = await fetch(API + "/api/mode", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode })
      });
      const data = await res.json();

      if (data.ok) {
        log(`Mode active: ${mode.toUpperCase()}`, "success");
        if (data.rollback && data.rollback.pending) {
          log(`Confirm within ${data.rollback.remainingSec}s or auto-revert to ${data.rollback.target.toUpperCase()}`, "warn");
        }
        await fetchStatus();
      } else {
        log(`ERROR: ${data.error}`, "error");
        document.getElementById("status-text").textContent = "MODE SWITCH FAILED";
      }
    } catch (e) {
      // Network drops during mode switch are expected (routing path changes).
      log(`Reconnecting after mode switch...`, "warn");
      document.getElementById("status-text").innerHTML = `<span class="spinner"></span>RECONNECTING...`;
      let recovered = false;
      for (let i = 0; i < 10; i++) {
        await new Promise(r => setTimeout(r, 3000));
        try { await fetchStatus(); recovered = true; log(`Mode active: ${mode.toUpperCase()}`, "success"); break; } catch (_) {}
      }
      if (!recovered) {
        log(`ERROR: Could not reach API — ${e.message}`, "error");
        document.getElementById("status-text").textContent = "CONNECTION LOST";
        document.getElementById("status-dot").className = "status-dot error";
      }
    }

    document.getElementById("card-" + mode).classList.remove("switching");
    switching = false;
  }

  // ── Support ──────────────────────────────────────────────

  async function runDiagnostics() {
    const btn = document.getElementById("btn-diag");
    const box = document.getElementById("diag-results");
    btn.disabled = true;
    btn.textContent = "RUNNING...";
    box.style.display = "block";
    box.innerHTML = '<div style="color:var(--text-dim);font-size:11px;padding:8px">Running checks...</div>';
    log("Running diagnostics...", "info");

    try {
      const res = await fetch(API + "/api/diagnostics");
      const data = await res.json();
      if (!data.ok) throw new Error(data.error);

      const s = data.summary;
      let html = `<div class="diag-summary ${s.allGood ? "all-good" : "has-issues"}">${s.passed}/${s.total} checks passed${s.allGood ? " — all systems nominal" : ""}</div>`;
      for (const c of data.checks) {
        const cls = c.ok ? (c.warn ? "warn" : "pass") : "fail";
        const icon = c.ok ? (c.warn ? "&#9888;" : "&#10004;") : "&#10008;";
        html += `<div class="diag-item ${cls}"><span class="diag-icon">${icon}</span><span class="diag-name">${esc(c.name)}</span><span class="diag-detail">${esc(c.detail)}</span></div>`;
        if (c.fix) html += `<div class="diag-fix">${esc(c.fix)}</div>`;
      }
      box.innerHTML = html;
      log(`Diagnostics: ${s.passed}/${s.total} passed`, s.allGood ? "success" : "warn");
    } catch (e) {
      box.innerHTML = `<div style="color:var(--red);font-size:11px;padding:8px">Error: ${esc(e.message)}</div>`;
      log("Diagnostics failed: " + e.message, "error");
    }
    btn.disabled = false;
    btn.textContent = "RUN DIAGNOSTICS";
  }

  function toggleTicketForm() {
    const wrap = document.getElementById("ticket-form-wrap");
    wrap.style.display = wrap.style.display === "none" ? "block" : "none";
  }

  const REPAIR_ENDPOINTS = {
    ap:        { url: "/api/hostapd/restart",    label: "RESTART AP",        name: "WiFi AP" },
    dns:       { url: "/api/repair/dns",         label: "RESTART DNS",       name: "DNS stack" },
    wireguard: { url: "/api/repair/wireguard",   label: "RESTART WIREGUARD", name: "WireGuard" },
    tailscale: { url: "/api/repair/tailscale",   label: "RESTART TAILSCALE", name: "Tailscale" },
    firewall:  { url: "/api/repair/firewall",    label: "REAPPLY FIREWALL",  name: "Firewall" },
    flushdns:  { url: "/api/repair/flushdns",    label: "FLUSH DNS CACHE",   name: "DNS cache" },
  };

  async function repairAction(key) {
    const cfg = REPAIR_ENDPOINTS[key];
    if (!cfg) return;
    const btnMap = { ap: "btn-ap", wireguard: "btn-wg", firewall: "btn-fw", dns: "btn-dns", tailscale: "btn-ts", flushdns: "btn-flush" };
    const btnId = btnMap[key] || "btn-" + key;
    const btn = document.getElementById(btnId);
    btn.disabled = true;
    btn.textContent = "WORKING...";
    log(`Restarting ${cfg.name}...`, "warn");

    try {
      const res = await fetch(API + cfg.url, { method: "POST" });
      const data = await res.json();
      if (data.ok) {
        log(`${cfg.name}: ${data.status}`, "success");
      } else {
        log(`${cfg.name} failed: ${data.error || data.status}`, "error");
      }
      // Refresh dashboard status
      await fetchStatus();
    } catch (e) {
      log(`${cfg.name} error: ${e.message}`, "error");
    }

    btn.disabled = false;
    btn.textContent = cfg.label;
  }

  function confirmReboot() {
    const btn = document.getElementById("btn-reboot");
    if (btn.dataset.armed === "true") {
      btn.disabled = true;
      btn.textContent = "REBOOTING...";
      log("Rebooting device...", "error");
      fetch(API + "/api/repair/reboot", { method: "POST" });
      return;
    }
    btn.dataset.armed = "true";
    btn.textContent = "CONFIRM REBOOT?";
    btn.style.borderColor = "var(--red)";
    btn.style.color = "var(--red)";
    log("Press REBOOT again within 5s to confirm", "warn");
    setTimeout(() => {
      btn.dataset.armed = "false";
      btn.textContent = "REBOOT DEVICE";
      btn.style.borderColor = "#ff444466";
      btn.style.color = "#ff8888";
    }, 5000);
  }

  function confirmFactory() {
    const btn = document.getElementById("btn-factory");
    if (btn.dataset.armed === "true") {
      btn.disabled = true;
      btn.textContent = "RESETTING...";
      log("Factory reset in progress...", "error");
      fetch(API + "/api/repair/factory-reset", { method: "POST" });
      return;
    }
    btn.dataset.armed = "true";
    btn.textContent = "CONFIRM RESET?";
    btn.style.borderColor = "var(--red)";
    btn.style.color = "var(--red)";
    log("Press FACTORY RESET again within 5s to confirm — THIS WILL ERASE ALL SETTINGS", "error");
    setTimeout(() => {
      btn.dataset.armed = "false";
      btn.textContent = "FACTORY RESET";
      btn.style.borderColor = "#ff444466";
      btn.style.color = "#ff8888";
    }, 5000);
  }

  async function sendTicket() {
    const desc = document.getElementById("ticket-desc").value.trim();
    const contact = document.getElementById("ticket-contact").value.trim();
    const status = document.getElementById("ticket-status");

    if (desc.length < 10) {
      status.className = "ticket-status err";
      status.textContent = "Description must be at least 10 characters";
      return;
    }

    status.className = "ticket-status";
    status.textContent = "Sending...";
    log("Submitting trouble ticket...", "info");

    try {
      const res = await fetch(API + "/api/ticket", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ description: desc, contact: contact || undefined }),
      });
      const data = await res.json();
      if (data.ok) {
        status.className = "ticket-status ok";
        status.textContent = "Ticket sent successfully";
        document.getElementById("ticket-desc").value = "";
        document.getElementById("ticket-contact").value = "";
        log("Trouble ticket sent", "success");
      } else {
        status.className = "ticket-status err";
        status.textContent = data.error;
        log("Ticket failed: " + data.error, "error");
      }
    } catch (e) {
      status.className = "ticket-status err";
      status.textContent = "Failed to send: " + e.message;
      log("Ticket failed: " + e.message, "error");
    }
  }


  // ── PWA Install ───────────────────────────────────────────

  let deferredInstallPrompt = null;

  // Register service worker for PWA installability (network-only, no caching)
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  }

  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    deferredInstallPrompt = e;
    if (!localStorage.getItem('gp-pwa-dismissed')) {
      document.getElementById('pwa-install-bar').classList.add('visible');
    }
  });

  async function installPwa() {
    if (!deferredInstallPrompt) {
      // Fallback for iOS / browsers without beforeinstallprompt
      const isIos = /iPad|iPhone|iPod/.test(navigator.userAgent);
      if (isIos) {
        alert('Tap the Share button (box with arrow) then "Add to Home Screen"');
      } else {
        alert('Use your browser menu to "Add to Home Screen" or "Install app"');
      }
      return;
    }
    deferredInstallPrompt.prompt();
    const result = await deferredInstallPrompt.userChoice;
    if (result.outcome === 'accepted') {
      log('GhostPort app installed', 'success');
    }
    deferredInstallPrompt = null;
    document.getElementById('pwa-install-bar').classList.remove('visible');
  }

  function dismissPwa() {
    document.getElementById('pwa-install-bar').classList.remove('visible');
    localStorage.setItem('gp-pwa-dismissed', '1');
  }

  // Show iOS hint if in Safari and not already installed
  if (/iPad|iPhone|iPod/.test(navigator.userAgent) && !window.navigator.standalone && !localStorage.getItem('gp-pwa-dismissed')) {
    document.getElementById('pwa-install-bar').classList.add('visible');
  }


  function exitApp() {
    if (window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone) {
      // PWA standalone mode — try to close, fall back to blank page
      window.close();
      setTimeout(() => { window.location.href = 'about:blank'; }, 200);
    } else {
      // Regular browser tab — just close
      window.close();
      setTimeout(() => { history.back(); }, 200);
    }
  }

  // Boot sequence
  log("GhostPort Command Deck v1.1 initializing...", "info");
  log("Establishing connection to Pi...", "info");
  // ── Theme color engine ────────────────────────────────
  let rainbowRAF = null;

  // Old named theme → hex migration
  const THEME_MIGRATE = {
    green: "#39ff8f", purple: "#b44aff", blue: "#44aaff", red: "#ff4466",
    white: "#e0e0e0", cyan: "#00e5ff", amber: "#ffb300", gunmetal: "#8a9099",
  };

  function hsl2hex(h, s, l) {
    h = ((h % 360) + 360) % 360;
    s /= 100; l /= 100;
    const k = n => (n + h / 30) % 12;
    const a = s * Math.min(l, 1 - l);
    const f = n => Math.round(255 * (l - a * Math.max(-1, Math.min(k(n) - 3, 9 - k(n), 1))));
    return "#" + [f(0), f(8), f(4)].map(v => v.toString(16).padStart(2, "0")).join("");
  }

  function hex2hsl(hex) {
    let r = parseInt(hex.slice(1,3), 16) / 255;
    let g = parseInt(hex.slice(3,5), 16) / 255;
    let b = parseInt(hex.slice(5,7), 16) / 255;
    const max = Math.max(r, g, b), min = Math.min(r, g, b);
    let h, s, l = (max + min) / 2;
    if (max === min) { h = 0; s = 0; }
    else {
      const d = max - min;
      s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
      if (max === r) h = ((g - b) / d + (g < b ? 6 : 0)) / 6;
      else if (max === g) h = ((b - r) / d + 2) / 6;
      else h = ((r - g) / d + 4) / 6;
    }
    return [h * 360, s * 100, l * 100];
  }

  // Derive full theme from hue + saturation + optional breathing phase
  function hueToTheme(hue, breath, sat) {
    const bri = breath !== undefined ? (0.92 + breath * 0.08) : 1;
    const s = sat !== undefined ? sat : 100;
    const accent = hsl2hex(hue, s, 60 * bri);
    const dim = hsl2hex(hue, s * 0.5, 22 * bri);
    const bgSat = Math.min(s, 30);
    const txtSat = Math.min(s, 20);
    return {
      accent, dim, glow: accent + "44", a20: accent + "33", a33: accent + "55", a13: accent + "22",
      bg: hsl2hex(hue, bgSat, 2.5), bg2: hsl2hex(hue, bgSat * 0.83, 4.5), bg3: hsl2hex(hue, bgSat * 0.67, 7),
      border: hsl2hex(hue, txtSat, 12),
      text: hsl2hex(hue, txtSat, 80 * bri), textDim: hsl2hex(hue, txtSat * 0.9, 35 * bri), textFaint: hsl2hex(hue, txtSat * 0.75, 20),
    };
  }

  function colorToTheme(hex) {
    const [h, s] = hex2hsl(hex);
    return hueToTheme(h, undefined, s);
  }

  function applyThemeVars(t) {
    const r = document.documentElement.style;
    r.setProperty("--green", t.accent); r.setProperty("--green-dim", t.dim);
    r.setProperty("--green-glow", t.glow); r.setProperty("--green-20", t.a20);
    r.setProperty("--green-33", t.a33); r.setProperty("--green-13", t.a13);
    r.setProperty("--bg", t.bg); r.setProperty("--bg2", t.bg2); r.setProperty("--bg3", t.bg3);
    r.setProperty("--border", t.border); r.setProperty("--text", t.text);
    r.setProperty("--text-dim", t.textDim); r.setProperty("--text-faint", t.textFaint);
  }

  function setColorTheme(hex) {
    stopRainbow();
    if (hex === "#39ff8f") {
      // Default green — clear overrides so hand-tuned :root values show
      ["--green","--green-dim","--green-glow","--green-20","--green-33","--green-13",
       "--bg","--bg2","--bg3","--border","--text","--text-dim","--text-faint"
      ].forEach(v => document.documentElement.style.removeProperty(v));
    } else {
      applyThemeVars(colorToTheme(hex));
    }
    localStorage.setItem("gp-theme", hex);
    const picker = document.getElementById("theme-picker");
    if (picker) picker.value = hex;
    const label = document.getElementById("theme-label");
    if (label) label.textContent = hex.toUpperCase();
    const btn = document.getElementById("rgb-btn");
    if (btn) btn.classList.remove("active");
    // Update swatch active states
    document.querySelectorAll(".theme-swatch").forEach(s => {
      s.classList.toggle("active", s.dataset.color === hex);
    });
  }

  // ── RGB breathing engine ──────────────────────────────
  function startRainbow() {
    if (rainbowRAF) return;
    localStorage.setItem("gp-theme", "rainbow");
    const btn = document.getElementById("rgb-btn");
    if (btn) btn.classList.add("active");
    const label = document.getElementById("theme-label");
    if (label) label.textContent = "RGB CYCLE";
    document.querySelectorAll(".theme-swatch").forEach(s => s.classList.remove("active"));
    const speed = 0.015, breathSpeed = 0.002;
    let hue = 0, breathPhase = 0, last = 0;
    function frame(ts) {
      if (ts - last < 33) { rainbowRAF = requestAnimationFrame(frame); return; }
      last = ts;
      hue = (hue + speed * 33) % 360;
      breathPhase += breathSpeed * 33;
      const breath = (Math.sin(breathPhase) + 1) / 2;
      applyThemeVars(hueToTheme(hue, breath));
      const picker = document.getElementById("theme-picker");
      if (picker) picker.value = hsl2hex(hue, 100, 60);
      rainbowRAF = requestAnimationFrame(frame);
    }
    rainbowRAF = requestAnimationFrame(frame);
  }

  function stopRainbow() {
    if (rainbowRAF) { cancelAnimationFrame(rainbowRAF); rainbowRAF = null; }
  }

  function toggleRainbow() {
    if (rainbowRAF) {
      const picker = document.getElementById("theme-picker");
      stopRainbow();
      if (picker) setColorTheme(picker.value);
    } else {
      startRainbow();
    }
  }

  // Wire up color picker
  const picker = document.getElementById("theme-picker");
  if (picker) {
    picker.addEventListener("input", (e) => setColorTheme(e.target.value));
  }

  // Apply saved theme on load (with migration from old named themes)
  let savedTheme = localStorage.getItem("gp-theme") || "#39ff8f";
  if (THEME_MIGRATE[savedTheme]) {
    savedTheme = THEME_MIGRATE[savedTheme];
    localStorage.setItem("gp-theme", savedTheme);
  }
  if (savedTheme === "rainbow") {
    startRainbow();
  } else {
    const hex = savedTheme.startsWith("#") ? savedTheme : "#39ff8f";
    setColorTheme(hex);
  }

  fetchStatus().then(() => {
    log("All systems nominal. Welcome aboard, Captain.", "success");
  });

  // Poll status every 5 seconds (faster to catch rollback countdown)
  setInterval(fetchStatus, 5000);

  // ── Traffic Monitor ──────────────────────────────────────────

  // DECISION: Poll every 2s as requested. The /rate endpoint itself takes ~1s (server-side delta),
  // so effective update cycle is ~3s. This avoids overlapping requests.
  const TM_IFACE_LABELS = { eth0: "WAN", wlan0: "AP", wg0: "Control", wg1: "Data Tunnel", tailscale0: "Tailscale" };
  const TM_IFACE_ORDER = ["eth0", "wlan0", "wg1", "wg0", "tailscale0"];
  let tmInterval = null;
  let tmFetching = false;

  function isBandwidthVisible() {
    const section = document.getElementById("bandwidth-section");
    if (!section) return false;
    // Hidden if simple mode is active (tech-only elements are display:none)
    if (document.body.classList.contains("simple-mode")) return false;
    // Check if element is visible in viewport (not scrolled away — but we poll anyway if section exists)
    return section.offsetParent !== null;
  }

  function formatTmRate(humanStr) {
    // humanStr is like "1.23 Mbps" or "456 Kbps" or "0 bps"
    if (!humanStr) return { val: "0", unit: "bps" };
    const parts = humanStr.split(" ");
    return { val: parts[0] || "0", unit: parts[1] || "bps" };
  }

  async function fetchTrafficData() {
    if (tmFetching) return; // skip if previous request still in flight
    tmFetching = true;
    const dot = document.getElementById("tm-dot");
    const rxEl = document.getElementById("tm-rx-total");
    const txEl = document.getElementById("tm-tx-total");
    const ifacesEl = document.getElementById("tm-ifaces");
    const errorsEl = document.getElementById("tm-errors");

    try {
      const res = await fetch(API + "/api/tools/bandwidth/rate");
      if (!res.ok) throw new Error("HTTP " + res.status);
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || "unknown");

      // Update live dot
      if (dot) { dot.classList.add("live"); dot.classList.remove("offline"); }

      // Total throughput
      const rxTotal = formatTmRate(data.total.rx_rate_human);
      const txTotal = formatTmRate(data.total.tx_rate_human);
      if (rxEl) rxEl.innerHTML = esc(rxTotal.val) + '<span class="tm-total-unit">' + esc(rxTotal.unit) + '</span>';
      if (txEl) txEl.innerHTML = esc(txTotal.val) + '<span class="tm-total-unit">' + esc(txTotal.unit) + '</span>';

      // Find max rate for bar scaling
      let maxRate = 0;
      for (const iface of TM_IFACE_ORDER) {
        const d = data.interfaces[iface];
        if (!d) continue;
        maxRate = Math.max(maxRate, d.rx_rate_bps, d.tx_rate_bps);
      }
      if (maxRate === 0) maxRate = 1; // avoid division by zero

      // Per-interface rows
      let html = "";
      let errorParts = [];
      for (const iface of TM_IFACE_ORDER) {
        const d = data.interfaces[iface];
        const label = TM_IFACE_LABELS[iface] || iface;
        const isDim = !d || (d.rx_rate_bps === 0 && d.tx_rate_bps === 0);
        const dimCls = isDim ? " dim" : "";
        const rxH = d ? formatTmRate(d.rx_rate_human) : { val: "---", unit: "" };
        const txH = d ? formatTmRate(d.tx_rate_human) : { val: "---", unit: "" };
        const barPct = d ? Math.max(d.rx_rate_bps, d.tx_rate_bps) / maxRate * 100 : 0;

        html += '<div class="tm-iface-row">';
        html += '<div class="tm-iface-name' + dimCls + '">' + esc(label) + '<br><span class="tm-iface-label">' + esc(iface) + '</span></div>';
        html += '<div class="tm-bar-wrap"><div class="tm-bar' + dimCls + '" style="width:' + barPct.toFixed(1) + '%"></div></div>';
        html += '<div class="tm-rate tm-rate-rx' + dimCls + '"><span class="tm-arrow">▼</span>' + esc(rxH.val) + ' ' + esc(rxH.unit) + '</div>';
        html += '<div class="tm-rate tm-rate-tx' + dimCls + '"><span class="tm-arrow">▲</span>' + esc(txH.val) + ' ' + esc(txH.unit) + '</div>';
        html += '</div>';

        // Collect errors/drops (only non-zero)
        if (d) {
          const totalErr = (d.rx_errors || 0) + (d.tx_errors || 0);
          const totalDrop = (d.rx_dropped || 0) + (d.tx_dropped || 0);
          if (totalErr > 0) errorParts.push('<span class="has-errors">' + esc(label) + ': ' + totalErr + ' err</span>');
          if (totalDrop > 0) errorParts.push('<span class="has-errors">' + esc(label) + ': ' + totalDrop + ' drop</span>');
        }
      }
      if (ifacesEl) ifacesEl.innerHTML = html;

      // Error/drop counters
      if (errorsEl) {
        if (errorParts.length > 0) {
          errorsEl.innerHTML = errorParts.join("");
          errorsEl.style.display = "block";
        } else {
          errorsEl.style.display = "none";
        }
      }
    } catch (e) {
      // Show offline state
      if (dot) { dot.classList.remove("live"); dot.classList.add("offline"); }
      if (rxEl) rxEl.textContent = "---";
      if (txEl) txEl.textContent = "---";
      if (ifacesEl) ifacesEl.innerHTML = '<div class="tm-offline">OFFLINE — waiting for data</div>';
      if (errorsEl) errorsEl.style.display = "none";
    }
    tmFetching = false;
  }

  function startTrafficMonitor() {
    if (tmInterval) return;
    fetchTrafficData(); // initial fetch
    tmInterval = setInterval(function() {
      // DECISION: Only poll when bandwidth section is visible to save CPU/network on Pi
      if (isBandwidthVisible()) {
        fetchTrafficData();
      }
    }, 2000);
  }

  function stopTrafficMonitor() {
    if (tmInterval) { clearInterval(tmInterval); tmInterval = null; }
  }

  // Start traffic monitor after initial status fetch completes
  // DECISION: Small delay (2s) so the dashboard loads first, then traffic monitor kicks in
  setTimeout(startTrafficMonitor, 2000);

  // ── WiFi Network ──────────────────────────────────────────

  function toggleWifiPassVisibility() {
    const input = document.getElementById("wifi-pass");
    const btn = document.getElementById("wifi-pass-eye");
    if (input.type === "password") {
      input.type = "text";
      btn.textContent = "HIDE";
    } else {
      input.type = "password";
      btn.textContent = "SHOW";
    }
  }

  async function loadWifiConfig() {
    try {
      const res = await fetch(API + "/api/hostapd/config");
      const data = await res.json();
      if (data.ok) {
        document.getElementById("wifi-ssid").value = data.ssid;
        document.getElementById("wifi-pass").value = data.passphrase;
      }
    } catch (e) { /* silent */ }
  }

  async function saveWifiConfig() {
    const ssid = document.getElementById("wifi-ssid").value.trim();
    const passphrase = document.getElementById("wifi-pass").value;
    const status = document.getElementById("wifi-status");

    if (!ssid && !passphrase) {
      status.style.color = "var(--red)";
      status.textContent = "Enter an SSID or password to change";
      return;
    }
    if (passphrase && (passphrase.length < 8 || passphrase.length > 63)) {
      status.style.color = "var(--red)";
      status.textContent = "Password must be 8-63 characters";
      return;
    }

    status.style.color = "var(--text-dim)";
    status.textContent = "Saving and restarting AP...";
    log("Updating WiFi network config...", "warn");

    try {
      const body = {};
      if (ssid) body.ssid = ssid;
      if (passphrase) body.passphrase = passphrase;

      const res = await fetch(API + "/api/hostapd/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (data.ok) {
        status.style.color = "var(--green)";
        status.textContent = data.status;
        log("WiFi network updated — devices must reconnect", "success");
      } else {
        status.style.color = "var(--red)";
        status.textContent = data.error;
        log("WiFi update failed: " + data.error, "error");
      }
    } catch (e) {
      status.style.color = "var(--red)";
      status.textContent = "Failed: " + e.message;
      log("WiFi update error: " + e.message, "error");
    }
  }


  // ── Family Shield ──────────────────────────────────────
  // All functions on window.* so onclick handlers always find them

  (function() {
    var fs = { on: false, cats: {}, devs: [], busy: false };

    function tog(id, on) {
      var el = document.getElementById(id);
      if (el) { if (on) el.classList.add('on'); else el.classList.remove('on'); }
      console.log('[FS] tog', id, on);
    }

    function updateUI() {
      tog('tog-family-shield', fs.on);
      var cats = ['adult','gambling','facebook','tiktok','twitter','acr','dataBrokers'];
      for (var i = 0; i < cats.length; i++) {
        var c = cats[i];
        var card = document.getElementById('fs-cat-' + (c === 'twitter' ? 'x' : c));
        if (card) {
          card.style.opacity = fs.on ? '1' : '0.35';
          card.style.pointerEvents = fs.on ? 'auto' : 'none';
        }
        tog('tog-fs-' + c, !!fs.cats[c]);
        var st = document.getElementById('fs-st-' + c);
        if (st) {
          st.textContent = fs.cats[c] ? 'BLOCKED' : 'ALLOWED';
          st.className = 'fs-cat-status' + (fs.cats[c] ? ' blocked' : '');
        }
      }
      renderDevs();
    }

    function renderDevs() {
      var list = document.getElementById('fs-device-list');
      if (!list) return;
      if (!fs.devs || !fs.devs.length) {
        list.innerHTML = '<div style="font-size:10px;color:var(--text-faint)">No devices — click DISCOVER</div>';
        return;
      }
      var h = '';
      for (var i = 0; i < fs.devs.length; i++) {
        var d = fs.devs[i];
        var name = esc(d.name || d.hostname || 'Unknown');
        var ip = esc(d.ip);
        var shielded = !!d.shielded;
        var dis = fs.on ? '' : ' disabled';
        h += '<div style="display:flex;align-items:center;gap:6px;padding:5px 0;border-bottom:1px solid var(--bg3,#1a1a1a);font-size:10px">';
        h += '<div style="flex:1"><div style="color:var(--text)">' + name + '</div><div style="color:var(--text-faint)">' + ip + '</div></div>';
        h += '<span style="font-size:8px;padding:2px 6px;border-radius:8px;background:' + (shielded ? 'rgba(0,200,83,0.15);color:#0c3' : 'rgba(255,68,68,0.15);color:#f44') + '">' + (shielded ? 'SHIELDED' : 'EXPOSED') + '</span>';
        h += '<button class="arsenal-btn" style="font-size:9px;padding:3px 8px"' + dis + ' onclick="window._fsToggleDev(\'' + esc(d.ip) + '\', ' + (!shielded) + ')">' + (shielded ? 'UNSHIELD' : 'SHIELD') + '</button>';
        h += '</div>';
      }
      list.innerHTML = h;
    }

    async function load() {
      if (fs.busy) { console.log('[FS] load() skipped — busy'); return; }
      try {
        var res = await fetch('/api/family-shield');
        var data = await res.json();
        console.log('[FS] load() response:', JSON.stringify(data).substring(0, 200));
        if (data.ok) {
          fs.on = !!data.enabled;
          fs.cats = data.categories || {};
          fs.devs = data.devices || [];
          updateUI();
        }
      } catch(e) { console.error('[FS] load() error:', e); }
    }

    window._fsToggleMaster = async function() {
      console.log('[FS] _fsToggleMaster called, fs.on =', fs.on);
      fs.busy = true;
      var newState = !fs.on;
      tog('tog-family-shield', newState);
      log('Family Shield ' + (newState ? 'enabling' : 'disabling') + '...', 'warn');
      try {
        var res = await fetch('/api/family-shield/toggle', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({enabled: newState})
        });
        var data = await res.json();
        console.log('[FS] toggle response:', JSON.stringify(data));
        if (data.ok) {
          fs.on = newState;
          log('Family Shield ' + (newState ? 'enabled' : 'disabled'), 'success');
          updateUI();
        } else {
          tog('tog-family-shield', fs.on);
          log('Family Shield failed: ' + (data.error || 'unknown'), 'error');
        }
      } catch(e) {
        console.error('[FS] toggle error:', e);
        tog('tog-family-shield', fs.on);
        log('Family Shield error: ' + e.message, 'error');
      }
      fs.busy = false;
    };

    // ACR first-enable warning. Stored acknowledgment means we don't
    // re-prompt on subsequent toggles (user already knows what it breaks).
    var ACR_ACK_KEY = "gp.acr.acknowledged";
    window._acrPendingCat = null;
    window.acrConfirm = function() {
      try { localStorage.setItem(ACR_ACK_KEY, "1"); } catch(e) {}
      var m = document.getElementById("acr-modal");
      if (m) m.classList.remove("visible");
      if (window._acrPendingCat) {
        var c = window._acrPendingCat;
        window._acrPendingCat = null;
        window._fsApplyCat(c);
      }
    };
    window.acrCancel = function() {
      var m = document.getElementById("acr-modal");
      if (m) m.classList.remove("visible");
      // Revert the toggle visual to its pre-click state
      if (window._acrPendingCat) {
        tog('tog-fs-' + window._acrPendingCat, fs.cats[window._acrPendingCat]);
        window._acrPendingCat = null;
      }
    };

    window._fsToggleCat = async function(cat) {
      console.log('[FS] _fsToggleCat called, cat =', cat, 'fs.on =', fs.on);
      if (!fs.on) return;
      var newState = !fs.cats[cat];

      // ACR first-enable interstitial: if user is turning ACR ON and has
      // never acknowledged the breakage warning, show the modal and defer
      // the actual toggle until they confirm.
      if (cat === 'acr' && newState) {
        var acked = false;
        try { acked = localStorage.getItem(ACR_ACK_KEY) === "1"; } catch(e) {}
        if (!acked) {
          window._acrPendingCat = cat;
          // Preview the toggle going ON so the user sees their click took effect
          tog('tog-fs-' + cat, true);
          var m = document.getElementById("acr-modal");
          if (m) m.classList.add("visible");
          return;
        }
      }
      window._fsApplyCat(cat);
    };

    window._fsApplyCat = async function(cat) {
      fs.busy = true;
      var newState = !fs.cats[cat];
      tog('tog-fs-' + cat, newState);
      var cats = Object.assign({}, fs.cats);
      cats[cat] = newState;
      log(cat + ' ' + (newState ? 'blocking' : 'allowing') + '...', 'warn');
      try {
        var res = await fetch('/api/family-shield/categories', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(cats)
        });
        var data = await res.json();
        if (data.ok) {
          fs.cats = cats;
          log(cat + ' ' + (newState ? 'blocked' : 'allowed'), 'success');
          updateUI();
        } else {
          tog('tog-fs-' + cat, fs.cats[cat]); // revert
          log('Failed: ' + (data.error || 'unknown'), 'error');
        }
      } catch(e) {
        tog('tog-fs-' + cat, fs.cats[cat]); // revert
        log('Error: ' + e.message, 'error');
      }
      fs.busy = false;
    };

    window._fsToggleDev = async function(ip, shielded) {
      if (!fs.on) return;
      log((shielded ? 'Shielding' : 'Unshielding') + ' ' + ip + '...', 'warn');
      try {
        var res = await fetch('/api/family-shield/devices', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ip: ip, shielded: shielded})
        });
        var data = await res.json();
        if (data.ok) {
          for (var i = 0; i < fs.devs.length; i++) {
            if (fs.devs[i].ip === ip) fs.devs[i].shielded = shielded;
          }
          log(ip + ' ' + (shielded ? 'shielded' : 'unshielded'), 'success');
          renderDevs();
        } else {
          log('Failed: ' + (data.error || 'unknown'), 'error');
        }
      } catch(e) {
        log('Error: ' + e.message, 'error');
      }
    };

    window._fsDiscover = async function() {
      var btn = document.getElementById('btn-fs-discover');
      if (btn) { btn.disabled = true; btn.textContent = 'SCANNING...'; }
      log('Scanning network...', 'info');
      try {
        var res = await fetch('/api/family-shield/discover');
        var data = await res.json();
        if (data.ok && data.devices) {
          // Merge discovered into existing
          for (var i = 0; i < data.devices.length; i++) {
            var d = data.devices[i];
            if (!fs.devs.find(function(x) { return x.ip === d.ip; })) {
              fs.devs.push(d);
            }
          }
          log('Found ' + data.devices.length + ' device(s)', 'success');
          renderDevs();
        }
      } catch(e) {
        log('Scan failed: ' + e.message, 'error');
      }
      if (btn) { btn.disabled = false; btn.textContent = 'DISCOVER'; }
    };

    // Init
    load();
    setInterval(load, 30000);
  })();

  loadWifiConfig();

// ── Event listeners (replaces inline handlers for CSP compliance) ──
(function() {
  var e_el_1 = document.getElementById("el-1");
  if (e_el_1) e_el_1.addEventListener("click", function() { this.classList.add("glitch"); setTimeout(function() { document.getElementById("el-1").classList.remove("glitch"); }, 600); });
  var e = document.getElementById("btn-2");
  if (e) e.addEventListener("click", function() { exitApp(); });
  var e = document.getElementById("el-3");
  if (e) e.addEventListener("error", function() { this.style.display = "none"; });
  var e = document.getElementById("btn-4");
  if (e) e.addEventListener("click", function() { installPwa(); });
  var e = document.getElementById("btn-5");
  if (e) e.addEventListener("click", function() { dismissPwa(); });
  var e = document.getElementById("btn-6");
  if (e) e.addEventListener("click", function() { confirmMode(); });
  var e = document.getElementById("btn-7");
  if (e) e.addEventListener("click", function() { rollbackMode(); });
  var e = document.getElementById("card-isp");
  if (e) e.addEventListener("click", function() { switchMode('isp'); });
  var e = document.getElementById("card-zerotrust");
  if (e) e.addEventListener("click", function() { switchMode('zerotrust'); });
  var e = document.getElementById("card-doublehop");
  if (e) e.addEventListener("click", function() { switchMode('doublehop'); });
  var e = document.getElementById("card-zhop");
  if (e) e.addEventListener("click", function() { switchMode('zhop'); });
  var e = document.getElementById("ts-toggle");
  if (e) e.addEventListener("click", function() { toggleTailscale(); });
  var e = document.getElementById("btn-diag");
  if (e) e.addEventListener("click", function() { runDiagnostics(); });
  var e = document.getElementById("btn-ticket");
  if (e) e.addEventListener("click", function() { toggleTicketForm(); });
  var e = document.getElementById("btn-ap");
  if (e) e.addEventListener("click", function() { repairAction('ap'); });
  var e = document.getElementById("btn-dns");
  if (e) e.addEventListener("click", function() { repairAction('dns'); });
  var e = document.getElementById("btn-wg");
  if (e) e.addEventListener("click", function() { repairAction('wireguard'); });
  var e = document.getElementById("btn-ts");
  if (e) e.addEventListener("click", function() { repairAction('tailscale'); });
  var e = document.getElementById("btn-fw");
  if (e) e.addEventListener("click", function() { repairAction('firewall'); });
  var e = document.getElementById("btn-flush");
  if (e) e.addEventListener("click", function() { repairAction('flushdns'); });
  var e = document.getElementById("btn-reboot");
  if (e) e.addEventListener("click", function() { confirmReboot(); });
  var e = document.getElementById("btn-factory");
  if (e) e.addEventListener("click", function() { confirmFactory(); });
  var e = document.getElementById("btn-8");
  if (e) e.addEventListener("click", function() { sendTicket(); });
  var e = document.getElementById("btn-pihole-setup");
  if (e) e.addEventListener("click", function() { setupPihole(); });
  var e = document.getElementById("tog-killswitch");
  if (e) e.addEventListener("click", function() { arsenalToggle('killswitch'); });
  var e = document.getElementById("tog-ks-auto");
  if (e) e.addEventListener("click", function() { toggleKsAuto(); });
  var e = document.getElementById("tog-encrypteddns");
  if (e) e.addEventListener("click", function() { arsenalToggle('encrypteddns'); });
  var e = document.getElementById("tog-quicblock");
  if (e) e.addEventListener("click", function() { arsenalToggle('quicblock'); });
  var e = document.getElementById("tog-tcpscrub");
  if (e) e.addEventListener("click", function() { arsenalToggle('tcpscrub'); });
  var e = document.getElementById("gm-check");
  if (e) e.addEventListener("click", function() { if (typeof toggleGhostMode === 'function') toggleGhostMode(); });
  var e = document.getElementById("enemies-header");
  if (e) {
    e.addEventListener("click", function() { if (typeof toggleEnemies === 'function') toggleEnemies(); });
    e.addEventListener("keydown", function(ev) {
      if (ev.key === " " || ev.key === "Enter") { ev.preventDefault(); toggleEnemies(); }
    });
  }
  var e = document.getElementById("btn-dnstest");
  if (e) e.addEventListener("click", function() { runDnsTest(); });
  var e = document.getElementById("tog-macrandom");
  if (e) e.addEventListener("click", function() { arsenalToggle('macrandom'); });
  var e = document.getElementById("tog-terminalmode");
  if (e) e.addEventListener("click", function() { arsenalToggle('terminalmode'); });
  var e = document.getElementById("sel-blockfreq");
  if (e) e.addEventListener("change", function() { setBlockFreq(this.value); });
  var e = document.getElementById("btn-gravity");
  if (e) e.addEventListener("click", function() { updateGravity(); });
  var e = document.getElementById("btn-9");
  if (e) e.addEventListener("click", function() { submitDomain('deny'); });
  var e = document.getElementById("btn-10");
  if (e) e.addEventListener("click", function() { submitDomain('allow'); });
  var e = document.getElementById("btn-11");
  if (e) e.addEventListener("click", function() { addSchedule(); });
  var e = document.getElementById("btn-speedtest");
  if (e) e.addEventListener("click", function() { runSpeedTest(); });
  var e = document.getElementById("btn-pingtest");
  if (e) e.addEventListener("click", function() { runPingTest(); });
  var e = document.getElementById("btn-ipleak");
  if (e) e.addEventListener("click", function() { runIpLeak(); });
  var e = document.getElementById("btn-securityscan");
  if (e) e.addEventListener("click", function() { runSecurityScan(); });
  var e = document.getElementById("btn-blocked");
  if (e) e.addEventListener("click", function() { fetchBlocked(); });
  var e = document.getElementById("btn-12");
  if (e) e.addEventListener("click", function() { downloadBackup(); });
  var e = document.getElementById("el-13");
  if (e) e.addEventListener("change", function(ev) { importBackup(ev); });
  var e = document.getElementById("btn-update");
  if (e) e.addEventListener("click", function() { confirmUpdate(); });
  var e = document.getElementById("tog-family-shield");
  if (e) e.addEventListener("click", function() { window._fsToggleMaster(); });
  var e = document.getElementById("fs-cat-adult");
  if (e) e.addEventListener("click", function() { window._fsToggleCat('adult'); });
  var e = document.getElementById("fs-cat-gambling");
  if (e) e.addEventListener("click", function() { window._fsToggleCat('gambling'); });
  var e = document.getElementById("fs-cat-facebook");
  if (e) e.addEventListener("click", function() { window._fsToggleCat('facebook'); });
  var e = document.getElementById("fs-cat-tiktok");
  if (e) e.addEventListener("click", function() { window._fsToggleCat('tiktok'); });
  var e = document.getElementById("fs-cat-x");
  if (e) e.addEventListener("click", function() { window._fsToggleCat('twitter'); });
  var e = document.getElementById("fs-cat-acr");
  if (e) e.addEventListener("click", function() { window._fsToggleCat('acr'); });
  var e = document.getElementById("fs-cat-dataBrokers");
  if (e) e.addEventListener("click", function() { window._fsToggleCat('dataBrokers'); });
  var e = document.getElementById("btn-fs-discover");
  if (e) e.addEventListener("click", function() { window._fsDiscover(); });
  var e = document.getElementById("btn-14");
  if (e) e.addEventListener("click", function() { switchWgTab('guided'); });
  var e = document.getElementById("btn-15");
  if (e) e.addEventListener("click", function() { switchWgTab('form'); });
  var e = document.getElementById("btn-16");
  if (e) e.addEventListener("click", function() { switchWgTab('advanced'); });
  var e = document.getElementById("wg-provider");
  if (e) e.addEventListener("change", function() { showProviderGuide(); });
  var e = document.getElementById("el-17");
  if (e) e.addEventListener("change", function(ev) { uploadWgConfig(ev); });
  var e = document.getElementById("btn-18");
  if (e) e.addEventListener("click", function() { switchWgTab('advanced'); });
  var e = document.getElementById("btn-19");
  if (e) e.addEventListener("click", function() { saveWgForm(); });
  var e = document.getElementById("btn-20");
  if (e) e.addEventListener("click", function() { saveWgConfig(); });
  var e = document.getElementById("el-21");
  if (e) e.addEventListener("change", function(ev) { uploadWgConfig(ev); });
  var e = document.getElementById("btn-wg-restore");
  if (e) e.addEventListener("click", function() { restoreWgConfig(); });
  var e = document.getElementById("btn-22");
  if (e) e.addEventListener("click", function() { logoutSession(); });
  var e = document.getElementById("btn-23");
  if (e) e.addEventListener("click", function() { changePasscode(); });
  var e = document.getElementById("btn-24");
  if (e) e.addEventListener("click", function() { resetPiholePassword(); });
  var e = document.getElementById("wifi-pass-eye");
  if (e) e.addEventListener("click", function() { toggleWifiPassVisibility(); });
  var e = document.getElementById("btn-25");
  if (e) e.addEventListener("click", function() { saveWifiConfig(); });
  var e = document.getElementById("rgb-btn");
  if (e) e.addEventListener("click", function() { toggleRainbow(); });
  var e = document.getElementById("ads-range");
  if (e) e.addEventListener("change", function() { fetchAdsRange(); });

  // Theme swatches — event delegation
  var swatches = document.querySelector(".theme-swatches");
  if (swatches) swatches.addEventListener("click", function(ev) {
    var sw = ev.target.closest(".theme-swatch");
    if (sw && sw.dataset.color) setColorTheme(sw.dataset.color);
  });
})();

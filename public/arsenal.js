/**
 * GhostPort Arsenal — UI Logic
 * Manages security tool toggles, DNS testing, client display, and scheduling
 */

function escapeHtml(str) {
  if (str == null) return "";
  return String(str).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#39;");
}

const ARSENAL_API = "";
const DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

// ── Toggle helpers ──────────────────────────────────────────

function setToggle(id, on) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.toggle("on", on);
}

// T-0066: gate a toggle from being interactable when the underlying feature
// is mode-incompatible (e.g. Kill Switch outside tunnel modes) or
// mode-managed (e.g. QUIC Block hard-coded in tunnel-mode nft profiles).
function setToggleEnabled(id, enabled, reason) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.toggle("disabled", !enabled);
  el.style.pointerEvents = enabled ? "" : "none";
  el.style.opacity = enabled ? "" : "0.45";
  if (reason) el.title = enabled ? "" : reason;
}

function setToggleLoading(id, loading) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.toggle("loading", loading);
}

// ── Arsenal status polling ──────────────────────────────────

function updateArsenalUI(data) {
  // Kill Switch — only meaningful in tunnel modes (doublehop/zhop). Outside
  // those modes, killswitch monitor early-returns; toggling silently no-ops.
  // T-0066: gate the UI by killSwitchApplicable so the toggle doesn't lie.
  setToggle("tog-killswitch", data.killSwitch);
  setToggle("tog-ks-auto", data.killSwitchAuto !== false);
  setToggleEnabled("tog-killswitch", data.killSwitchApplicable !== false,
    "Kill Switch monitors VPN health — only available in DoubleHop / ZHop modes");
  setToggleEnabled("tog-ks-auto", data.killSwitchApplicable !== false,
    "Auto-trip requires VPN monitoring — only available in tunnel modes");
  const ksLockNote = document.getElementById("ks-lock-note");
  if (ksLockNote) ksLockNote.style.display = (data.killSwitchApplicable === false) ? "block" : "none";
  const ksStatus = document.getElementById("ks-status");
  if (data.killSwitchApplicable === false) {
    ksStatus.className = "arsenal-status off";
    ksStatus.textContent = "N/A IN " + (data.mode || "?").toUpperCase();
  } else if (data.killSwitchTripped) {
    ksStatus.className = "arsenal-status tripped";
    ksStatus.textContent = data.dnsLeakDetected ? "TRIPPED — DNS LEAK" : "TRIPPED — VPN DOWN";
  } else if (data.killSwitch) {
    ksStatus.className = "arsenal-status on";
    ksStatus.textContent = "ARMED";
  } else {
    ksStatus.className = "arsenal-status off";
    ksStatus.textContent = "OFF";
  }

  // DNS Leak Alert Banner
  const leakAlert = document.getElementById("dns-leak-alert");
  const leakAction = document.getElementById("dns-leak-action");
  if (data.dnsLeakDetected) {
    leakAlert.classList.add("visible");
    if (data.killSwitchTripped) {
      leakAction.textContent = "Kill switch has been activated.";
    } else if (data.killSwitch && data.killSwitchAuto) {
      leakAction.textContent = "Kill switch will auto-trip.";
    } else {
      leakAction.textContent = "Enable kill switch to protect your traffic.";
    }
  } else {
    leakAlert.classList.remove("visible");
  }

  // QUIC Block — rule is hardcoded in zerotrust/doublehop/zhop nft profiles,
  // so the user-toggle is only authoritative in ISP mode. T-0066: gate the
  // UI by quicBlockManaged so the toggle doesn't lie about a ghost rule.
  setToggle("tog-quicblock", data.quicBlock);
  setToggleEnabled("tog-quicblock", !data.quicBlockManaged,
    "QUIC blocking is enforced by the current mode profile — toggle disabled");
  const quicLockNote = document.getElementById("quic-lock-note");
  if (quicLockNote) quicLockNote.style.display = data.quicBlockManaged ? "block" : "none";
  const quicStatus = document.getElementById("quic-status");
  if (quicStatus) {
    if (data.quicBlockManaged) {
      quicStatus.className = "arsenal-status on";
      quicStatus.textContent = "MODE-MANAGED";
    } else {
      quicStatus.className = "arsenal-status " + (data.quicBlock ? "on" : "off");
      quicStatus.textContent = data.quicBlock ? "BLOCKING" : "OFF";
    }
  }

  // TCP Fingerprint Scrub
  setToggle("tog-tcpscrub", data.tcpScrub);
  const scrubStatus = document.getElementById("tcpscrub-status");
  if (scrubStatus) {
    scrubStatus.className = "arsenal-status " + (data.tcpScrub ? "on" : "off");
    scrubStatus.textContent = data.tcpScrub ? "SCRUBBING" : "OFF";
  }

  // DNS Protection (read-only — mode section is the source of truth)
  const ednsStatus = document.getElementById("edns-status");
  const ednsDesc = document.getElementById("edns-desc");
  if (ednsStatus && ednsDesc) {
    const map = {
      doh:         { cls: "on",      label: "DoH",         desc: "Encrypted DNS via Cloudflare. Default on ISP / Zero Trust." },
      tunnel:      { cls: "on",      label: "TUNNEL",      desc: "DNS encrypted through the WireGuard tunnel to the GhostPort resolver." },
      dreadnought: { cls: "on",      label: "DREADNOUGHT", desc: "Recursive lookups to root servers — no Cloudflare middleman, plaintext on wire." },
      plaintext:   { cls: "tripped", label: "PLAINTEXT",   desc: "Unencrypted queries to public resolvers. Debug only — not recommended." },
    };
    const m = map[data.dnsStatus] || map.doh;
    ednsStatus.className = "arsenal-status " + m.cls;
    ednsStatus.textContent = m.label;
    ednsDesc.textContent = m.desc;
  }

  // Dreadnought checkbox state (mode section row)
  updateDreadnought(data);
  // Ghost Mode checkbox state (mirrors Dreadnought pattern, tunnel modes only)
  updateGhostMode(data);

  // MAC Randomization
  setToggle("tog-macrandom", data.macRandomization);
  const macStatus = document.getElementById("mac-status");
  macStatus.className = "arsenal-status " + (data.macRandomization ? "on" : "off");
  macStatus.textContent = data.macRandomization ? "ENABLED (next reboot)" : "OFF";

  // Terminal Mode
  if (data.terminalMode !== undefined) {
    setToggle("tog-terminalmode", data.terminalMode);
    const termStatus = document.getElementById("term-status");
    termStatus.className = "arsenal-status " + (data.terminalMode ? "on" : "off");
    termStatus.textContent = data.terminalMode ? "CLI ONLY (next reboot)" : "DESKTOP";
  }

  // Blocklist freq
  const freqSel = document.getElementById("sel-blockfreq");
  if (freqSel) freqSel.value = data.blocklistFreq || "weekly";

  // Client count
  document.getElementById("client-count").textContent = data.clientCount + " device" + (data.clientCount !== 1 ? "s" : "");

  // Pi-hole connection
  updatePiholeSetupBanner(data.piholeConnected);

  // Schedules
  renderSchedules(data.schedules || []);
}

async function fetchArsenalStatus() {
  try {
    const [arsenalRes, termRes] = await Promise.all([
      fetch(ARSENAL_API + "/api/arsenal/status"),
      fetch(ARSENAL_API + "/api/terminal-mode"),
    ]);
    const data = await arsenalRes.json();
    const termData = await termRes.json();
    if (data.ok) {
      if (termData.ok) data.terminalMode = termData.terminalMode;
      updateArsenalUI(data);
    }
  } catch (e) { /* silent */ }
}

async function fetchClients() {
  try {
    const res = await fetch(ARSENAL_API + "/api/arsenal/clients");
    const data = await res.json();
    if (data.ok) renderClients(data.clients);
  } catch (e) { /* silent */ }
}

function renderClients(clients) {
  const list = document.getElementById("client-list");
  if (!clients.length) {
    list.innerHTML = '<div style="font-size:10px;color:var(--text-faint)">No devices connected</div>';
    updateMacPrivacyCount(0, 0);
    return;
  }
  list.innerHTML = clients.map(c => {
    var macIcon = c.macRandomized ? '<span title="MAC randomized" style="color:var(--green);margin-left:4px">&#x1f512;</span>' : '<span title="Real MAC exposed — enable Private WiFi Address" style="color:var(--red);margin-left:4px">&#x26a0;</span>';
    return '<div class="client-row"><span class="client-host">' + escapeHtml(c.hostname) + '</span><span class="client-ip">' + escapeHtml(c.ip) + '</span><span class="client-mac">' + escapeHtml(c.mac) + macIcon + '</span></div>';
  }).join("");
  var randomized = clients.filter(function(c) { return c.macRandomized; }).length;
  updateMacPrivacyCount(randomized, clients.length);
}

function updateMacPrivacyCount(randomized, total) {
  var el = document.getElementById("mac-privacy-status");
  if (!el) return;
  if (total === 0) {
    el.textContent = "No devices connected";
    el.style.color = "var(--text-dim)";
  } else if (randomized === total) {
    el.textContent = total + "/" + total + " devices using private MAC";
    el.style.color = "var(--green)";
  } else {
    el.textContent = randomized + "/" + total + " devices using private MAC — " + (total - randomized) + " exposed";
    el.style.color = "var(--red)";
  }
}

// Scroll helper for read-only DNS card's "CHANGE" button
function scrollToModes(e) {
  if (e) e.preventDefault();
  const el = document.getElementById("section-modes");
  if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
}

// ── Dreadnought Mode (recursive DNS — ISP/ZT only) ──────────

function updateDreadnought(data) {
  const row = document.getElementById("dn-row");
  if (!row) return;
  const applicable = !!data.dreadnoughtApplicable;  // hide entirely in tunnel modes
  row.style.display = applicable ? "flex" : "none";
  if (!applicable) return;

  const on = !!data.dreadnought;
  const ready = !!data.dreadnoughtReady;
  row.classList.toggle("on", on);
  row.classList.toggle("disabled", !ready);

  const checkEl = document.getElementById("dn-check");
  if (checkEl) checkEl.setAttribute("aria-checked", on ? "true" : "false");

  const installBtn = document.getElementById("dn-install");
  const warn = document.getElementById("dn-warn");
  if (installBtn) installBtn.style.display = ready ? "none" : "inline-block";
  if (warn) warn.style.display = (ready && on) ? "inline" : "none";
}

// Ghost Mode — mirrors updateDreadnought / toggleDreadnought but for WG exit rotation.
// Applicable in tunnel modes (opposite of Dreadnought). "Ready" means ≥2 endpoints configured.
function updateGhostMode(data) {
  const row = document.getElementById("gm-row");
  if (!row) return;
  // Show only in doublehop/zhop (tunnel modes). data.mode is the current mode string.
  const applicable = data.mode === "doublehop" || data.mode === "zhop";
  row.style.display = applicable ? "flex" : "none";
  if (!applicable) return;

  const on = !!data.ghostMode;
  const ready = !!data.ghostReady;
  row.classList.toggle("on", on);
  row.classList.toggle("disabled", !ready);
  const check = document.getElementById("gm-check");
  if (check) check.setAttribute("aria-checked", on ? "true" : "false");
  const warn = document.getElementById("gm-warn");
  if (warn) warn.style.display = ready ? "none" : "inline";
}

function toggleGhostMode() {
  const row = document.getElementById("gm-row");
  if (!row || row.classList.contains("disabled") || row.style.display === "none") return;
  const isOn = row.classList.contains("on");
  setGhostMode(!isOn);
}

async function setGhostMode(enabled) {
  log(`Ghost Mode: ${enabled ? "engaging rotation..." : "disabling rotation..."}`, "warn");
  try {
    const res = await fetch(ARSENAL_API + "/api/arsenal/ghostmode", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    });
    const data = await res.json();
    if (data.ok) {
      log(`Ghost Mode ${enabled ? "active — WG exit rotates every 4h" : "disabled"}`, enabled ? "success" : "info");
      await fetchArsenalStatus();
    } else {
      log(`Ghost Mode: ${data.error || "failed"}`, "error");
    }
  } catch (e) {
    log(`Ghost Mode error: ${e.message}`, "error");
  }
}

function toggleDreadnought() {
  const row = document.getElementById("dn-row");
  if (!row || row.classList.contains("disabled") || row.style.display === "none") return;
  const isOn = row.classList.contains("on");
  if (isOn) {
    setDreadnought(false);  // disabling doesn't need confirmation — user owns the risk
  } else {
    document.getElementById("dn-modal").classList.add("visible");
  }
}

function dreadnoughtCancel() {
  document.getElementById("dn-modal").classList.remove("visible");
}

function dreadnoughtConfirm() {
  document.getElementById("dn-modal").classList.remove("visible");
  setDreadnought(true);
}

async function setDreadnought(enabled) {
  log(`Dreadnought: ${enabled ? "engaging..." : "disengaging..."}`, "warn");
  try {
    const res = await fetch(ARSENAL_API + "/api/arsenal/dreadnought", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    });
    const data = await res.json();
    if (data.ok) {
      log(`Dreadnought ${enabled ? "active — DNS now recursive" : "disabled — DoH restored"}`, enabled ? "success" : "info");
      await fetchArsenalStatus();
    } else {
      log(`Dreadnought: ${data.error}`, "error");
    }
  } catch (e) {
    log(`Dreadnought error: ${e.message}`, "error");
  }
}

async function installDreadnought() {
  const btns = [document.getElementById("dn-install")].filter(Boolean);
  btns.forEach(b => { b.disabled = true; b.textContent = "INSTALLING..."; });
  log("Dreadnought: installing unbound (may take up to 2 minutes)...", "info");
  try {
    const res = await fetch(ARSENAL_API + "/api/arsenal/dreadnought/install", { method: "POST" });
    const data = await res.json();
    if (data.ok) {
      log("Dreadnought: unbound installed and running — ready to engage", "success");
      await fetchArsenalStatus();
    } else {
      log(`Dreadnought install failed: ${data.error}`, "error");
    }
  } catch (e) {
    log(`Dreadnought install error: ${e.message}`, "error");
  }
  btns.forEach(b => { b.disabled = false; b.textContent = "INSTALL"; });
}

// ── Toggle actions ──────────────────────────────────────────

async function arsenalToggle(feature) {
  // encrypteddns intentionally removed — DNS Protection card is now read-only.
  // DNS intent is set from the mode section (Dreadnought checkbox) and the
  // gp-dns-upstream script applied automatically by gp-mode.
  const toggleMap = {
    killswitch: { endpoint: "/api/arsenal/killswitch", key: "killSwitch", togId: "tog-killswitch" },
    quicblock: { endpoint: "/api/arsenal/quicblock", key: "quicBlock", togId: "tog-quicblock" },
    tcpscrub:  { endpoint: "/api/arsenal/tcpscrub",  key: "tcpScrub",  togId: "tog-tcpscrub"  },
    macrandom: { endpoint: "/api/arsenal/macrandom", key: "macRandomization", togId: "tog-macrandom" },
    terminalmode: { endpoint: "/api/terminal-mode", key: "terminalMode", togId: "tog-terminalmode" },
  };

  const cfg = toggleMap[feature];
  if (!cfg) return;

  const toggle = document.getElementById(cfg.togId);
  // T-0066: setToggleEnabled disables pointer-events for mode-locked toggles, but keep
  // a defensive check (programmatic calls or future synthetic events should also bail).
  if (toggle.classList.contains("disabled")) {
    log(`Arsenal: ${feature} is locked by the current mode — change mode to control it`, "warn");
    return;
  }
  const isOn = toggle.classList.contains("on");
  const newState = !isOn;

  setToggleLoading(cfg.togId, true);
  log(`Arsenal: ${feature} → ${newState ? "ON" : "OFF"}...`, "warn");

  try {
    const res = await fetch(ARSENAL_API + cfg.endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: newState }),
    });
    const data = await res.json();
    if (data.ok) {
      log(`Arsenal: ${feature} ${newState ? "enabled" : "disabled"}`, "success");
      if (data.note) log(`Note: ${data.note}`, "info");
      await fetchArsenalStatus();
    } else {
      log(`Arsenal: ${feature} failed — ${data.error}`, "error");
    }
  } catch (e) {
    log(`Arsenal: ${feature} error — ${e.message}`, "error");
  }
  setToggleLoading(cfg.togId, false);
}

// ── Kill Switch Auto Toggle ─────────────────────────────────

async function toggleKsAuto() {
  const toggle = document.getElementById("tog-ks-auto");
  const isOn = toggle.classList.contains("on");
  const newState = !isOn;

  setToggleLoading("tog-ks-auto", true);
  try {
    const res = await fetch(ARSENAL_API + "/api/arsenal/killswitch/auto", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: newState }),
    });
    const data = await res.json();
    if (data.ok) {
      log(`Kill switch auto-trip ${newState ? "enabled" : "disabled"}`, "success");
      await fetchArsenalStatus();
    } else {
      log("Auto-trip toggle failed: " + data.error, "error");
    }
  } catch (e) {
    log("Auto-trip error: " + e.message, "error");
  }
  setToggleLoading("tog-ks-auto", false);
}

// ── DNS Leak Test ───────────────────────────────────────────

async function runDnsTest() {
  const btn = document.getElementById("btn-dnstest");
  const result = document.getElementById("dns-result");
  btn.disabled = true;
  btn.textContent = "TESTING...";
  result.innerHTML = "";
  log("Running DNS leak test...", "info");

  try {
    const res = await fetch(ARSENAL_API + "/api/arsenal/dnstest", { method: "POST" });
    const data = await res.json();
    if (data.ok) {
      const cls = data.passed ? "pass" : "fail";
      result.innerHTML = `<div class="dns-result ${cls}">
        <div>${data.passed ? "PASS" : "FAIL"} — ${escapeHtml(data.reason)}</div>
        <div style="margin-top:4px;color:var(--text-dim)">Local resolver: ${escapeHtml(data.localResolver)} | Direct: ${escapeHtml(data.directResolver)}</div>
        <div style="color:var(--text-dim)">Mode: ${escapeHtml(data.mode)} | Encrypted: ${data.encrypted ? "Yes" : "No"}</div>
      </div>`;
      log(`DNS test: ${data.passed ? "PASS" : "FAIL"} — ${data.reason}`, data.passed ? "success" : "error");
    } else {
      result.innerHTML = `<div class="dns-result fail">Error: ${escapeHtml(data.error)}</div>`;
    }
  } catch (e) {
    result.innerHTML = `<div class="dns-result fail">Error: ${escapeHtml(e.message)}</div>`;
    log("DNS test failed: " + e.message, "error");
  }
  btn.disabled = false;
  btn.textContent = "RUN TEST";
}

// ── Blocklist ───────────────────────────────────────────────

async function setBlockFreq(freq) {
  log(`Setting blocklist update to ${freq}...`, "info");
  try {
    const res = await fetch(ARSENAL_API + "/api/arsenal/blocklist", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ freq }),
    });
    const data = await res.json();
    if (data.ok) {
      log(`Blocklist updates set to ${freq}`, "success");
    } else {
      log("Blocklist freq change failed: " + data.error, "error");
    }
  } catch (e) {
    log("Blocklist freq error: " + e.message, "error");
  }
}

async function updateGravity() {
  const btn = document.getElementById("btn-gravity");
  const status = document.getElementById("gravity-status");
  btn.disabled = true;
  btn.textContent = "UPDATING...";
  status.textContent = "";
  status.style.color = "";
  log("Updating Pi-hole gravity...", "info");

  try {
    const res = await fetch(ARSENAL_API + "/api/arsenal/blocklist/update", { method: "POST" });
    const data = await res.json();
    if (data.ok) {
      status.textContent = "Updated!";
      status.style.color = "var(--green)";
      log("Gravity update complete", "success");
    } else {
      status.textContent = "Failed";
      status.style.color = "var(--red)";
      log("Gravity update failed", "error");
    }
  } catch (e) {
    status.textContent = "Error";
    status.style.color = "var(--red)";
  }
  btn.disabled = false;
  btn.textContent = "UPDATE NOW";
}

// ── Domain Block/Allow ─────────────────────────────────────

async function submitDomain(action) {
  const input = document.getElementById("domain-input");
  const status = document.getElementById("domain-status");
  const domain = input.value.trim();

  if (!domain) {
    status.style.color = "var(--red)";
    status.textContent = "Enter a domain";
    return;
  }

  status.style.color = "var(--text-dim)";
  status.textContent = "Submitting...";

  try {
    const res = await fetch(ARSENAL_API + "/api/arsenal/blocklist/domain", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ domain, action }),
    });
    const data = await res.json();
    if (data.ok) {
      status.style.color = "var(--green)";
      status.textContent = `${data.domain} ${action === "deny" ? "blocked" : "allowed"}`;
      input.value = "";
      log(`Domain ${action === "deny" ? "blocked" : "allowed"}: ${data.domain}`, "success");
    } else {
      status.style.color = "var(--red)";
      status.textContent = data.error;
    }
  } catch (e) {
    status.style.color = "var(--red)";
    status.textContent = "Error: " + e.message;
  }
}

// ── Schedules ───────────────────────────────────────────────

document.querySelectorAll("#day-picker .day-btn").forEach(btn => {
  btn.addEventListener("click", () => btn.classList.toggle("active"));
});

function renderSchedules(schedules) {
  const list = document.getElementById("schedule-list");
  if (!schedules.length) {
    list.innerHTML = '<div style="font-size:10px;color:var(--text-faint);padding:4px 0">No schedules set</div>';
    return;
  }
  list.innerHTML = schedules.map(s => {
    const dayLabels = s.days.map(d => DAY_NAMES[d]).join(", ");
    return `<div class="schedule-item">
      <span style="color:var(--green)">${escapeHtml(s.time)}</span>
      <span style="color:var(--text-dim)">${escapeHtml(dayLabels)}</span>
      <span style="color:var(--text)">${escapeHtml(s.mode.toUpperCase())}</span>
      <button class="schedule-del" data-sched-id="${escapeHtml(s.id)}">&times;</button>
    </div>`;
  }).join("");
  // Bind delete buttons via event delegation (no inline onclick)
  list.querySelectorAll(".schedule-del[data-sched-id]").forEach(btn => {
    btn.addEventListener("click", () => deleteSchedule(btn.dataset.schedId));
  });
}

async function addSchedule() {
  const time = document.getElementById("sched-time").value;
  const mode = document.getElementById("sched-mode").value;
  const days = [];
  document.querySelectorAll("#day-picker .day-btn.active").forEach(btn => {
    days.push(parseInt(btn.dataset.day));
  });

  if (!time || !days.length) {
    log("Schedule: pick a time and at least one day", "warn");
    return;
  }

  log(`Adding schedule: ${mode.toUpperCase()} at ${time}...`, "info");
  try {
    const res = await fetch(ARSENAL_API + "/api/arsenal/schedules", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ time, days, mode }),
    });
    const data = await res.json();
    if (data.ok) {
      log(`Schedule added: ${mode.toUpperCase()} at ${time}`, "success");
      await fetchArsenalStatus();
    } else {
      log("Schedule failed: " + data.error, "error");
    }
  } catch (e) {
    log("Schedule error: " + e.message, "error");
  }
}

async function deleteSchedule(id) {
  try {
    const res = await fetch(ARSENAL_API + "/api/arsenal/schedules/" + id, { method: "DELETE" });
    const data = await res.json();
    if (data.ok) {
      log("Schedule removed", "success");
      await fetchArsenalStatus();
    }
  } catch (e) {
    log("Delete schedule error: " + e.message, "error");
  }
}

// ── Passcode & Session ──────────────────────────────────────

async function changePasscode() {
  const current = document.getElementById("current-passcode").value;
  const newPass = document.getElementById("new-passcode").value;
  const status = document.getElementById("passcode-status");

  if (!current) {
    status.style.color = "var(--red)";
    status.textContent = "Current passcode is required";
    return;
  }

  status.style.color = "var(--text-dim)";
  status.textContent = "Changing...";

  try {
    const res = await fetch(ARSENAL_API + "/api/auth/change-passcode", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ currentPasscode: current, newPasscode: newPass || undefined }),
    });
    const data = await res.json();
    if (data.ok) {
      document.getElementById("current-passcode").value = "";
      document.getElementById("new-passcode").value = "";
      if (data.generated) {
        status.style.color = "var(--green)";
        status.textContent = `New passcode: ${data.passcode} — save this!`;
      } else {
        status.style.color = "var(--green)";
        status.textContent = "Passcode changed successfully";
      }
      log("Device passcode changed", "success");
    } else {
      status.style.color = "var(--red)";
      status.textContent = data.error;
    }
  } catch (e) {
    status.style.color = "var(--red)";
    status.textContent = "Error: " + e.message;
  }
}

async function logoutSession() {
  try {
    await fetch(ARSENAL_API + "/api/auth/logout", { method: "POST" });
  } catch (e) { /* ignore */ }
  window.location.href = "/login.html";
}

// ── TOTP Two-Factor Authentication ───────────────────────────

async function totpLoadStatus() {
  try {
    const res = await fetch(ARSENAL_API + "/api/auth/totp/status");
    const data = await res.json();
    const badge = document.getElementById("totp-badge");
    if (data.totpConfigured) {
      document.getElementById("totp-off").style.display = "none";
      document.getElementById("totp-setup").style.display = "none";
      document.getElementById("totp-on").style.display = "block";
      badge.textContent = "ACTIVE";
      badge.style.background = "var(--green-13)";
      badge.style.color = "var(--green)";
      badge.style.display = "inline-block";
      // Load backup code count
      try {
        const bcRes = await fetch(ARSENAL_API + "/api/auth/totp/backup-codes");
        const bcData = await bcRes.json();
        if (bcData.ok) {
          const el = document.getElementById("totp-backup-remaining");
          el.textContent = `Backup codes remaining: ${bcData.remaining} of ${bcData.total}`;
          if (bcData.remaining <= 2) el.style.color = "var(--red)";
        }
      } catch {}
    } else {
      document.getElementById("totp-off").style.display = "block";
      document.getElementById("totp-setup").style.display = "none";
      document.getElementById("totp-on").style.display = "none";
      badge.style.display = "none";
    }
  } catch {}
}

async function totpEnable() {
  const status = document.getElementById("totp-status");
  status.style.color = "var(--text-dim)";
  status.textContent = "Generating...";
  try {
    const res = await fetch(ARSENAL_API + "/api/auth/totp/setup", {
      method: "POST",
      headers: { "Content-Type": "application/json" }
    });
    const data = await res.json();
    if (!data.ok) {
      status.style.color = "var(--red)";
      status.textContent = data.error || "Setup failed";
      return;
    }
    // Show QR code and secret
    document.getElementById("totp-qr").src = data.qrDataUri;
    document.getElementById("totp-secret-display").textContent = data.secret.replace(/(.{4})/g, "$1 ").trim();
    // Show backup codes (safe DOM construction)
    const codesEl = document.getElementById("totp-backup-codes");
    codesEl.textContent = "";
    data.backupCodes.forEach(function(c) {
      var span = document.createElement("span");
      span.style.padding = "2px 4px";
      span.textContent = c;
      codesEl.appendChild(span);
    });
    // Switch view
    document.getElementById("totp-off").style.display = "none";
    document.getElementById("totp-setup").style.display = "block";
    document.getElementById("totp-confirm-code").value = "";
    document.getElementById("totp-confirm-code").focus();
    status.textContent = "";
  } catch (e) {
    status.style.color = "var(--red)";
    status.textContent = "Error: " + e.message;
  }
}

async function totpConfirmSetup() {
  const code = document.getElementById("totp-confirm-code").value.trim();
  const status = document.getElementById("totp-status");
  if (!code || code.length !== 6) {
    status.style.color = "var(--red)";
    status.textContent = "Enter the 6-digit code from your authenticator app";
    return;
  }
  status.style.color = "var(--text-dim)";
  status.textContent = "Verifying...";
  try {
    const res = await fetch(ARSENAL_API + "/api/auth/totp/confirm-setup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: code })
    });
    const data = await res.json();
    if (data.ok) {
      status.style.color = "var(--green)";
      status.textContent = "Two-factor authentication activated!";
      log("TOTP two-factor authentication enabled", "success");
      totpLoadStatus();
    } else {
      status.style.color = "var(--red)";
      status.textContent = data.error || "Invalid code";
      document.getElementById("totp-confirm-code").value = "";
      document.getElementById("totp-confirm-code").focus();
    }
  } catch (e) {
    status.style.color = "var(--red)";
    status.textContent = "Error: " + e.message;
  }
}

function totpCancelSetup() {
  document.getElementById("totp-setup").style.display = "none";
  document.getElementById("totp-off").style.display = "block";
  document.getElementById("totp-status").textContent = "";
}

async function totpDisable() {
  const code = document.getElementById("totp-disable-code").value.trim();
  const status = document.getElementById("totp-status");
  if (!code || code.length !== 6) {
    status.style.color = "var(--red)";
    status.textContent = "Enter your current TOTP code to disable";
    return;
  }
  status.style.color = "var(--text-dim)";
  status.textContent = "Disabling...";
  try {
    const res = await fetch(ARSENAL_API + "/api/auth/totp/disable", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: code })
    });
    const data = await res.json();
    if (data.ok) {
      status.style.color = "var(--green)";
      status.textContent = "Two-factor authentication disabled";
      document.getElementById("totp-disable-code").value = "";
      log("TOTP two-factor authentication disabled", "warning");
      totpLoadStatus();
    } else {
      status.style.color = "var(--red)";
      status.textContent = data.error || "Invalid code";
      document.getElementById("totp-disable-code").value = "";
    }
  } catch (e) {
    status.style.color = "var(--red)";
    status.textContent = "Error: " + e.message;
  }
}

async function totpRegenBackup() {
  var code = prompt("Enter your current TOTP code to regenerate backup codes:");
  if (!code) return;
  var status = document.getElementById("totp-status");
  status.style.color = "var(--text-dim)";
  status.textContent = "Regenerating...";
  try {
    var res = await fetch(ARSENAL_API + "/api/auth/totp/regenerate-backup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: code.trim() })
    });
    var data = await res.json();
    if (data.ok) {
      var codesEl = document.getElementById("totp-regen-codes");
      codesEl.textContent = "";
      data.backupCodes.forEach(function(c) {
        var span = document.createElement("span");
        span.style.padding = "2px 4px";
        span.textContent = c;
        codesEl.appendChild(span);
      });
      codesEl.style.display = "grid";
      status.style.color = "var(--green)";
      status.textContent = "New backup codes generated — save them now!";
      totpLoadStatus();
    } else {
      status.style.color = "var(--red)";
      status.textContent = data.error || "Failed";
    }
  } catch (e) {
    status.style.color = "var(--red)";
    status.textContent = "Error: " + e.message;
  }
}

async function totpResync() {
  const status = document.getElementById("totp-status");
  // Require passcode for security
  const passcode = prompt("Enter your device passcode to resync 2FA:");
  if (!passcode) return;
  status.style.color = "var(--text-dim)";
  status.textContent = "Verifying passcode...";
  try {
    const res = await fetch(ARSENAL_API + "/api/auth/totp/resync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ passcode: passcode })
    });
    const data = await res.json();
    if (!data.ok) {
      status.style.color = "var(--red)";
      status.textContent = data.error || "Resync failed";
      return;
    }
    // Show the new QR setup
    document.getElementById("totp-qr").src = data.qrDataUri;
    document.getElementById("totp-secret-display").textContent = data.secret.replace(/(.{4})/g, "$1 ").trim();
    var codesEl = document.getElementById("totp-backup-codes");
    codesEl.textContent = "";
    data.backupCodes.forEach(function(c) {
      var span = document.createElement("span");
      span.style.padding = "2px 4px";
      span.textContent = c;
      codesEl.appendChild(span);
    });
    document.getElementById("totp-on").style.display = "none";
    document.getElementById("totp-setup").style.display = "block";
    document.getElementById("totp-confirm-code").value = "";
    document.getElementById("totp-confirm-code").focus();
    status.style.color = "var(--green)";
    status.textContent = "Scan the new QR code and enter the 6-digit code to confirm";
  } catch (e) {
    status.style.color = "var(--red)";
    status.textContent = "Error: " + e.message;
  }
}

// Wire up TOTP buttons (event delegation safe)
document.addEventListener("DOMContentLoaded", function() {
  var el;
  el = document.getElementById("totp-enable-btn");
  if (el) el.addEventListener("click", totpEnable);
  el = document.getElementById("totp-confirm-btn");
  if (el) el.addEventListener("click", totpConfirmSetup);
  el = document.getElementById("totp-cancel-btn");
  if (el) el.addEventListener("click", totpCancelSetup);
  el = document.getElementById("totp-disable-btn");
  if (el) el.addEventListener("click", totpDisable);
  el = document.getElementById("totp-resync-btn");
  if (el) el.addEventListener("click", totpResync);
  el = document.getElementById("totp-regen-btn");
  if (el) el.addEventListener("click", totpRegenBackup);
  // Enter key on confirm input
  el = document.getElementById("totp-confirm-code");
  if (el) el.addEventListener("keydown", function(e) { if (e.key === "Enter") totpConfirmSetup(); });
  el = document.getElementById("totp-disable-code");
  if (el) el.addEventListener("keydown", function(e) { if (e.key === "Enter") totpDisable(); });
  // Load initial TOTP status
  totpLoadStatus();
});

// ── Speed Test ───────────────────────────────────────────────

async function runSpeedTest() {
  const btn = document.getElementById("btn-speedtest");
  const result = document.getElementById("speed-result");
  btn.disabled = true;
  btn.textContent = "TESTING...";
  result.innerHTML = '<div style="font-size:11px;color:var(--text-dim)">Running speed test (this may take 30-60 seconds)...</div>';
  log("Running speed test...", "info");
  try {
    const res = await fetch(ARSENAL_API + "/api/tools/speedtest", { method: "POST" });
    const data = await res.json();
    if (data.ok) {
      result.innerHTML = `<div style="display:flex;gap:16px;flex-wrap:wrap;margin-top:4px">
        <div><div style="font-size:10px;color:var(--text-faint)">DOWNLOAD</div><div style="font-size:18px;color:var(--green)">${escapeHtml(data.download)} <span style="font-size:11px">Mbps</span></div></div>
        <div><div style="font-size:10px;color:var(--text-faint)">UPLOAD</div><div style="font-size:18px;color:var(--green)">${escapeHtml(data.upload)} <span style="font-size:11px">Mbps</span></div></div>
        <div><div style="font-size:10px;color:var(--text-faint)">PING</div><div style="font-size:18px;color:var(--green)">${escapeHtml(data.ping)} <span style="font-size:11px">ms</span></div></div>
      </div><div style="font-size:10px;color:var(--text-faint);margin-top:4px">Server: ${escapeHtml(data.server)}</div>`;
      log(`Speed: ${data.download} Mbps down / ${data.upload} Mbps up / ${data.ping}ms`, "success");
    } else {
      result.innerHTML = `<div class="dns-result fail">${escapeHtml(data.error)}</div>`;
      log("Speed test failed: " + data.error, "error");
    }
  } catch (e) {
    result.innerHTML = `<div class="dns-result fail">Error: ${escapeHtml(e.message)}</div>`;
  }
  btn.disabled = false;
  btn.textContent = "RUN TEST";
}

// ── Ping Test ────────────────────────────────────────────────

async function runPingTest() {
  const btn = document.getElementById("btn-pingtest");
  const result = document.getElementById("ping-result");
  btn.disabled = true;
  btn.textContent = "TESTING...";
  result.innerHTML = '<div style="font-size:11px;color:var(--text-dim)">Pinging targets...</div>';
  log("Running ping test...", "info");
  try {
    const res = await fetch(ARSENAL_API + "/api/tools/ping", { method: "POST" });
    const data = await res.json();
    if (data.ok) {
      result.innerHTML = data.results.map(r => {
        const cls = r.reachable ? "pass" : "fail";
        const latency = r.latency ? `${r.latency.avg}ms` : "timeout";
        return `<div class="dns-result ${cls}" style="padding:6px 10px;margin-top:4px">
          <span style="color:var(--text)">${escapeHtml(r.name)}</span>
          <span style="color:var(--text-dim);margin-left:8px">${escapeHtml(r.target)}</span>
          <span style="float:right;color:${r.reachable ? 'var(--green)' : 'var(--red)'}">${latency}</span>
        </div>`;
      }).join("");
      log("Ping test complete", "success");
    } else {
      result.innerHTML = `<div class="dns-result fail">${escapeHtml(data.error)}</div>`;
    }
  } catch (e) {
    result.innerHTML = `<div class="dns-result fail">Error: ${escapeHtml(e.message)}</div>`;
  }
  btn.disabled = false;
  btn.textContent = "RUN TEST";
}

// ── IP Leak Test ─────────────────────────────────────────────

async function runIpLeak() {
  const btn = document.getElementById("btn-ipleak");
  const result = document.getElementById("ipleak-result");
  btn.disabled = true;
  btn.textContent = "TESTING...";
  result.innerHTML = '<div style="font-size:11px;color:var(--text-dim)">Checking public IP...</div>';
  log("Running IP leak test...", "info");
  try {
    const res = await fetch(ARSENAL_API + "/api/tools/ipleak", { method: "POST" });
    const data = await res.json();
    if (data.ok) {
      const cls = data.leaked ? "fail" : "pass";
      result.innerHTML = `<div class="dns-result ${cls}" style="margin-top:4px">
        <div>${data.leaked ? "FAIL — IP LEAK DETECTED" : data.vpnMode ? "PASS — IP is masked" : "INFO — Not in VPN mode"}</div>
        <div style="margin-top:4px;color:var(--text-dim);font-size:11px">Public IP: ${escapeHtml(data.publicIp)} | Mode: ${escapeHtml(data.mode)} | WG endpoint: ${escapeHtml(data.wgEndpoint)}</div>
      </div>`;
      log(`IP leak test: ${data.status}`, data.leaked ? "error" : "success");
    } else {
      result.innerHTML = `<div class="dns-result fail">${escapeHtml(data.error)}</div>`;
    }
  } catch (e) {
    result.innerHTML = `<div class="dns-result fail">Error: ${escapeHtml(e.message)}</div>`;
  }
  btn.disabled = false;
  btn.textContent = "RUN TEST";
}

// ── Bandwidth Monitor ────────────────────────────────────────

function formatBytes(bytes) {
  if (bytes >= 1e9) return (bytes / 1e9).toFixed(1) + " GB";
  if (bytes >= 1e6) return (bytes / 1e6).toFixed(1) + " MB";
  if (bytes >= 1e3) return (bytes / 1e3).toFixed(1) + " KB";
  return bytes + " B";
}

function formatRate(bytesPerSec) {
  if (bytesPerSec >= 1e6) return (bytesPerSec / 1e6).toFixed(1) + " MB/s";
  if (bytesPerSec >= 1e3) return (bytesPerSec / 1e3).toFixed(1) + " KB/s";
  return bytesPerSec.toFixed(0) + " B/s";
}

async function fetchBandwidth() {
  const result = document.getElementById("bandwidth-result");
  if (!result) return;
  try {
    // T-0070: migrated from /api/tools/bandwidth (raw counters, deltas computed client-side)
    // to /api/tools/bandwidth/rate (server returns precomputed rx_rate_bps/tx_rate_bps).
    // Endpoint is shared with the TRAFFIC MONITOR poll in index.html's inline block.
    const res = await fetch(ARSENAL_API + "/api/tools/bandwidth/rate");
    const data = await res.json();
    if (data.ok) {
      var labels = { eth0: "WAN", wlan0: "WiFi AP", wg0: "WireGuard", tailscale0: "Tailscale" };
      var colors = { eth0: "#00e5ff", wlan0: "#00c853", wg0: "#ff9f43", tailscale0: "#888" };

      result.innerHTML = Object.entries(data.interfaces).map(function(entry) {
        var iface = entry[0], s = entry[1];
        var rxRate = s.rx_rate_bps || 0;
        var txRate = s.tx_rate_bps || 0;
        var c = colors[iface] || "#888";
        return '<div style="padding:6px 0;border-bottom:1px solid var(--border)">'
          + '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:2px">'
          + '<span style="color:' + c + ';font-size:12px;font-weight:600">' + (labels[iface] || iface) + '</span>'
          + '<span style="color:var(--text-dim);font-size:10px">Total: ' + formatBytes(s.rx_bytes + s.tx_bytes) + '</span>'
          + '</div>'
          + '<div style="display:flex;gap:16px;font-size:11px">'
          + '<span style="color:var(--green)">↓ ' + formatRate(rxRate) + '</span>'
          + '<span style="color:var(--amber)">↑ ' + formatRate(txRate) + '</span>'
          + '<span style="color:var(--text-dim);margin-left:auto">↓ ' + formatBytes(s.rx_bytes) + ' / ↑ ' + formatBytes(s.tx_bytes) + '</span>'
          + '</div>'
          + '</div>';
      }).join("");
    }
  } catch (e) { /* silent — endpoint may return 429 if a parallel /rate poll overlaps */ }
}

// ── Recent Blocked Domains ───────────────────────────────────

async function fetchBlocked() {
  const btn = document.getElementById("btn-blocked");
  const result = document.getElementById("blocked-result");
  btn.disabled = true;
  try {
    const res = await fetch(ARSENAL_API + "/api/tools/blocked");
    const data = await res.json();
    if (data.ok && data.queries.length) {
      result.innerHTML = data.queries.map(q =>
        `<div style="display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px solid #1a1a1a;font-size:10px">
          <span style="color:var(--red);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(q.domain)}</span>
          <span style="color:var(--text-faint);margin-left:8px;white-space:nowrap">${escapeHtml(q.client)}</span>
        </div>`
      ).join("");
    } else if (data.ok) {
      result.innerHTML = '<div style="font-size:10px;color:var(--text-faint)">No blocked queries found</div>';
    } else {
      result.innerHTML = `<div style="font-size:10px;color:var(--red)">${escapeHtml(data.error)}</div>`;
    }
  } catch (e) {
    result.innerHTML = `<div style="font-size:10px;color:var(--red)">Error: ${escapeHtml(e.message)}</div>`;
  }
  btn.disabled = false;
}

// ── Enemy List (data brokers + surveillance partners) ────────────────
// Collapsible card — default collapsed; open/closed state persists in
// localStorage so the user's choice sticks across refreshes. Only polls
// the backend when expanded, so a collapsed tile costs nothing.

const ENEMIES_PREF_KEY = "gp.enemies.expanded";

function setEnemiesExpanded(expanded) {
  const body = document.getElementById("enemies-body");
  const caret = document.getElementById("enemies-caret");
  const header = document.getElementById("enemies-header");
  if (!body || !caret || !header) return;
  body.style.display = expanded ? "block" : "none";
  caret.textContent = expanded ? "▾" : "▸";
  header.setAttribute("aria-expanded", expanded ? "true" : "false");
  try { localStorage.setItem(ENEMIES_PREF_KEY, expanded ? "1" : "0"); } catch {}
  if (expanded) fetchEnemies();
}

function toggleEnemies() {
  const header = document.getElementById("enemies-header");
  if (!header) return;
  const expanded = header.getAttribute("aria-expanded") === "true";
  setEnemiesExpanded(!expanded);
}

async function fetchEnemies() {
  const btn = document.getElementById("btn-enemies");
  const result = document.getElementById("enemies-result");
  const totalLbl = document.getElementById("enemies-total");
  if (btn) btn.disabled = true;
  try {
    const res = await fetch(ARSENAL_API + "/api/tools/enemies");
    const data = await res.json();
    if (!data.ok) {
      result.innerHTML = `<div style="font-size:10px;color:var(--red)">${escapeHtml(data.error || "Operation failed")}</div>`;
      if (totalLbl) totalLbl.textContent = "—";
      return;
    }
    if (!data.brokers.length) {
      result.innerHTML = '<div style="font-size:10px;color:var(--text-faint)">No broker traffic detected. Clean house.</div>';
      if (totalLbl) totalLbl.textContent = "0 contacts";
      return;
    }
    const top = data.brokers.slice(0, 10);
    const nft = data.source === "nftables";
    const metric = nft ? "packets" : "attempted";
    const maxVal = top[0][metric] || 1;
    const fmtBytes = b => {
      if (b < 1024) return b + " B";
      if (b < 1048576) return (b / 1024).toFixed(1) + " KB";
      if (b < 1073741824) return (b / 1048576).toFixed(1) + " MB";
      return (b / 1073741824).toFixed(2) + " GB";
    };
    result.innerHTML = top.map(b => {
      const val = b[metric];
      const pct = Math.max(3, Math.round((val / maxVal) * 100));
      let rightLabel, badge = "";
      if (nft) {
        rightLabel = val.toLocaleString() + " pkts";
        badge = `<span style="font-size:9px;color:var(--text-faint)">${fmtBytes(b.bytes)}</span>`;
      } else {
        const pctBlocked = b.attempted ? Math.round((b.blocked / b.attempted) * 100) : 0;
        const badgeColor = pctBlocked === 100 ? "var(--green)" : pctBlocked === 0 ? "var(--red)" : "var(--amber)";
        const badgeText = pctBlocked === 100 ? "✓ all blocked" : pctBlocked === 0 ? "⚠ none blocked" : `${pctBlocked}% blocked`;
        badge = `<span style="font-size:9px;color:${badgeColor}">${badgeText}</span>`;
        rightLabel = val.toLocaleString();
      }
      return `<div style="padding:4px 0;border-bottom:1px solid var(--border)">
        <div style="display:flex;justify-content:space-between;align-items:center;font-size:11px;margin-bottom:3px">
          <span style="color:var(--text)">${escapeHtml(b.name)}</span>
          <span style="display:flex;gap:8px;align-items:center">
            ${badge}
            <span style="color:var(--text);font-weight:bold">${escapeHtml(rightLabel)}</span>
          </span>
        </div>
        <div style="height:4px;background:var(--bg2);border-radius:2px;overflow:hidden">
          <div style="width:${pct}%;height:100%;background:linear-gradient(90deg,#d00,#f44)"></div>
        </div>
      </div>`;
    }).join("");
    if (totalLbl) {
      if (nft) {
        totalLbl.textContent = `${data.packetsTotal.toLocaleString()} pkts / ${fmtBytes(data.bytesTotal)}`;
      } else {
        totalLbl.textContent = `${data.attemptedTotal.toLocaleString()} attempts / ${data.blockedTotal.toLocaleString()} blocked`;
      }
    }
  } catch (e) {
    result.innerHTML = `<div style="font-size:10px;color:var(--red)">Error: ${escapeHtml(e.message)}</div>`;
  } finally {
    if (btn) btn.disabled = false;
  }
}

// ── Backup / Restore ─────────────────────────────────────────

async function downloadBackup() {
  try {
    const res = await fetch(ARSENAL_API + "/api/tools/backup");
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "ghostport-backup.json";
    a.click();
    URL.revokeObjectURL(url);
    log("Backup exported", "success");
  } catch (e) {
    log("Backup failed: " + e.message, "error");
  }
}

async function importBackup(event) {
  const file = event.target.files[0];
  if (!file) return;
  const status = document.getElementById("backup-status");
  status.style.color = "var(--text-dim)";
  status.textContent = "Restoring...";
  try {
    const text = await file.text();
    const backup = JSON.parse(text);
    const res = await fetch(ARSENAL_API + "/api/tools/restore", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(backup),
    });
    const data = await res.json();
    if (data.ok) {
      status.style.color = "var(--green)";
      status.textContent = "Config restored!";
      log("Config restored from backup", "success");
      await fetchArsenalStatus();
    } else {
      status.style.color = "var(--red)";
      status.textContent = data.error;
    }
  } catch (e) {
    status.style.color = "var(--red)";
    status.textContent = "Invalid backup file";
  }
  event.target.value = "";
}

// ── System Update ────────────────────────────────────────────

function confirmUpdate() {
  const btn = document.getElementById("btn-update");
  if (btn.dataset.armed === "true") {
    runSystemUpdate();
    return;
  }
  btn.dataset.armed = "true";
  btn.textContent = "CONFIRM?";
  btn.style.borderColor = "var(--green)";
  log("Press UPDATE again within 5s to confirm", "warn");
  setTimeout(() => {
    btn.dataset.armed = "false";
    btn.textContent = "UPDATE";
    btn.style.borderColor = "";
  }, 5000);
}

async function runSystemUpdate() {
  const btn = document.getElementById("btn-update");
  const result = document.getElementById("update-result");
  btn.disabled = true;
  btn.textContent = "UPDATING...";
  result.innerHTML = '<div style="font-size:11px;color:var(--text-dim)">Running apt update & upgrade (this may take several minutes)...</div>';
  log("System update started...", "info");
  try {
    const res = await fetch(ARSENAL_API + "/api/tools/update", { method: "POST" });
    const data = await res.json();
    if (data.ok) {
      result.innerHTML = `<div class="dns-result pass">${escapeHtml(data.status)}</div>`;
      if (data.output) result.innerHTML += `<pre style="font-size:9px;color:var(--text-dim);margin-top:4px;white-space:pre-wrap">${escapeHtml(data.output)}</pre>`;
      log("System update complete", "success");
    } else {
      result.innerHTML = `<div class="dns-result fail">${escapeHtml(data.error || data.status)}</div>`;
      log("System update failed", "error");
    }
  } catch (e) {
    result.innerHTML = `<div class="dns-result fail">Error: ${escapeHtml(e.message)}</div>`;
  }
  btn.disabled = false;
  btn.textContent = "UPDATE";
  btn.dataset.armed = "false";
}

// ── WireGuard Setup ──────────────────────────────────────────

const WG_PROVIDERS = {
  mullvad: {
    name: "Mullvad VPN",
    steps: [
      "Go to <a href='https://mullvad.net/en/account/wireguard-config' target='_blank' style='color:var(--green)'>mullvad.net/en/account/wireguard-config</a>",
      "Log in with your account number",
      "Click <b>Generate key</b>, select a server location",
      "Click <b>Download file</b> to get the .conf file",
      "Upload the file below or paste its contents"
    ]
  },
  proton: {
    name: "ProtonVPN",
    steps: [
      "Go to <a href='https://account.protonvpn.com/downloads#wireguard-configuration' target='_blank' style='color:var(--green)'>account.protonvpn.com/downloads</a>",
      "Scroll to <b>WireGuard configuration</b>",
      "Select a server, choose <b>Router</b> as the platform",
      "Give it a name and click <b>Create</b>",
      "Download the generated .conf file"
    ]
  },
  ivpn: {
    name: "IVPN",
    steps: [
      "Go to <a href='https://www.ivpn.net/account/wireguard-config' target='_blank' style='color:var(--green)'>ivpn.net/account/wireguard-config</a>",
      "Log in with your IVPN account",
      "Click <b>Generate WireGuard key</b>",
      "Select a server and click <b>Download</b>",
      "Upload the .conf file below"
    ]
  },
  surfshark: {
    name: "Surfshark",
    steps: [
      "Go to <a href='https://my.surfshark.com/vpn/manual-setup/router/wireguard' target='_blank' style='color:var(--green)'>my.surfshark.com manual setup</a>",
      "Log in and go to <b>VPN → Manual Setup → Router</b>",
      "Select the <b>WireGuard</b> tab",
      "Choose a server and click <b>Get Credentials</b>",
      "Download the .conf or copy the key/endpoint details into the Easy Form tab"
    ]
  },
  nord: {
    name: "NordVPN",
    steps: [
      "Go to <a href='https://my.nordaccount.com/dashboard/nordvpn/manual-configuration/' target='_blank' style='color:var(--green)'>NordVPN manual configuration</a>",
      "Log in and select <b>Set up NordVPN manually</b>",
      "Go to the <b>WireGuard</b> section",
      "Generate a new key pair and pick a server",
      "Use the Easy Form tab to enter the Private Key, Server Public Key, and Endpoint"
    ]
  },
  pia: {
    name: "Private Internet Access",
    steps: [
      "Go to <a href='https://www.privateinternetaccess.com/pages/client-support/' target='_blank' style='color:var(--green)'>PIA client support</a>",
      "Log in to your account dashboard",
      "Navigate to <b>Downloads → WireGuard Configuration Generator</b>",
      "Select a server region and generate the config",
      "Download and upload the .conf file below"
    ]
  },
  windscribe: {
    name: "Windscribe",
    steps: [
      "Go to <a href='https://windscribe.com/getconfig/wireguard' target='_blank' style='color:var(--green)'>windscribe.com/getconfig/wireguard</a>",
      "Log in and select a server location",
      "Click <b>Get Config</b>",
      "Download the .conf file",
      "Upload it below"
    ]
  },
  airvpn: {
    name: "AirVPN",
    steps: [
      "Go to <a href='https://airvpn.org/generator/' target='_blank' style='color:var(--green)'>airvpn.org/generator</a>",
      "Log in and select <b>WireGuard</b> as the protocol",
      "Choose a server or country",
      "Click <b>Generate</b> and download the .conf file",
      "Upload it below"
    ]
  },
  other: {
    name: "Other Provider",
    steps: [
      "Log in to your VPN provider's website or dashboard",
      "Look for <b>WireGuard</b> or <b>Manual Setup</b> in their settings",
      "Generate or download a <b>.conf</b> configuration file",
      "The file should contain an <code>[Interface]</code> and <code>[Peer]</code> section",
      "Upload the .conf file, paste it in the Paste Config tab, or use the Easy Form tab"
    ]
  }
};

function switchWgTab(tab) {
  document.querySelectorAll(".wg-tab-content").forEach(el => el.style.display = "none");
  document.querySelectorAll(".wg-tab").forEach(el => el.classList.remove("active"));
  document.getElementById("wg-tab-" + tab).style.display = "block";
  document.querySelector(`.wg-tab[data-tab="${tab}"]`).classList.add("active");
}

function showProviderGuide() {
  const provider = document.getElementById("wg-provider").value;
  const guide = document.getElementById("wg-provider-guide");
  const steps = document.getElementById("wg-provider-steps");
  if (!provider || !WG_PROVIDERS[provider]) {
    guide.style.display = "none";
    return;
  }
  const p = WG_PROVIDERS[provider];
  steps.innerHTML = `<div style="color:var(--green);font-size:12px;margin-bottom:6px">${p.name} Setup</div>` +
    p.steps.map((s, i) => `<div style="margin:4px 0"><span style="color:var(--green)">${i + 1}.</span> ${s}</div>`).join("");
  guide.style.display = "block";
}

async function fetchWgStatus() {
  try {
    const res = await fetch(ARSENAL_API + "/api/wireguard/status");
    const data = await res.json();
    const el = document.getElementById("wg-setup-status");
    if (data.ok) {
      if (!data.configured) {
        el.className = "arsenal-status off";
        el.textContent = "NOT CONFIGURED";
      } else if (data.status === "up") {
        el.className = "arsenal-status on";
        el.textContent = "CONNECTED";
      } else {
        el.className = "arsenal-status tripped";
        el.textContent = "DOWN";
      }
      var rb = document.getElementById("btn-wg-restore");
      if (rb && data.configured) rb.style.display = "inline-block";
    }
  } catch (e) { /* silent */ }
}


async function restoreWgConfig() {
  const msg = document.getElementById("wg-setup-msg");
  const btn = document.getElementById("btn-wg-restore");
  btn.disabled = true;
  btn.textContent = "RESTORING...";
  msg.style.color = "var(--text-dim)";
  msg.textContent = "Restoring previous config...";
  try {
    const res = await fetch(ARSENAL_API + "/api/wireguard/restore", { method: "POST" });
    const data = await res.json();
    if (data.ok) {
      msg.style.color = data.status === "up" ? "var(--green)" : "var(--amber)";
      msg.textContent = data.message;
      log("WireGuard: " + data.message, data.status === "up" ? "success" : "warn");
      fetchWgStatus();
    } else {
      msg.style.color = "var(--red)";
      msg.textContent = data.error;
    }
  } catch (e) {
    msg.style.color = "var(--red)";
    msg.textContent = "Restore failed: " + e.message;
  }
  btn.disabled = false;
  btn.textContent = "RESTORE PREVIOUS";
}

async function submitWgConfig(config) {
  const msg = document.getElementById("wg-setup-msg");
  msg.style.color = "var(--text-dim)";
  msg.textContent = "Saving and connecting...";
  try {
    const res = await fetch(ARSENAL_API + "/api/wireguard/setup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ config }),
    });
    const data = await res.json();
    if (data.ok) {
      msg.style.color = data.status === "up" ? "var(--green)" : "var(--amber)";
      msg.textContent = data.message;
      log("WireGuard: " + data.message, data.status === "up" ? "success" : "warn");
      fetchWgStatus();
      return true;
    } else {
      msg.style.color = "var(--red)";
      msg.textContent = data.error;
      return false;
    }
  } catch (e) {
    msg.style.color = "var(--red)";
    msg.textContent = "Error: " + e.message;
    return false;
  }
}

async function saveWgConfig() {
  const config = document.getElementById("wg-config").value;
  const msg = document.getElementById("wg-setup-msg");
  if (!config.trim()) {
    msg.style.color = "var(--red)";
    msg.textContent = "Paste your WireGuard config above";
    return;
  }
  if (await submitWgConfig(config)) {
    document.getElementById("wg-config").value = "";
  }
}

function saveWgForm() {
  const msg = document.getElementById("wg-setup-msg");
  const privateKey = document.getElementById("wg-private-key").value.trim();
  const address = document.getElementById("wg-address").value.trim();
  const dns = document.getElementById("wg-dns").value.trim();
  const peerKey = document.getElementById("wg-peer-key").value.trim();
  const endpoint = document.getElementById("wg-endpoint").value.trim();
  const allowed = document.getElementById("wg-allowed").value.trim() || "0.0.0.0/0, ::/0";

  if (!privateKey || !address || !peerKey || !endpoint) {
    msg.style.color = "var(--red)";
    msg.textContent = "Fill in all required fields (Private Key, Address, Server Public Key, Endpoint)";
    return;
  }

  const config = `[Interface]
PrivateKey = ${privateKey}
Address = ${address}
DNS = ${dns || "10.64.0.1"}

[Peer]
PublicKey = ${peerKey}
Endpoint = ${endpoint}
AllowedIPs = ${allowed}
PersistentKeepalive = 25`;

  submitWgConfig(config);
}

async function uploadWgConfig(event) {
  const file = event.target.files[0];
  if (!file) return;
  const text = await file.text();
  event.target.value = "";
  // Auto-submit on upload
  if (text.includes("[Interface]") && text.includes("[Peer]")) {
    await submitWgConfig(text);
  } else {
    // Fall back to paste tab so user can review
    switchWgTab("advanced");
    document.getElementById("wg-config").value = text;
    const msg = document.getElementById("wg-setup-msg");
    msg.style.color = "var(--amber)";
    msg.textContent = "File loaded — check the config and click SAVE & CONNECT";
  }
}

// ── Pi-hole Setup ────────────────────────────────────────────

async function setupPihole() {
  const pw = document.getElementById("pihole-pw").value;
  const status = document.getElementById("pihole-setup-status");
  const btn = document.getElementById("btn-pihole-setup");
  if (!pw) {
    status.style.color = "var(--red)";
    status.textContent = "Enter your Pi-hole password";
    return;
  }
  btn.disabled = true;
  btn.textContent = "CONNECTING...";
  status.textContent = "";
  try {
    const res = await fetch(ARSENAL_API + "/api/pihole/setup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: pw }),
    });
    const data = await res.json();
    if (data.ok) {
      status.style.color = "var(--green)";
      status.textContent = "Connected to Pi-hole!";
      document.getElementById("pihole-pw").value = "";
      log("Pi-hole connected", "success");
      setTimeout(() => {
        document.getElementById("pihole-setup").style.display = "none";
      }, 2000);
    } else {
      status.style.color = "var(--red)";
      status.textContent = data.error || "Connection failed";
    }
  } catch (e) {
    status.style.color = "var(--red)";
    status.textContent = "Error: " + e.message;
  }
  btn.disabled = false;
  btn.textContent = "CONNECT";
}

async function resetPiholePassword() {
  const pw = document.getElementById("pihole-new-pw").value;
  const status = document.getElementById("pihole-pw-status");
  if (!pw) {
    status.style.color = "var(--red)";
    status.textContent = "Enter a new password";
    return;
  }
  status.style.color = "var(--text-dim)";
  status.textContent = "Changing...";
  try {
    const res = await fetch(ARSENAL_API + "/api/pihole/reset-password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ newPassword: pw }),
    });
    const data = await res.json();
    if (data.ok) {
      document.getElementById("pihole-new-pw").value = "";
      status.style.color = "var(--green)";
      status.textContent = "Pi-hole password changed";
      log("Pi-hole password updated", "success");
    } else {
      status.style.color = "var(--red)";
      status.textContent = data.error || "Failed to change password";
    }
  } catch (e) {
    status.style.color = "var(--red)";
    status.textContent = "Error: " + e.message;
  }
}

function updatePiholeSetupBanner(connected) {
  const banner = document.getElementById("pihole-setup");
  if (banner) banner.style.display = connected ? "none" : "block";
}

// ── Init ────────────────────────────────────────────────────

// Wrap fetch to redirect on 401 (session expired)
const _origFetch = window.fetch;
window.fetch = async function(...args) {
  const res = await _origFetch.apply(this, args);
  if (res.status === 401 && !args[0]?.toString().includes("/api/auth/login")) {
    window.location.href = "/login.html";
  }
  return res;
};

fetchArsenalStatus();
fetchClients();
fetchBandwidth();
fetchBlocked();
// Restore Enemy List expand state from localStorage. Default collapsed so
// users who don't want the surveillance-partner tally staring at them on
// every dashboard load don't have to see it. When expanded, fetchEnemies()
// runs; when collapsed, no backend poll happens.
(function restoreEnemiesPref() {
  let expanded = false;
  try { expanded = localStorage.getItem(ENEMIES_PREF_KEY) === "1"; } catch {}
  setEnemiesExpanded(expanded);
})();
fetchWgStatus();
// T-0071: track interval IDs so visibilitychange can pause polling when the tab
// is hidden — saves Pi CPU + bandwidth when dashboard is in a background tab.
// Pattern mirrors topology.js (line 768-778). Bound once across module lifetime.
var _arsenalTimers = [];
function _startArsenalPollers() {
  if (_arsenalTimers.length) return; // already polling
  _arsenalTimers.push(setInterval(fetchArsenalStatus, 10000));
  _arsenalTimers.push(setInterval(fetchClients, 15000));
  _arsenalTimers.push(setInterval(fetchBandwidth, 5000));
}
function _stopArsenalPollers() {
  _arsenalTimers.forEach(clearInterval);
  _arsenalTimers = [];
}
_startArsenalPollers();
document.addEventListener("visibilitychange", function() {
  if (document.hidden) {
    _stopArsenalPollers();
  } else {
    // Refetch once immediately so user sees fresh data when they return
    fetchArsenalStatus();
    fetchClients();
    fetchBandwidth();
    _startArsenalPollers();
  }
});

// A11y: custom toggle divs (.toggle-switch, .dn-check) are not real <input>s,
// so browsers never give them focus or keyboard activation for free. One-time
// wire-up applies role=switch, tabindex=0, aria-checked, and Space/Enter → click.
// aria-checked auto-syncs via MutationObserver on the .on class.
(function wireA11y() {
  function syncAria(el, onClass) {
    el.setAttribute("aria-checked", el.classList.contains(onClass) ? "true" : "false");
  }
  function initEl(el, onClass) {
    if (el.dataset.a11yWired === "1") return;
    el.dataset.a11yWired = "1";
    if (!el.hasAttribute("role")) el.setAttribute("role", "switch");
    if (!el.hasAttribute("tabindex")) el.setAttribute("tabindex", "0");
    syncAria(el, onClass);
    el.addEventListener("keydown", function(ev) {
      if (ev.key === " " || ev.key === "Enter") {
        ev.preventDefault();
        el.click();
      }
    });
    // Observe class changes so aria-checked tracks the visual state
    var mo = new MutationObserver(function() { syncAria(el, onClass); });
    mo.observe(el, { attributes: true, attributeFilter: ["class"] });
  }
  document.querySelectorAll(".toggle-switch").forEach(function(el) { initEl(el, "on"); });
  document.querySelectorAll(".dn-check").forEach(function(el) {
    // dn-check's "on" state lives on the parent .dreadnought-row; observe the row.
    if (el.dataset.a11yWired === "1") return;
    el.dataset.a11yWired = "1";
    if (!el.hasAttribute("role")) el.setAttribute("role", "switch");
    if (!el.hasAttribute("tabindex")) el.setAttribute("tabindex", "0");
    var row = el.closest(".dreadnought-row");
    var syncRow = function() { el.setAttribute("aria-checked", row && row.classList.contains("on") ? "true" : "false"); };
    syncRow();
    el.addEventListener("keydown", function(ev) {
      if (ev.key === " " || ev.key === "Enter") {
        ev.preventDefault();
        el.click();
      }
    });
    if (row) new MutationObserver(syncRow).observe(row, { attributes: true, attributeFilter: ["class"] });
  });
})();


// ── Security Scan (Lynis) ──────────────────────────────────

async function runSecurityScan() {
  const btn = document.getElementById("btn-securityscan");
  const result = document.getElementById("security-scan-result");
  btn.disabled = true;
  btn.textContent = "SCANNING...";
  result.innerHTML = '<div class="scan-progress"><span class="spinner"></span>Running full system audit... this takes about 60 seconds.</div>';
  log("Starting security scan...", "info");

  try {
    const res = await fetch(ARSENAL_API + "/api/security/scan");
    const data = await res.json();

    if (!data.ok) {
      result.innerHTML = `<div style="color:var(--red);font-size:11px;padding:8px 0">${escapeHtml(data.error)}</div>`;
      log("Scan failed: " + data.error, "error");
      btn.disabled = false;
      btn.textContent = "RUN SCAN";
      return;
    }

    let html = '<div class="scan-overview">';
    html += `<div class="scan-score-ring grade-${escapeHtml(data.grade)}">${escapeHtml(data.score)}</div>`;
    html += '<div class="scan-stats">';
    html += `<div><span class="label">Grade:</span> ${escapeHtml(data.grade)}</div>`;
    html += `<div><span class="label">Warnings:</span> <span style="color:${data.warnings.length > 0 ? 'var(--red)' : 'var(--green)'}">${data.warnings.length}</span></div>`;
    html += `<div><span class="label">Suggestions:</span> <span style="color:var(--amber)">${data.suggestions.length}</span></div>`;
    html += `<div><span class="label">Scanned:</span> ${new Date(data.scannedAt).toLocaleTimeString()}</div>`;
    html += '</div></div>';

    if (data.warnings.length > 0) {
      html += '<div class="scan-section"><div class="scan-section-title">WARNINGS</div>';
      for (const w of data.warnings) {
        html += `<div class="scan-item warning"><span class="scan-id">${escapeHtml(w.id)}</span>${escapeHtml(w.message)}`;
        if (w.fix) html += `<span class="scan-fix">${escapeHtml(w.fix)}</span>`;
        html += '</div>';
      }
      html += '</div>';
    }

    if (data.suggestions.length > 0) {
      const shown = data.suggestions.slice(0, 15);
      const remaining = data.suggestions.length - shown.length;
      html += '<div class="scan-section"><div class="scan-section-title">SUGGESTIONS</div>';
      for (const s of shown) {
        html += `<div class="scan-item"><span class="scan-id">${escapeHtml(s.id)}</span>${escapeHtml(s.message)}`;
        if (s.fix) html += `<span class="scan-fix">${escapeHtml(s.fix)}</span>`;
        html += '</div>';
      }
      if (remaining > 0) {
        html += `<div style="font-size:10px;color:var(--text-dim);padding:6px 8px">+ ${remaining} more suggestions</div>`;
      }
      html += '</div>';
    }

    if (data.warnings.length === 0 && data.suggestions.length === 0) {
      html += '<div style="color:var(--green);font-size:11px;padding:8px 0">No warnings or suggestions — system is hardened.</div>';
    }

    result.innerHTML = html;
    log(`Security scan complete — Score: ${data.score}/100 (Grade ${data.grade})`, data.warnings.length > 0 ? "warning" : "success");

  } catch (e) {
    result.innerHTML = `<div style="color:var(--red);font-size:11px;padding:8px 0">Error: ${escapeHtml(e.message)}</div>`;
    log("Scan error: " + e.message, "error");
  }

  btn.disabled = false;
  btn.textContent = "RUN SCAN";
}

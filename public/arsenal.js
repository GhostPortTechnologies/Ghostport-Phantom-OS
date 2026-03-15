/**
 * GhostPort Arsenal — UI Logic
 * Manages security tool toggles, DNS testing, client display, and scheduling
 */

const ARSENAL_API = "";
const DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

// ── Toggle helpers ──────────────────────────────────────────

function setToggle(id, on) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.toggle("on", on);
}

function setToggleLoading(id, loading) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.toggle("loading", loading);
}

// ── Arsenal status polling ──────────────────────────────────

function updateArsenalUI(data) {
  // Kill Switch
  setToggle("tog-killswitch", data.killSwitch);
  setToggle("tog-ks-auto", data.killSwitchAuto !== false);
  const ksStatus = document.getElementById("ks-status");
  if (data.killSwitchTripped) {
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

  // QUIC Block
  setToggle("tog-quicblock", data.quicBlock);
  const quicStatus = document.getElementById("quic-status");
  if (quicStatus) {
    quicStatus.className = "arsenal-status " + (data.quicBlock ? "on" : "off");
    quicStatus.textContent = data.quicBlock ? "BLOCKING" : "OFF";
  }

  // Encrypted DNS
  setToggle("tog-encrypteddns", data.encryptedDns);
  const ednsStatus = document.getElementById("edns-status");
  ednsStatus.className = "arsenal-status " + (data.encryptedDns ? "on" : "off");
  ednsStatus.textContent = data.encryptedDns ? "DoH ACTIVE" : "CLEARTEXT";

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
    return;
  }
  list.innerHTML = clients.map(c =>
    `<div class="client-row"><span class="client-host">${c.hostname}</span><span class="client-ip">${c.ip}</span><span class="client-mac">${c.mac}</span></div>`
  ).join("");
}

// ── Toggle actions ──────────────────────────────────────────

async function arsenalToggle(feature) {
  const toggleMap = {
    killswitch: { endpoint: "/api/arsenal/killswitch", key: "killSwitch", togId: "tog-killswitch" },
    quicblock: { endpoint: "/api/arsenal/quicblock", key: "quicBlock", togId: "tog-quicblock" },
    encrypteddns: { endpoint: "/api/arsenal/encrypteddns", key: "encryptedDns", togId: "tog-encrypteddns" },
    macrandom: { endpoint: "/api/arsenal/macrandom", key: "macRandomization", togId: "tog-macrandom" },
    terminalmode: { endpoint: "/api/terminal-mode", key: "terminalMode", togId: "tog-terminalmode" },
  };

  const cfg = toggleMap[feature];
  if (!cfg) return;

  const toggle = document.getElementById(cfg.togId);
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
        <div>${data.passed ? "PASS" : "FAIL"} — ${data.reason}</div>
        <div style="margin-top:4px;color:var(--text-dim)">Local resolver: ${data.localResolver} | Direct: ${data.directResolver}</div>
        <div style="color:var(--text-dim)">Mode: ${data.mode} | Encrypted: ${data.encrypted ? "Yes" : "No"}</div>
      </div>`;
      log(`DNS test: ${data.passed ? "PASS" : "FAIL"} — ${data.reason}`, data.passed ? "success" : "error");
    } else {
      result.innerHTML = `<div class="dns-result fail">Error: ${data.error}</div>`;
    }
  } catch (e) {
    result.innerHTML = `<div class="dns-result fail">Error: ${e.message}</div>`;
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
      <span style="color:var(--green)">${s.time}</span>
      <span style="color:var(--text-dim)">${dayLabels}</span>
      <span style="color:var(--text)">${s.mode.toUpperCase()}</span>
      <button class="schedule-del" onclick="deleteSchedule('${s.id}')">&times;</button>
    </div>`;
  }).join("");
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
        <div><div style="font-size:10px;color:var(--text-faint)">DOWNLOAD</div><div style="font-size:18px;color:var(--green)">${data.download} <span style="font-size:11px">Mbps</span></div></div>
        <div><div style="font-size:10px;color:var(--text-faint)">UPLOAD</div><div style="font-size:18px;color:var(--green)">${data.upload} <span style="font-size:11px">Mbps</span></div></div>
        <div><div style="font-size:10px;color:var(--text-faint)">PING</div><div style="font-size:18px;color:var(--green)">${data.ping} <span style="font-size:11px">ms</span></div></div>
      </div><div style="font-size:10px;color:var(--text-faint);margin-top:4px">Server: ${data.server}</div>`;
      log(`Speed: ${data.download} Mbps down / ${data.upload} Mbps up / ${data.ping}ms`, "success");
    } else {
      result.innerHTML = `<div class="dns-result fail">${data.error}</div>`;
      log("Speed test failed: " + data.error, "error");
    }
  } catch (e) {
    result.innerHTML = `<div class="dns-result fail">Error: ${e.message}</div>`;
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
          <span style="color:var(--text)">${r.name}</span>
          <span style="color:var(--text-dim);margin-left:8px">${r.target}</span>
          <span style="float:right;color:${r.reachable ? 'var(--green)' : 'var(--red)'}">${latency}</span>
        </div>`;
      }).join("");
      log("Ping test complete", "success");
    } else {
      result.innerHTML = `<div class="dns-result fail">${data.error}</div>`;
    }
  } catch (e) {
    result.innerHTML = `<div class="dns-result fail">Error: ${e.message}</div>`;
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
        <div style="margin-top:4px;color:var(--text-dim);font-size:11px">Public IP: ${data.publicIp} | Mode: ${data.mode} | WG endpoint: ${data.wgEndpoint}</div>
      </div>`;
      log(`IP leak test: ${data.status}`, data.leaked ? "error" : "success");
    } else {
      result.innerHTML = `<div class="dns-result fail">${data.error}</div>`;
    }
  } catch (e) {
    result.innerHTML = `<div class="dns-result fail">Error: ${e.message}</div>`;
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

async function fetchBandwidth() {
  const result = document.getElementById("bandwidth-result");
  if (!result) return;
  try {
    const res = await fetch(ARSENAL_API + "/api/tools/bandwidth");
    const data = await res.json();
    if (data.ok) {
      const labels = { eth0: "WAN", wlan0: "WiFi AP", wg0: "WireGuard", tailscale0: "Tailscale" };
      result.innerHTML = Object.entries(data.interfaces).map(([iface, s]) =>
        `<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid #1a1a1a;font-size:11px">
          <span style="color:var(--green);min-width:80px">${labels[iface] || iface}</span>
          <span style="color:var(--text-dim)">↓ ${formatBytes(s.rx)}</span>
          <span style="color:var(--text-dim)">↑ ${formatBytes(s.tx)}</span>
        </div>`
      ).join("");
    }
  } catch (e) { /* silent */ }
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
          <span style="color:var(--red);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${q.domain}</span>
          <span style="color:var(--text-faint);margin-left:8px;white-space:nowrap">${q.client}</span>
        </div>`
      ).join("");
    } else if (data.ok) {
      result.innerHTML = '<div style="font-size:10px;color:var(--text-faint)">No blocked queries found</div>';
    } else {
      result.innerHTML = `<div style="font-size:10px;color:var(--red)">${data.error}</div>`;
    }
  } catch (e) {
    result.innerHTML = `<div style="font-size:10px;color:var(--red)">Error: ${e.message}</div>`;
  }
  btn.disabled = false;
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
      result.innerHTML = `<div class="dns-result pass">${data.status}</div>`;
      if (data.output) result.innerHTML += `<pre style="font-size:9px;color:var(--text-dim);margin-top:4px;white-space:pre-wrap">${data.output}</pre>`;
      log("System update complete", "success");
    } else {
      result.innerHTML = `<div class="dns-result fail">${data.error || data.status}</div>`;
      log("System update failed", "error");
    }
  } catch (e) {
    result.innerHTML = `<div class="dns-result fail">Error: ${e.message}</div>`;
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
    }
  } catch (e) { /* silent */ }
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

fetchArsenalStatus();
fetchClients();
fetchBandwidth();
fetchBlocked();
fetchWgStatus();
setInterval(fetchArsenalStatus, 10000);
setInterval(fetchClients, 15000);
setInterval(fetchBandwidth, 30000);


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
      result.innerHTML = `<div style="color:var(--red);font-size:11px;padding:8px 0">${data.error}</div>`;
      log("Scan failed: " + data.error, "error");
      btn.disabled = false;
      btn.textContent = "RUN SCAN";
      return;
    }

    let html = '<div class="scan-overview">';
    html += `<div class="scan-score-ring grade-${data.grade}">${data.score}</div>`;
    html += '<div class="scan-stats">';
    html += `<div><span class="label">Grade:</span> ${data.grade}</div>`;
    html += `<div><span class="label">Warnings:</span> <span style="color:${data.warnings.length > 0 ? 'var(--red)' : 'var(--green)'}">${data.warnings.length}</span></div>`;
    html += `<div><span class="label">Suggestions:</span> <span style="color:var(--amber)">${data.suggestions.length}</span></div>`;
    html += `<div><span class="label">Scanned:</span> ${new Date(data.scannedAt).toLocaleTimeString()}</div>`;
    html += '</div></div>';

    if (data.warnings.length > 0) {
      html += '<div class="scan-section"><div class="scan-section-title">WARNINGS</div>';
      for (const w of data.warnings) {
        html += `<div class="scan-item warning"><span class="scan-id">${w.id}</span>${w.message}`;
        if (w.fix) html += `<span class="scan-fix">${w.fix}</span>`;
        html += '</div>';
      }
      html += '</div>';
    }

    if (data.suggestions.length > 0) {
      const shown = data.suggestions.slice(0, 15);
      const remaining = data.suggestions.length - shown.length;
      html += '<div class="scan-section"><div class="scan-section-title">SUGGESTIONS</div>';
      for (const s of shown) {
        html += `<div class="scan-item"><span class="scan-id">${s.id}</span>${s.message}`;
        if (s.fix) html += `<span class="scan-fix">${s.fix}</span>`;
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
    result.innerHTML = `<div style="color:var(--red);font-size:11px;padding:8px 0">Error: ${e.message}</div>`;
    log("Scan error: " + e.message, "error");
  }

  btn.disabled = false;
  btn.textContent = "RUN SCAN";
}

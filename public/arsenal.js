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
  const ksStatus = document.getElementById("ks-status");
  if (data.killSwitchTripped) {
    ksStatus.className = "arsenal-status tripped";
    ksStatus.textContent = "TRIPPED — VPN DOWN";
  } else if (data.killSwitch) {
    ksStatus.className = "arsenal-status on";
    ksStatus.textContent = "ARMED";
  } else {
    ksStatus.className = "arsenal-status off";
    ksStatus.textContent = "OFF";
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

  // Blocklist freq
  const freqSel = document.getElementById("sel-blockfreq");
  if (freqSel) freqSel.value = data.blocklistFreq || "weekly";

  // Client count
  document.getElementById("client-count").textContent = data.clientCount + " device" + (data.clientCount !== 1 ? "s" : "");

  // Schedules
  renderSchedules(data.schedules || []);
}

async function fetchArsenalStatus() {
  try {
    const res = await fetch(ARSENAL_API + "/api/arsenal/status");
    const data = await res.json();
    if (data.ok) updateArsenalUI(data);
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
    encrypteddns: { endpoint: "/api/arsenal/encrypteddns", key: "encryptedDns", togId: "tog-encrypteddns" },
    macrandom: { endpoint: "/api/arsenal/macrandom", key: "macRandomization", togId: "tog-macrandom" },
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

// ── Init ────────────────────────────────────────────────────

fetchArsenalStatus();
fetchClients();
setInterval(fetchArsenalStatus, 10000);
setInterval(fetchClients, 15000);

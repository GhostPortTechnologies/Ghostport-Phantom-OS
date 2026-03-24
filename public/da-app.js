const DA_API = "";
let sessionTimer = null, sessionExpiry = null, passcodeHideTimer = null;

async function daLogin() {
  const pw = document.getElementById("da-password").value;
  const status = document.getElementById("login-status");
  if (!pw) { status.textContent = "Password required"; return; }
  try {
    const res = await fetch(DA_API + "/da/api/auth", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: pw }),
    });
    const data = await res.json();
    if (data.ok) {
      document.getElementById("login-screen").style.display = "none";
      document.getElementById("da-dashboard").style.display = "block";
      startSessionTimer(data.expiresIn || 900);
      fetchDeviceInfo(); fetchLogs(); checkProvisionStatus();
    } else { status.textContent = data.error || "Authentication failed"; }
  } catch (e) { status.textContent = "Connection error"; }
}

function daLogout() {
  fetch(DA_API + "/da/api/logout", { method: "POST" });
  document.getElementById("da-dashboard").style.display = "none";
  document.getElementById("login-screen").style.display = "block";
  document.getElementById("da-password").value = "";
  if (sessionTimer) clearInterval(sessionTimer);
}

function startSessionTimer(seconds) {
  sessionExpiry = Date.now() + seconds * 1000;
  const bar = document.getElementById("session-bar");
  const total = seconds;
  sessionTimer = setInterval(() => {
    const rem = Math.max(0, sessionExpiry - Date.now()) / 1000;
    bar.style.width = (rem / total * 100) + "%";
    document.getElementById("session-expiry").textContent =
      Math.floor(rem/60) + ":" + Math.floor(rem%60).toString().padStart(2,"0");
    if (rem <= 0) { clearInterval(sessionTimer); daLogout(); document.getElementById("login-status").textContent = "Session expired"; }
    bar.style.background = rem < 120 ? "var(--red)" : rem < 300 ? "var(--amber)" : "var(--green)";
  }, 1000);
}

function esc(s) { return s == null ? "" : String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;"); }

async function fetchDeviceInfo() {
  try {
    const res = await fetch(DA_API + "/da/api/device-info");
    const data = await res.json();
    if (!data.ok) return;
    const d = data.device;
    const rows = [
      ["Hostname", d.hostname], ["Model", d.model], ["OS", d.os], ["Kernel", d.kernel],
      ["GhostPort Version", "1.1"], ["Uptime", d.uptime], ["Current Mode", d.mode],
      ["LAN IP (wlan0)", d.lanIp], ["WAN IP (eth0)", d.wanIp], ["Tailscale IP", d.tailscaleIp],
      ["WiFi MAC", d.wifiMac], ["Ethernet MAC", d.ethMac],
    ];
    document.getElementById("device-info").innerHTML = rows.map(([l,v]) =>
      `<div class="info-row"><span class="info-label">${esc(l)}</span><span class="info-value ${v&&v!=="N/A"?"green":"amber"}">${esc(v||"N/A")}</span></div>`
    ).join("");
  } catch {}
}

async function checkProvisionStatus() {
  try {
    const res = await fetch(DA_API + "/da/api/provision-status");
    const data = await res.json();
    if (data.ok && data.hasPasscode) document.getElementById("provision-warning").style.display = "block";
  } catch {}
}

async function provisionDevice() {
  const btn = document.getElementById("btn-provision");
  const status = document.getElementById("provision-status");
  const display = document.getElementById("passcode-display");
  if (!btn.dataset.confirmed) {
    btn.textContent = "CLICK AGAIN TO CONFIRM";
    btn.classList.add("btn-amber");
    btn.dataset.confirmed = "1";
    setTimeout(() => { btn.textContent = "PROVISION DEVICE"; btn.classList.remove("btn-amber"); delete btn.dataset.confirmed; }, 5000);
    return;
  }
  delete btn.dataset.confirmed;
  btn.disabled = true; btn.textContent = "PROVISIONING...";
  status.style.color = "var(--text-dim)";
  status.innerHTML = '<span class="spinner"></span> Generating passcode and configuring Pi-hole...';
  try {
    const res = await fetch(DA_API + "/da/api/provision", { method: "POST" });
    const data = await res.json();
    if (data.ok) {
      showPasscode(data.passcode, "Record this passcode.");
      status.style.color = "var(--green)";
      status.textContent = "Device provisioned. Pi-hole configured.";
      document.getElementById("provision-warning").style.display = "block";
      fetchLogs();
    } else { status.style.color = "var(--red)"; status.textContent = data.error || "Failed"; }
  } catch (e) { status.style.color = "var(--red)"; status.textContent = "Error: " + e.message; }
  btn.disabled = false; btn.textContent = "PROVISION DEVICE"; btn.classList.remove("btn-amber");
}

function showPasscode(code, msg) {
  const display = document.getElementById("passcode-display");
  const hint = document.getElementById("passcode-hint");
  document.getElementById("passcode-value").textContent = code;
  display.style.display = "block";
  if (passcodeHideTimer) clearTimeout(passcodeHideTimer);
  let countdown = 30;
  hint.textContent = msg + " Auto-hiding in 30s.";
  const ci = setInterval(() => { countdown--; hint.textContent = msg + ` Auto-hiding in ${countdown}s.`; if(countdown<=0) clearInterval(ci); }, 1000);
  passcodeHideTimer = setTimeout(() => {
    document.getElementById("passcode-value").textContent = "--- --- ---";
    hint.textContent = "Passcode hidden."; clearInterval(ci);
  }, 30000);
}

async function revealPasscode() {
  const btn = document.getElementById("btn-reveal");
  btn.disabled = true; btn.textContent = "LOADING...";
  try {
    const res = await fetch(DA_API + "/da/api/passcode");
    const data = await res.json();
    if (data.ok && data.passcode) showPasscode(data.passcode, "Current device passcode.");
    else {
      document.getElementById("passcode-value").textContent = "NOT SET";
      document.getElementById("passcode-display").style.display = "block";
      document.getElementById("passcode-hint").textContent = "No passcode set. Run provisioning first.";
    }
  } catch {}
  btn.disabled = false; btn.textContent = "REVEAL PASSCODE";
}

async function runQA() {
  const btn = document.getElementById("btn-qa");
  const results = document.getElementById("qa-results");
  btn.disabled = true; btn.textContent = "RUNNING...";
  results.innerHTML = '<div style="padding:12px;font-size:11px;color:var(--text-dim)"><span class="spinner"></span> Running 12-point checklist...</div>';
  try {
    const res = await fetch(DA_API + "/da/api/qa", { method: "POST" });
    const data = await res.json();
    if (data.ok) {
      let html = "", allPass = true;
      for (const c of data.checks) {
        const icon = c.status === "pass" ? "&#10003;" : c.status === "warn" ? "&#9888;" : "&#10007;";
        const color = c.status === "pass" ? "green" : c.status === "warn" ? "amber" : "red";
        if (c.status === "fail") allPass = false;
        html += `<div class="qa-item"><span class="qa-icon" style="color:var(--${color})">${icon}</span><span class="qa-name">${esc(c.name)}</span><span class="qa-status ${c.status}">${esc(c.detail||c.status.toUpperCase())}</span></div>`;
      }
      html += `<div class="qa-verdict ${allPass?'ship':'no-ship'}">${allPass?'READY TO SHIP':'DO NOT SHIP'}</div>`;
      results.innerHTML = html;
      fetchLogs();
    }
  } catch (e) { results.innerHTML = `<div style="color:var(--red)">${esc(e.message)}</div>`; }
  btn.disabled = false; btn.textContent = "RUN QA CHECKLIST";
}

async function fetchLogs() {
  try {
    const res = await fetch(DA_API + "/da/api/logs");
    const data = await res.json();
    if (!data.ok) return;
    const tog = document.getElementById("tog-logging");
    if (data.enabled) tog.classList.add("on"); else tog.classList.remove("on");
    const c = document.getElementById("log-entries");
    if (!data.logs || !data.logs.length) { c.innerHTML = '<div style="font-size:10px;color:var(--text-dim);padding:8px">No entries.</div>'; return; }
    c.innerHTML = data.logs.slice().reverse().map(l =>
      `<div class="log-entry ${l.type||""}"><span class="ts">${esc(l.timestamp)}</span>${esc(l.message)}</div>`
    ).join("");
  } catch {}
}

async function toggleLogging() {
  const tog = document.getElementById("tog-logging");
  const enabling = !tog.classList.contains("on");
  try {
    const res = await fetch(DA_API + "/da/api/logs/toggle", { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({enabled:enabling}) });
    const data = await res.json();
    if (data.ok) { if (enabling) tog.classList.add("on"); else tog.classList.remove("on"); }
  } catch {}
}

async function clearLogs() {
  if (!confirm("Clear all provisioning logs?")) return;
  try { await fetch(DA_API + "/da/api/logs/clear", { method: "POST" }); fetchLogs(); } catch {}
}

document.addEventListener("DOMContentLoaded", function() {
  document.getElementById("da-password").addEventListener("keydown", function(event) {
    if (event.key === "Enter") daLogin();
  });

  document.getElementById("btn-authenticate").addEventListener("click", daLogin);

  document.getElementById("btn-provision").addEventListener("click", provisionDevice);

  document.getElementById("btn-reveal").addEventListener("click", revealPasscode);

  document.getElementById("btn-qa").addEventListener("click", runQA);

  document.getElementById("tog-logging").addEventListener("click", toggleLogging);

  document.getElementById("btn-refresh-logs").addEventListener("click", fetchLogs);

  document.getElementById("btn-clear-logs").addEventListener("click", clearLogs);

  document.getElementById("btn-logout").addEventListener("click", function(e) {
    e.preventDefault();
    daLogout();
  });
});

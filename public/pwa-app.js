  const API_PORT = 4200;
  let currentStep = 1;
  let selectedMode = 'zerotrust';
  let useRemote = false;
  let statusInterval = null;
  const logs = [];

  function esc(s) { return s == null ? "" : String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;"); }

  // ── URL params ──
  const params = new URLSearchParams(window.location.search);
  const serialParam = params.get('serial') || 'GP-' + Math.random().toString(36).substr(2,6).toUpperCase();
  const isSetup = params.get('register') === 'true' || !localStorage.getItem('gp_registered');

  // ── Init ──
  window.addEventListener('load', () => {
    document.getElementById('unit-serial').textContent = serialParam;
    document.getElementById('unit-date').textContent = 'Manufactured: ' + new Date().toLocaleDateString('en-US', {year:'numeric',month:'long'});

    if (isSetup) {
      document.getElementById('setup-overlay').classList.remove('hidden');
      document.getElementById('app').classList.remove('visible');
    } else {
      launchDashboard();
    }
  });

  // ── SETUP WIZARD ──
  function nextStep() {
    if (currentStep === 2) {
      if (!document.getElementById('router-name').value.trim()) { alert('Please name your ship, Captain.'); return; }
      if (!document.getElementById('captain-email').value.includes('@')) { alert('Please enter a valid email address.'); return; }
    }
    if (currentStep === 3) {
      const pw = document.getElementById('dashboard-password').value;
      const cpw = document.getElementById('confirm-password').value;
      if (pw.length < 8) { alert('Password must be at least 8 characters.'); return; }
      if (pw !== cpw) { alert('Passwords do not match.'); return; }
    }
    currentStep++;
    showStep(currentStep);
  }

  function prevStep() {
    currentStep--;
    showStep(currentStep);
  }

  function showStep(n) {
    document.querySelectorAll('.step').forEach(s => s.classList.remove('active'));
    document.getElementById('step-' + n)?.classList.add('active');
    document.getElementById('progress-fill').style.width = (n / 5 * 100) + '%';
    window.scrollTo(0, 0);
  }

  function selectMode(mode) {
    selectedMode = mode;
    document.querySelectorAll('.mode-option').forEach(el => el.classList.remove('selected'));
    document.getElementById('mode-' + mode).classList.add('selected');
  }

  function showOS(os, clickedTab) {
    document.querySelectorAll('.os-tab').forEach(t => t.classList.remove('active'));
    clickedTab.classList.add('active');
    document.getElementById('ios-steps').style.display = os === 'ios' ? 'block' : 'none';
    document.getElementById('android-steps').style.display = os === 'android' ? 'block' : 'none';
  }

  async function completeSetup() {
    const name = document.getElementById('router-name').value.trim() || 'GhostPort Router';
    const email = document.getElementById('captain-email').value.trim();
    const password = document.getElementById('dashboard-password').value;

    // Save to localStorage
    localStorage.setItem('gp_registered', 'true');
    localStorage.setItem('gp_name', name);
    localStorage.setItem('gp_email', email);
    localStorage.setItem('gp_serial', serialParam);
    localStorage.setItem('gp_default_mode', selectedMode);

    // Send registration to Pi API
    try {
      await fetch(`http://${location.hostname}:${API_PORT}/api/register`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ serial: serialParam, name, email, defaultMode: selectedMode, password })
      });
    } catch (e) { /* offline registration stored locally */ }

    // Show summary
    const modeNames = { isp:'ISP — Open Waters', zerotrust:'Zero Trust — Ghost Cloak', doublehop:'Double Hop — Dead Man\'s Route', zhop:'Z-HOP — Davy Jones' };
    document.getElementById('summary-details').innerHTML = `
      <div class="detail-row"><span class="detail-key">Ship Name</span><span class="detail-val">${esc(name)}</span></div>
      <div class="detail-row"><span class="detail-key">Serial</span><span class="detail-val">${esc(serialParam)}</span></div>
      <div class="detail-row"><span class="detail-key">Captain</span><span class="detail-val">${esc(email)}</span></div>
      <div class="detail-row"><span class="detail-key">Default Mode</span><span class="detail-val">${modeNames[selectedMode]}</span></div>
      <div class="detail-row"><span class="detail-key">Status</span><span class="detail-val">☠ ACTIVE</span></div>
    `;

    currentStep = 'success';
    document.querySelectorAll('.step').forEach(s => s.classList.remove('active'));
    document.getElementById('step-success').classList.add('active');
    document.getElementById('progress-fill').style.width = '100%';
    window.scrollTo(0, 0);
  }

  function launchDashboard() {
    document.getElementById('setup-overlay').classList.add('hidden');
    document.getElementById('app').classList.add('visible');
    addLog('GhostPort Command Deck initialized', 'info');
    addLog('Serial: ' + (localStorage.getItem('gp_serial') || serialParam), 'info');
    fetchStatus();
    statusInterval = setInterval(fetchStatus, 10000);
  }

  // ── DASHBOARD ──
  const modeData = {
    isp:        { icon:'⚓', name:'ISP', sub:'Open Waters', privacy:15 },
    zerotrust:  { icon:'👻', name:'Zero Trust', sub:'Ghost Cloak', privacy:55 },
    doublehop:  { icon:'💀', name:'Double Hop', sub:"Dead Man's Route", privacy:80 },
    zhop:       { icon:'🏴‍☠️', name:'Z-HOP', sub:'Davy Jones', privacy:100 },
  };

  async function fetchStatus() {
    try {
      const baseUrl = `http://${location.hostname}:${API_PORT}`;
      const res = await fetch(`${baseUrl}/api/status`, { signal: AbortSignal.timeout(8000) });
      if (!res.ok) throw new Error();
      const data = await res.json();
      updateDashboard(data);
      document.getElementById('conn-pill').textContent = useRemote ? 'REMOTE' : 'LOCAL';
      document.getElementById('conn-pill').className = 'connection-pill connected';
    } catch {
      document.getElementById('conn-pill').textContent = 'OFFLINE';
      document.getElementById('conn-pill').className = 'connection-pill offline';
      addLog('Connection lost — retrying...', 'error');
    }
  }

  function updateDashboard(data) {
    const mode = data.activeMode || 'isp';
    const md = modeData[mode];

    // Banner
    document.getElementById('banner-icon').textContent = md.icon;
    document.getElementById('banner-mode').textContent = `${md.name} — ${md.sub}`;
    document.getElementById('banner-uptime').textContent = 'Uptime: ' + (data.uptime || '--');
    document.getElementById('banner-ip').textContent = data.ip || '--';

    // Cards
    Object.keys(modeData).forEach(m => {
      const card = document.getElementById('card-' + m);
      card.classList.toggle('active', m === mode);
      const badge = card.querySelector('.active-badge');
      if (badge) badge.remove();
      if (m === mode) {
        const b = document.createElement('div');
        b.className = 'active-badge';
        b.textContent = 'ACTIVE';
        card.insertBefore(b, card.firstChild);
      }
    });

    // Tunnels
    updateTunnel('wg', data.tunnels?.wg0);
    updateTunnel('ts', data.tunnels?.tailscale, true);
    updateTunnel('ph', data.tunnels?.pihole);

    // Stats
    document.getElementById('stat-ads').textContent = (data.adsBlocked || 0).toLocaleString();
    document.getElementById('stat-ip').textContent = data.ip || '--';
    document.getElementById('stat-ip-label').textContent = mode === 'isp' ? 'ISP IP' : 'masked';
    document.getElementById('stat-uptime').textContent = data.uptime || '--';
  }

  function updateTunnel(key, state, hasToggle = false) {
    const dot = document.getElementById('dot-' + key);
    const txt = document.getElementById('txt-' + key);
    const isUp = state === 'up';
    dot.className = 'status-dot ' + (isUp ? 'up' : 'down');
    txt.textContent = isUp ? 'UP' : 'DOWN';
    txt.className = 'status-text ' + (isUp ? 'up' : 'down');
    if (hasToggle) {
      const tog = document.getElementById('ts-toggle');
      tog.textContent = isUp ? 'ON' : 'OFF';
      tog.className = 'tailscale-toggle ' + (isUp ? 'on' : '');
    }
  }

  async function switchMode(mode) {
    if (mode === document.querySelector('.mode-card.active')?.id?.replace('card-','')) return;
    const overlay = document.getElementById('switching-overlay');
    const md = modeData[mode];
    document.getElementById('switching-text').textContent = 'ENGAGING ' + md.name.toUpperCase();
    overlay.classList.add('show');
    addLog('Switching to ' + md.name.toUpperCase() + '...', 'warn');

    try {
      const res = await fetch(`http://${location.hostname}:${API_PORT}/api/mode`, {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({mode}),
        signal: AbortSignal.timeout(15000)
      });
      const data = await res.json();
      if (data.ok) {
        addLog(md.name + ' active ✓', 'success');
        await fetchStatus();
      } else {
        addLog('ERROR: Mode switch failed', 'error');
      }
    } catch {
      addLog('ERROR: Connection timeout', 'error');
    }
    overlay.classList.remove('show');
  }

  async function toggleTailscale() {
    const isOn = document.getElementById('ts-toggle').classList.contains('on');
    const action = isOn ? 'stop' : 'start';
    addLog('Tailscale ' + action + 'ing...', 'warn');
    try {
      await fetch(`http://${location.hostname}:${API_PORT}/api/tailscale`, {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({action}),
        signal: AbortSignal.timeout(10000)
      });
      await fetchStatus();
      addLog('Tailscale ' + (isOn ? 'deactivated' : 'activated') + ' ✓', 'success');
    } catch {
      addLog('ERROR: Tailscale toggle failed', 'error');
    }
  }

  function toggleRemote() {
    useRemote = !useRemote;
    document.getElementById('conn-pill').textContent = useRemote ? 'REMOTE' : 'LOCAL';
    addLog('Switched to ' + (useRemote ? 'remote' : 'local') + ' connection', 'info');
    fetchStatus();
  }

  function openSettings() {
    const name = localStorage.getItem('gp_name') || 'Unknown';
    const serial = localStorage.getItem('gp_serial') || serialParam;
    alert(`⚙ Settings\n\nShip: ${name}\nSerial: ${serial}\n\nTo change settings, access the full dashboard at\nhttp://${location.hostname}:${API_PORT}`);
  }

  function addLog(msg, type) {
    const now = new Date();
    const time = now.getHours().toString().padLeft2() + ':' + now.getMinutes().toString().padLeft2();
    logs.push({time, msg, type});
    if (logs.length > 60) logs.shift();
    renderLog();
  }

  String.prototype.padLeft2 = function() { return this.padStart(2,'0'); };

  function renderLog() {
    const box = document.getElementById('log-box');
    if (!box) return;
    box.innerHTML = [...logs].reverse().map(l =>
      `<div class="log-entry"><span class="log-time">${l.time}</span><span class="log-msg ${l.type}">${esc(l.msg)}</span></div>`
    ).join('');
  }

  // ── EVENT BINDING (replaces inline onclick handlers) ──
  document.addEventListener('DOMContentLoaded', () => {
    // Step 1: next
    document.getElementById('btn-step1-next').addEventListener('click', () => nextStep());

    // Step 2: next + back
    document.getElementById('btn-step2-next').addEventListener('click', () => nextStep());
    document.getElementById('btn-step2-back').addEventListener('click', () => prevStep());

    // Step 3: next + back
    document.getElementById('btn-step3-next').addEventListener('click', () => nextStep());
    document.getElementById('btn-step3-back').addEventListener('click', () => prevStep());

    // Step 4: mode selection
    document.getElementById('mode-isp').addEventListener('click', () => selectMode('isp'));
    document.getElementById('mode-zerotrust').addEventListener('click', () => selectMode('zerotrust'));
    document.getElementById('mode-doublehop').addEventListener('click', () => selectMode('doublehop'));
    document.getElementById('mode-zhop').addEventListener('click', () => selectMode('zhop'));

    // Step 4: next + back
    document.getElementById('btn-step4-next').addEventListener('click', () => nextStep());
    document.getElementById('btn-step4-back').addEventListener('click', () => prevStep());

    // Step 5: OS tabs
    document.getElementById('os-tab-ios').addEventListener('click', function() { showOS('ios', this); });
    document.getElementById('os-tab-android').addEventListener('click', function() { showOS('android', this); });

    // Step 5: complete + back
    document.getElementById('btn-step5-complete').addEventListener('click', () => completeSetup());
    document.getElementById('btn-step5-back').addEventListener('click', () => prevStep());

    // Success: launch dashboard
    document.getElementById('btn-launch-dashboard').addEventListener('click', () => launchDashboard());

    // Dashboard nav
    document.getElementById('conn-pill').addEventListener('click', () => toggleRemote());
    document.getElementById('btn-settings').addEventListener('click', () => openSettings());

    // Mode cards
    document.getElementById('card-isp').addEventListener('click', () => switchMode('isp'));
    document.getElementById('card-zerotrust').addEventListener('click', () => switchMode('zerotrust'));
    document.getElementById('card-doublehop').addEventListener('click', () => switchMode('doublehop'));
    document.getElementById('card-zhop').addEventListener('click', () => switchMode('zhop'));

    // Tailscale toggle
    document.getElementById('ts-toggle').addEventListener('click', () => toggleTailscale());

    // Settings tab
    document.getElementById('tab-settings').addEventListener('click', () => openSettings());
  });

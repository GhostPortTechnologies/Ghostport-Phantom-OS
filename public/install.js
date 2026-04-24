(function() {
  const input = document.getElementById('license-key');
  const btn = document.getElementById('activate-btn');
  const btnText = document.getElementById('btn-text');
  const spinner = document.getElementById('spinner');
  const errorMsg = document.getElementById('error-msg');
  const activateView = document.getElementById('activate-view');
  const successView = document.getElementById('success-view');

  let credentials = null;

  // Auto-format: insert dashes every 4 alphanumeric characters
  input.addEventListener('input', function(e) {
    const raw = input.value.replace(/[^A-Za-z0-9]/g, '').toUpperCase().slice(0, 16);
    const parts = raw.match(/.{1,4}/g) || [];
    const formatted = parts.join('-');

    if (formatted !== input.value) {
      const cursorWasAtEnd = input.selectionStart >= input.value.length;
      input.value = formatted;
      if (cursorWasAtEnd) input.setSelectionRange(formatted.length, formatted.length);
    }

    btn.disabled = raw.length !== 16;
    errorMsg.style.display = 'none';
  });

  // Allow Enter key
  input.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' && !btn.disabled) activate();
  });

  btn.addEventListener('click', activate);

  async function activate() {
    const key = input.value.trim();
    if (key.length !== 19) return;

    btn.disabled = true;
    btnText.textContent = 'Activating...';
    spinner.style.display = 'inline-block';
    errorMsg.style.display = 'none';

    try {
      const res = await fetch('/api/fleet/activate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ license_key: key })
      });

      let data;
      try {
        data = await res.json();
      } catch {
        throw new Error('Server returned an invalid response. Please try again.');
      }

      if (!res.ok || !data.ok) {
        throw new Error(data.error || 'Activation failed. Please check your key and try again.');
      }

      credentials = data;
      showSuccess(data);

    } catch (err) {
      let msg = err.message;
      if (err.name === 'TypeError' && msg.includes('fetch')) {
        msg = 'Unable to reach the server. Make sure you are connected to the GhostPort network.';
      }
      errorMsg.textContent = msg;
      errorMsg.style.display = 'block';
      btn.disabled = false;
      btnText.textContent = 'Activate Device';
      spinner.style.display = 'none';
    }
  }

  function showSuccess(data) {
    document.getElementById('cred-passcode').textContent = data.passcode || '—';
    document.getElementById('cred-ssid').textContent = data.wifi_ssid || '—';
    document.getElementById('cred-wifi-pw').textContent = data.wifi_password || '—';
    document.getElementById('cred-fleet').textContent = data.fleet_status || 'registered';

    // Show TOTP setup if provided
    if (data.totp && data.totp.qrDataUri) {
      document.getElementById('cred-totp-qr').src = data.totp.qrDataUri;
      document.getElementById('cred-totp-secret').textContent = data.totp.secret.replace(/(.{4})/g, '$1 ').trim();
      var backupEl = document.getElementById('cred-totp-backup');
      backupEl.textContent = '';
      data.totp.backupCodes.forEach(function(c) {
        var span = document.createElement('span');
        span.style.padding = '1px 2px';
        span.textContent = c;
        backupEl.appendChild(span);
      });
      document.getElementById('totp-section').style.display = 'block';
    }

    activateView.style.display = 'none';
    successView.style.display = 'block';
  }

  // Copy all credentials
  document.getElementById('copy-all-btn').addEventListener('click', function() {
    if (!credentials) return;
    var lines = [
      'GhostPort Device Credentials',
      '============================',
      'Passcode:      ' + (credentials.passcode || '—'),
      'WiFi Network:  ' + (credentials.wifi_ssid || '—'),
      'WiFi Password: ' + (credentials.wifi_password || '—'),
      'Fleet Status:  ' + (credentials.fleet_status || '—')
    ];
    if (credentials.totp) {
      lines.push('');
      lines.push('Two-Factor Auth (TOTP)');
      lines.push('Secret Key:    ' + credentials.totp.secret);
      lines.push('Backup Codes:  ' + credentials.totp.backupCodes.join(', '));
    }
    const text = lines.join('\n');

    copyToClipboard(text, this);
  });

  function copyToClipboard(text, button) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function() {
        flashCopied(button);
      }).catch(function() {
        fallbackCopy(text, button);
      });
    } else {
      fallbackCopy(text, button);
    }
  }

  function fallbackCopy(text, button) {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); flashCopied(button); }
    catch { /* silent */ }
    document.body.removeChild(ta);
  }

  function flashCopied(button) {
    const orig = button.textContent;
    button.textContent = 'Copied';
    button.classList.add('copied');
    setTimeout(function() {
      button.textContent = orig;
      button.classList.remove('copied');
    }, 1500);
  }

  // Skip activation — use without subscription
  var skipLink = document.getElementById('skip-link');
  if (skipLink) {
    skipLink.addEventListener('click', async function(e) {
      e.preventDefault();
      try {
        const res = await fetch('/api/fleet/skip-activation', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' }
        });
        const data = await res.json();
        if (data.ok || res.status === 409) {
          window.location.href = '/login.html';
        } else {
          errorMsg.textContent = data.error || 'Failed to skip activation';
          errorMsg.style.display = 'block';
        }
      } catch {
        window.location.href = '/login.html';
      }
    });
  }
})();

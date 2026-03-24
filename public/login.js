async function doLogin(e) {
  e.preventDefault();
  const btn = document.getElementById("login-btn");
  const input = document.getElementById("passcode-input");
  const error = document.getElementById("login-error");

  btn.disabled = true;
  btn.textContent = "AUTHENTICATING...";
  error.textContent = "";

  try {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ passcode: input.value }),
    });
    const data = await res.json();
    if (data.ok) {
      if (data.csrfToken) localStorage.setItem("gp-csrf", data.csrfToken);
      window.location.href = "/";
    } else {
      error.textContent = data.error;
      input.value = "";
      input.focus();
    }
  } catch (err) {
    error.textContent = "Connection failed — is GhostPort running?";
  }

  btn.disabled = false;
  btn.textContent = "AUTHENTICATE";
}

// Theme engine — reads saved theme from dashboard localStorage
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
function applyVars(t) {
  const r = document.documentElement.style;
  r.setProperty("--green", t.accent); r.setProperty("--green-dim", t.dim);
  r.setProperty("--green-glow", t.glow); r.setProperty("--green-20", t.a20);
  r.setProperty("--green-33", t.a33); r.setProperty("--green-13", t.a13);
  r.setProperty("--bg", t.bg); r.setProperty("--bg2", t.bg2); r.setProperty("--bg3", t.bg3);
  r.setProperty("--border", t.border); r.setProperty("--text", t.text);
  r.setProperty("--text-dim", t.textDim); r.setProperty("--text-faint", t.textFaint);
}
let saved = localStorage.getItem("gp-theme") || "#39ff8f";
if (THEME_MIGRATE[saved]) { saved = THEME_MIGRATE[saved]; localStorage.setItem("gp-theme", saved); }
if (saved === "rainbow") {
  let hue = 0, breathPhase = 0, last = 0;
  function frame(ts) {
    if (ts - last < 33) { requestAnimationFrame(frame); return; }
    last = ts;
    hue = (hue + 0.015 * 33) % 360;
    breathPhase += 0.002 * 33;
    applyVars(hueToTheme(hue, (Math.sin(breathPhase) + 1) / 2));
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
} else if (saved !== "#39ff8f") {
  const hex = saved.startsWith("#") ? saved : "#39ff8f";
  const [h, s] = hex2hsl(hex);
  applyVars(hueToTheme(h, undefined, s));
}

document.getElementById("login-form").addEventListener("submit", doLogin);

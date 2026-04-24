/**
 * GhostPort Background Effects
 * Lightweight canvas/video background system for the dashboard
 * Performance-tuned for Raspberry Pi 5 (4GB RAM)
 */
var GhostBG = (function() {
  var canvas, ctx, raf, video;
  var effect = "solid";
  var particles = [];
  var matrixCols = [];
  var lastFrame = 0;
  var FPS_CAP = 30;
  var frameInterval = 1000 / FPS_CAP;
  var paused = false;

  // Get current accent color from CSS
  function getAccent() {
    var s = getComputedStyle(document.documentElement);
    return s.getPropertyValue("--green").trim() || "#39ff8f";
  }

  function hexToRgba(hex, a) {
    var r = parseInt(hex.slice(1,3), 16) || 57;
    var g = parseInt(hex.slice(3,5), 16) || 255;
    var b = parseInt(hex.slice(5,7), 16) || 143;
    return "rgba(" + r + "," + g + "," + b + "," + a + ")";
  }

  function init() {
    if (canvas) return;
    canvas = document.createElement("canvas");
    canvas.id = "bg-canvas";
    canvas.style.cssText = "position:fixed;inset:0;z-index:-1;pointer-events:none;width:100vw;height:100vh;";
    document.body.insertBefore(canvas, document.body.firstChild);
    ctx = canvas.getContext("2d");
    resize();
    window.addEventListener("resize", resize);
    document.addEventListener("visibilitychange", function() {
      paused = document.hidden;
      if (!paused && effect !== "solid") loop();
    });
  }

  function resize() {
    if (!canvas) return;
    // Half resolution for performance
    canvas.width = Math.floor(window.innerWidth / 2);
    canvas.height = Math.floor(window.innerHeight / 2);
  }

  function destroy() {
    if (raf) { cancelAnimationFrame(raf); raf = null; }
    if (canvas) { canvas.remove(); canvas = null; ctx = null; }
    if (video) { video.remove(); video = null; }
    particles = [];
    matrixCols = [];
  }

  function destroyVideo() {
    if (video) { video.pause(); video.remove(); video = null; }
  }

  function destroyCanvas() {
    if (raf) { cancelAnimationFrame(raf); raf = null; }
    if (canvas && ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
  }

  function loop(ts) {
    if (paused || effect === "solid") return;
    if (ts - lastFrame < frameInterval) { raf = requestAnimationFrame(loop); return; }
    lastFrame = ts;

    var w = canvas.width, h = canvas.height;
    ctx.clearRect(0, 0, w, h);

    switch(effect) {
      case "particles": drawParticles(w, h); break;
      case "matrix": drawMatrix(w, h); break;
      case "grid": drawGrid(w, h); break;
      case "waves": drawWaves(w, h, ts); break;
    }

    raf = requestAnimationFrame(loop);
  }

  // ── Particles ──────────────────────────────────
  function initParticles() {
    particles = [];
    var w = canvas.width, h = canvas.height;
    var count = Math.min(40, Math.floor((w * h) / 8000));
    for (var i = 0; i < count; i++) {
      particles.push({
        x: Math.random() * w, y: Math.random() * h,
        vx: (Math.random() - 0.5) * 0.5, vy: (Math.random() - 0.5) * 0.5,
        r: Math.random() * 1.5 + 0.5
      });
    }
  }

  function drawParticles(w, h) {
    var accent = getAccent();
    var connectDist = 80;

    for (var i = 0; i < particles.length; i++) {
      var p = particles[i];
      p.x += p.vx; p.y += p.vy;
      if (p.x < 0 || p.x > w) p.vx *= -1;
      if (p.y < 0 || p.y > h) p.vy *= -1;

      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fillStyle = hexToRgba(accent, 0.6);
      ctx.fill();

      // Connect nearby particles
      for (var j = i + 1; j < particles.length; j++) {
        var p2 = particles[j];
        var dx = p.x - p2.x, dy = p.y - p2.y;
        var dist = Math.sqrt(dx*dx + dy*dy);
        if (dist < connectDist) {
          ctx.beginPath();
          ctx.moveTo(p.x, p.y);
          ctx.lineTo(p2.x, p2.y);
          ctx.strokeStyle = hexToRgba(accent, 0.15 * (1 - dist / connectDist));
          ctx.lineWidth = 0.5;
          ctx.stroke();
        }
      }
    }
  }

  // ── Matrix Rain ────────────────────────────────
  function initMatrix() {
    var w = canvas.width;
    var colW = 14;
    var numCols = Math.min(60, Math.floor(w / colW));
    matrixCols = [];
    for (var i = 0; i < numCols; i++) {
      matrixCols.push({ x: i * colW, y: Math.random() * -100, speed: Math.random() * 2 + 1 });
    }
  }

  function drawMatrix(w, h) {
    var accent = getAccent();
    ctx.fillStyle = "rgba(6, 10, 6, 0.1)";
    ctx.fillRect(0, 0, w, h);

    ctx.font = "10px monospace";
    var chars = "01アイウエオカキクケコサシスセソ";
    for (var i = 0; i < matrixCols.length; i++) {
      var col = matrixCols[i];
      var ch = chars[Math.floor(Math.random() * chars.length)];
      ctx.fillStyle = hexToRgba(accent, 0.7);
      ctx.fillText(ch, col.x, col.y);
      col.y += col.speed * 8;
      if (col.y > h + 20) {
        col.y = Math.random() * -50;
        col.speed = Math.random() * 2 + 1;
      }
    }
  }

  // ── Grid ───────────────────────────────────────
  function drawGrid(w, h) {
    var accent = getAccent();
    var t = Date.now() * 0.0003;
    var spacing = 30;

    ctx.strokeStyle = hexToRgba(accent, 0.08);
    ctx.lineWidth = 0.5;

    // Horizontal lines with perspective
    for (var y = 0; y < h; y += spacing) {
      var offset = Math.sin(y * 0.01 + t) * 3;
      ctx.beginPath();
      ctx.moveTo(0, y + offset);
      ctx.lineTo(w, y + offset);
      ctx.stroke();
    }

    // Vertical lines
    for (var x = 0; x < w; x += spacing) {
      var offset2 = Math.cos(x * 0.01 + t) * 3;
      ctx.beginPath();
      ctx.moveTo(x + offset2, 0);
      ctx.lineTo(x + offset2, h);
      ctx.stroke();
    }

    // Horizon glow
    var grad = ctx.createRadialGradient(w/2, h*0.7, 0, w/2, h*0.7, w*0.4);
    grad.addColorStop(0, hexToRgba(accent, 0.06));
    grad.addColorStop(1, "transparent");
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, w, h);
  }

  // ── Waves ──────────────────────────────────────
  function drawWaves(w, h, ts) {
    var accent = getAccent();
    var t = ts * 0.001;

    for (var wave = 0; wave < 3; wave++) {
      ctx.beginPath();
      var yBase = h * (0.3 + wave * 0.2);
      var amp = 20 + wave * 10;
      var freq = 0.005 + wave * 0.002;
      var speed = t * (0.5 + wave * 0.3);

      for (var x = 0; x <= w; x += 2) {
        var y = yBase + Math.sin(x * freq + speed) * amp + Math.sin(x * freq * 2.3 + speed * 1.5) * (amp * 0.3);
        if (x === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }

      ctx.strokeStyle = hexToRgba(accent, 0.06 + wave * 0.02);
      ctx.lineWidth = 1;
      ctx.stroke();
    }
  }

  // ── Video Background ───────────────────────────
  function setVideoSrc(url) {
    destroyCanvas();
    if (!video) {
      video = document.createElement("video");
      video.style.cssText = "position:fixed;inset:0;z-index:-1;width:100vw;height:100vh;object-fit:cover;pointer-events:none;opacity:0.3;";
      video.muted = true;
      video.loop = true;
      video.playsInline = true;
      video.autoplay = true;
      document.body.insertBefore(video, document.body.firstChild);
    }
    video.src = url;
    video.play().catch(function() {});
  }

  // ── Public API ─────────────────────────────────
  return {
    set: function(eff) {
      effect = eff;
      destroyVideo();
      if (eff === "solid") {
        destroyCanvas();
        return;
      }
      if (eff === "video") {
        var url = localStorage.getItem("gp-bg-video-url");
        if (url) setVideoSrc(url);
        return;
      }
      // Canvas effects
      if (!canvas) init();
      resize();
      if (eff === "particles") initParticles();
      else if (eff === "matrix") initMatrix();
      lastFrame = 0;
      if (raf) cancelAnimationFrame(raf);
      raf = requestAnimationFrame(loop);
    },

    setVideo: function(url) {
      destroyCanvas();
      setVideoSrc(url);
    },

    destroy: destroy
  };
})();

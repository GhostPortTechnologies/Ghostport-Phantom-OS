/**
 * GhostPort Network Topology Visualization
 * Renders a live, interactive SVG network map into the dashboard.
 * Pure vanilla JS, no dependencies. Polls /api/status, /api/arsenal/clients, /api/system/health.
 *
 * Colors come from CSS custom properties set on :root (--green, --text, --bg2, etc.)
 * so the live theme picker auto-propagates without re-render.
 */
(function () {
  "use strict";

  const NS = "http://www.w3.org/2000/svg";
  const VIEWBOX_W = 1200, VIEWBOX_H = 600;

  // Polling intervals (ms)
  const POLL_STATUS = 5000, POLL_CLIENTS = 10000, POLL_HEALTH = 15000;
  const MAX_CLIENTS = 8;

  // ── SVG helpers ──────────────────────────────────────────────

  function el(tag, attrs, parent) {
    const e = document.createElementNS(NS, tag);
    if (attrs) Object.entries(attrs).forEach(([k, v]) => e.setAttribute(k, v));
    if (parent) parent.appendChild(e);
    return e;
  }

  function text(str, x, y, attrs, parent) {
    const t = el("text", { x, y, "font-size": "12", "text-anchor": "middle", ...attrs }, parent);
    t.textContent = str;
    return t;
  }

  // ── Icons (simple SVG path data) ────────────────────────────

  const ICONS = {
    globe: "M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 17.93c-3.95-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z",
    cable: "M20 5V4c0-.55-.45-1-1-1h-2c-.55 0-1 .45-1 1v1h-1v4c0 .55.45 1 1 1h1v7c0 1.1-.9 2-2 2s-2-.9-2-2V7c0-2.21-1.79-4-4-4S5 4.79 5 7v7H4c-.55 0-1 .45-1 1v4h1v1c0 .55.45 1 1 1h2c.55 0 1-.45 1-1v-1h1v-4c0-.55-.45-1-1-1H7V7c0-1.1.9-2 2-2s2 .9 2 2v10c0 2.21 1.79 4 4 4s4-1.79 4-4V10h1c.55 0 1-.45 1-1V5h-1z",
    wifi: "M1 9l2 2c4.97-4.97 13.03-4.97 18 0l2-2C16.93 2.93 7.08 2.93 1 9zm8 8l3 3 3-3c-1.65-1.66-4.34-1.66-6 0zm-4-4l2 2c2.76-2.76 7.24-2.76 10 0l2-2C15.14 9.14 8.87 9.14 5 13z",
    shield: "M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4zm0 10.99h7c-.53 4.12-3.28 7.79-7 8.94V12H5V6.3l7-3.11v8.8z",
    lock: "M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm-6 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm3.1-9H8.9V6c0-1.71 1.39-3.1 3.1-3.1 1.71 0 3.1 1.39 3.1 3.1v2z",
    server: "M20 13H4c-.55 0-1 .45-1 1v6c0 .55.45 1 1 1h16c.55 0 1-.45 1-1v-6c0-.55-.45-1-1-1zm-1 5h-2v-2h2v2zM20 3H4c-.55 0-1 .45-1 1v6c0 .55.45 1 1 1h16c.55 0 1-.45 1-1V4c0-.55-.45-1-1-1zm-1 5h-2V6h2v2z",
    device: "M21 2H3c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h7v2H8v2h8v-2h-2v-2h7c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm0 14H3V4h18v12z",
    cloud: "M19.35 10.04C18.67 6.59 15.64 4 12 4 9.11 4 6.6 5.64 5.35 8.04 2.34 8.36 0 10.91 0 14c0 3.31 2.69 6 6 6h13c2.76 0 5-2.24 5-5 0-2.64-2.05-4.78-4.65-4.96z",
  };

  function drawIcon(pathData, x, y, size, cls, parent) {
    const g = el("g", {
      transform: `translate(${x - size / 2},${y - size / 2}) scale(${size / 24})`,
      class: "topo-icon " + (cls || "")
    }, parent);
    el("path", { d: pathData, opacity: "0.9" }, g);
    return g;
  }

  // ── Curved path between two points ──────────────────────────

  function curvePath(x1, y1, x2, y2) {
    const dx = x2 - x1;
    const dy = y2 - y1;
    // Smooth cubic bezier with horizontal bias for horizontal runs,
    // vertical bias for vertical runs. Makes lines feel more natural.
    const horizontal = Math.abs(dx) >= Math.abs(dy);
    if (horizontal) {
      const cp = Math.abs(dx) * 0.4;
      return `M${x1},${y1} C${x1 + Math.sign(dx) * cp},${y1} ${x2 - Math.sign(dx) * cp},${y2} ${x2},${y2}`;
    } else {
      const cp = Math.abs(dy) * 0.4;
      return `M${x1},${y1} C${x1},${y1 + Math.sign(dy) * cp} ${x2},${y2 - Math.sign(dy) * cp} ${x2},${y2}`;
    }
  }

  // ── Node class ──────────────────────────────────────────────

  class TopoNode {
    constructor(svg, id, icon, label, x, y, opts = {}) {
      this.id = id;
      this.x = x; this.y = y;
      this.w = opts.w || 140; this.h = opts.h || 60;
      this.detail = opts.detail || "";
      this.active = opts.active !== false;
      this.tooltipLines = opts.tooltipLines || [];
      this.onClick = opts.onClick || null;

      const activeCls = this.active ? "active" : "inactive";

      this.g = el("g", { class: "topo-node " + activeCls, "data-id": id, cursor: "pointer" }, svg);
      // Shadow
      el("rect", {
        x: x - this.w / 2 + 2, y: y - this.h / 2 + 2,
        width: this.w, height: this.h, rx: 10,
        class: "topo-shadow"
      }, this.g);
      // Body
      this.rect = el("rect", {
        x: x - this.w / 2, y: y - this.h / 2,
        width: this.w, height: this.h, rx: 10,
        class: "topo-node-body"
      }, this.g);
      // Icon
      drawIcon(icon, x - this.w / 2 + 22, y - 4, 18, "topo-node-icon", this.g);
      // Label
      this.labelEl = text(label, x + 10, y - 6, {
        "font-size": "11", "font-weight": "bold", class: "topo-node-label"
      }, this.g);
      // Detail line
      this.detailEl = text(this.detail, x + 10, y + 12, {
        "font-size": "9", class: "topo-node-detail"
      }, this.g);
      // Status dot
      this.dot = el("circle", {
        cx: x + this.w / 2 - 12, cy: y - this.h / 2 + 12, r: 4,
        class: "topo-dot"
      }, this.g);
    }

    setActive(on) {
      this.active = on;
      this.g.setAttribute("class", "topo-node " + (on ? "active" : "inactive"));
    }

    setDetail(str) {
      this.detail = str;
      this.detailEl.textContent = str;
    }

    setError() {
      this.g.setAttribute("class", "topo-node error");
    }

    getRight()  { return { x: this.x + this.w / 2, y: this.y }; }
    getLeft()   { return { x: this.x - this.w / 2, y: this.y }; }
    getTop()    { return { x: this.x, y: this.y - this.h / 2 }; }
    getBottom() { return { x: this.x, y: this.y + this.h / 2 }; }
  }

  // ── Connection line class ───────────────────────────────────

  class TopoLine {
    constructor(svg, id, fromPt, toPt, opts = {}) {
      this.id = id;
      this.active = opts.active !== false;
      this.encrypted = opts.encrypted || false;
      this.variant = opts.variant || "data"; // "data" | "control"

      const d = curvePath(fromPt.x, fromPt.y, toPt.x, toPt.y);
      const cls = [
        "topo-line",
        this.variant,
        this.active ? "active" : "inactive",
      ].join(" ");
      this.path = el("path", {
        d,
        class: cls,
        "marker-end": this.active ? (this.variant === "control" ? "url(#arrow-blue)" : "url(#arrow)") : ""
      }, svg);

      // Lock icon on encrypted lines
      if (this.encrypted && this.active) {
        const midX = (fromPt.x + toPt.x) / 2;
        const midY = (fromPt.y + toPt.y) / 2;
        const lockCls = this.variant === "control" ? "topo-lock-control" : "topo-lock-data";
        drawIcon(ICONS.lock, midX, midY - 10, 12, lockCls, svg);
      }
    }

    setActive(on) {
      this.active = on;
      this.path.setAttribute("class", ["topo-line", this.variant, on ? "active" : "inactive"].join(" "));
      this.path.setAttribute("marker-end", on ? (this.variant === "control" ? "url(#arrow-blue)" : "url(#arrow)") : "");
    }
  }

  // ── Tooltip ─────────────────────────────────────────────────

  class Tooltip {
    constructor(container) {
      this.el = document.createElement("div");
      this.el.className = "topo-tooltip";
      container.appendChild(this.el);
    }

    show(x, y, lines) {
      this.el.textContent = "";
      lines.forEach((l, i) => {
        if (i > 0) this.el.appendChild(document.createElement("br"));
        const span = document.createElement("span");
        if (l.startsWith("*")) {
          span.className = "topo-tooltip-title";
          span.textContent = l.slice(1);
        } else {
          span.textContent = l;
        }
        this.el.appendChild(span);
      });
      this.el.style.display = "block";
      this.el.style.left = x + "px";
      this.el.style.top = (y - 10) + "px";
    }

    hide() { this.el.style.display = "none"; }
  }

  // ── Main Topology Renderer ──────────────────────────────────

  class TopologyMap {
    constructor() {
      this.container = null;
      this.svg = null;
      this.tooltip = null;
      this.timers = [];
      this.state = { mode: "isp", tunnels: {}, ip: "", wan: {}, score: 0, breakdown: [], temp: null, clients: [] };
      this.nodes = {};
      this.lines = {};
      this.scoreOverlay = null;
    }

    init(containerId) {
      this.container = typeof containerId === "string" ? document.getElementById(containerId) : containerId;
      if (!this.container) { console.error("[Topology] Container not found:", containerId); return; }

      // Wrapper for relative positioning (tooltip)
      this.container.style.position = "relative";
      this.container.style.width = "100%";
      this.container.style.overflow = "hidden";

      // Inject CSS: all colors use CSS custom properties so the theme picker
      // (which rewrites :root --green, --text, etc.) applies without re-render.
      if (!document.getElementById("topo-styles")) {
        const style = document.createElement("style");
        style.id = "topo-styles";
        style.textContent = `
          /* Shadow under each node */
          .topo-shadow { fill: rgba(0,0,0,0.4); }

          /* Node body (rect) */
          .topo-node-body { fill: var(--bg2); stroke: var(--green-20); stroke-width: 1; }
          .topo-node.active .topo-node-body { stroke: var(--green-33); stroke-width: 1.5; }
          .topo-node:hover .topo-node-body {
            stroke: var(--green) !important;
            stroke-width: 2 !important;
            filter: drop-shadow(0 0 6px var(--green-glow));
          }

          /* Node label + detail + icon */
          .topo-node-label { fill: var(--green); font-family: 'Courier New', monospace; }
          .topo-node.inactive .topo-node-label { fill: var(--text-dim); }
          .topo-node-detail { fill: var(--text-dim); font-family: 'Courier New', monospace; }
          .topo-node-icon path { fill: var(--green); }
          .topo-node.inactive .topo-node-icon path { fill: var(--text-dim); }

          /* Status dot */
          .topo-dot { fill: var(--green); }
          .topo-node.inactive .topo-dot { fill: #333; }
          .topo-node.error .topo-dot { fill: var(--red); }

          /* Connection lines */
          .topo-line {
            fill: none;
            stroke: var(--green);
            stroke-width: 2;
            stroke-dasharray: 8 4;
            opacity: 0.9;
          }
          .topo-line.control { stroke: #00bcd4; }
          .topo-line.inactive {
            stroke: var(--text-faint);
            stroke-width: 1;
            stroke-dasharray: 4 4;
            opacity: 0.35;
          }
          .topo-line.active { animation: topo-dash 1.2s linear infinite; }
          @keyframes topo-dash { to { stroke-dashoffset: -24; } }

          /* Lock icons on encrypted lines */
          .topo-lock-data path { fill: var(--green); }
          .topo-lock-control path { fill: #00bcd4; }

          /* Mode label at top */
          .topo-mode-label {
            fill: var(--green);
            font-family: 'Courier New', monospace;
            font-weight: bold;
            letter-spacing: 2px;
          }

          /* Generic "dim text" label (e.g. +N more, No clients) */
          .topo-dim-label {
            fill: var(--text-dim);
            font-family: 'Courier New', monospace;
          }

          /* DNS lock badge */
          .topo-dns-badge { fill: var(--green); opacity: 0.15; }
          .topo-dns-badge-text { fill: var(--green); font-family: 'Courier New', monospace; font-weight: bold; }

          /* Arrow markers — use polyfill colors via marker attr; fill via CSS */
          .topo-arrow-data { fill: var(--green); }
          .topo-arrow-control { fill: #00bcd4; }

          /* Tooltip */
          .topo-tooltip {
            position: absolute; display: none; pointer-events: none;
            background: rgba(6, 10, 6, 0.95);
            border: 1px solid var(--green-33);
            border-radius: 6px;
            padding: 8px 12px;
            font-family: 'Courier New', monospace;
            font-size: 11px;
            color: var(--text);
            z-index: 100;
            max-width: 240px;
            line-height: 1.5;
            white-space: pre-line;
            box-shadow: 0 4px 12px rgba(0,0,0,0.6);
          }
          .topo-tooltip-title { color: var(--green); font-weight: bold; }

          /* Score overlay */
          .topo-score-overlay {
            position: absolute; top: 50%; left: 50%; transform: translate(-50%,-50%);
            background: rgba(6,10,6,0.95);
            border: 1px solid var(--green-33);
            border-radius: 10px;
            padding: 16px 24px;
            font-family: 'Courier New', monospace;
            color: var(--text);
            font-size: 11px;
            z-index: 200;
            box-shadow: 0 8px 24px rgba(0,0,0,0.7);
            min-width: 200px;
            cursor: pointer;
          }
          .topo-score-overlay h3 { color: var(--green); margin: 0 0 8px; font-size: 13px; }
          .topo-score-overlay .row { display: flex; justify-content: space-between; padding: 2px 0; }
          .topo-score-overlay .val { color: var(--green); }
          .topo-score-overlay .val.neg { color: var(--red); }
        `;
        document.head.appendChild(style);
      }

      this.tooltip = new Tooltip(this.container);
      this._render();
      this._poll();
    }

    destroy() {
      this.timers.forEach(clearInterval);
      this.timers = [];
      if (this.container) this.container.innerHTML = "";
      const style = document.getElementById("topo-styles");
      if (style) style.remove();
    }

    refresh() {
      this._fetchStatus();
      this._fetchClients();
      this._fetchHealth();
    }

    // ── Rendering ─────────────────────────────────────────────

    _render() {
      // Create SVG
      this.svg = el("svg", {
        viewBox: `0 0 ${VIEWBOX_W} ${VIEWBOX_H}`,
        width: "100%", preserveAspectRatio: "xMidYMid meet",
        style: "display:block;"
      });
      this.container.appendChild(this.svg);

      // Defs: arrow markers (fill via CSS class so theme colors apply)
      const defs = el("defs", {}, this.svg);
      const marker = el("marker", {
        id: "arrow", markerWidth: "8", markerHeight: "6",
        refX: "8", refY: "3", orient: "auto", markerUnits: "strokeWidth"
      }, defs);
      el("path", { d: "M0,0 L8,3 L0,6 Z", class: "topo-arrow-data" }, marker);

      const markerBlue = el("marker", {
        id: "arrow-blue", markerWidth: "8", markerHeight: "6",
        refX: "8", refY: "3", orient: "auto", markerUnits: "strokeWidth"
      }, defs);
      el("path", { d: "M0,0 L8,3 L0,6 Z", class: "topo-arrow-control" }, markerBlue);

      // Lines layer (drawn first, under nodes)
      this.lineLayer = el("g", { class: "topo-lines" }, this.svg);
      // Nodes layer
      this.nodeLayer = el("g", { class: "topo-nodes" }, this.svg);

      // Mode label (top center)
      this.modeLabel = text("Initializing...", VIEWBOX_W / 2, 30, {
        "font-size": "14", class: "topo-mode-label"
      }, this.svg);

      this._buildLayout();
      this._attachEvents();
    }

    _removeEvents() {
      if (this._onMouseMove) this.svg.removeEventListener("mousemove", this._onMouseMove);
      if (this._onMouseLeave) this.svg.removeEventListener("mouseleave", this._onMouseLeave);
      if (this._onClick) this.svg.removeEventListener("click", this._onClick);
    }

    _buildLayout() {
      // Clear layers
      this.lineLayer.innerHTML = "";
      this.nodeLayer.innerHTML = "";
      this.nodes = {};
      this.lines = {};

      const mode = this.state.mode;
      const t = this.state.tunnels || {};
      const tunnelActive = mode === "doublehop" || mode === "zhop";
      const dnsLocked = mode === "zerotrust" || mode === "zhop";

      // ── Symmetric column positions around Pi (x=600) ──
      const colInternet = 120;
      const colWan      = 330;
      const colPi       = 600;
      const colAp       = 870;
      const colClients  = 1080;
      const rowMain     = 270;

      // Tailscale above Pi (balances EC2 below), wg pair below Pi
      const rowTailscale = 120;
      const rowTunnels   = rowMain + 110;  // 380
      const rowEc2       = rowMain + 220;  // 490

      // ── Nodes ──

      // Internet
      this.nodes.internet = new TopoNode(this.nodeLayer, "internet", ICONS.globe, "INTERNET", colInternet, rowMain, {
        detail: this.state.ip || "...",
        tooltipLines: ["*Internet Gateway", `Public IP: ${this.state.ip || "unknown"}`]
      });

      // WAN (eth0 or wifi)
      const wanType = this.state.wan?.type || "ethernet";
      const wanLabel = wanType === "wifi" ? `WiFi: ${this.state.wan.ssid || "?"}` : "Ethernet";
      const wanIcon = wanType === "wifi" ? ICONS.wifi : ICONS.cable;
      this.nodes.wan = new TopoNode(this.nodeLayer, "wan", wanIcon, "WAN", colWan, rowMain, {
        detail: wanLabel,
        tooltipLines: ["*WAN Interface (eth0)", `Type: ${wanLabel}`]
      });

      // GhostPort Pi (center hub)
      const piDetail = `${this._modeName(mode)} | Score: ${this.state.score}`;
      this.nodes.pi = new TopoNode(this.nodeLayer, "pi", ICONS.shield, "GHOSTPORT", colPi, rowMain, {
        w: 160, h: 70,
        detail: piDetail,
        tooltipLines: [
          "*GhostPort Privacy Router",
          `Mode: ${this._modeName(mode)}`,
          `Security Score: ${this.state.score}/100`,
          this.state.temp != null ? `Temp: ${this.state.temp}\u00B0C` : null,
          "Click for score breakdown"
        ].filter(Boolean),
        onClick: () => this._showScoreOverlay()
      });

      // DNS lock badge above Pi (centered) for zerotrust/zhop
      if (dnsLocked) {
        const piGroup = this.nodes.pi.g;
        const bx = colPi, by = rowMain - 55;
        el("rect", {
          x: bx - 26, y: by - 9, width: 52, height: 16, rx: 8,
          class: "topo-dns-badge"
        }, piGroup);
        text("DNS \uD83D\uDD12", bx, by + 3, {
          "font-size": "9", class: "topo-dns-badge-text"
        }, piGroup);
      }

      // Access Point
      this.nodes.ap = new TopoNode(this.nodeLayer, "ap", ICONS.wifi, "ACCESS POINT", colAp, rowMain, {
        detail: "wlan0 (192.168.50.x)",
        tooltipLines: ["*Access Point (wlan0)", "SSID: GhostPortRouter", "Subnet: 192.168.50.0/24"]
      });

      // ── Tunnel plane nodes ──
      const wg0Active = tunnelActive && t.wg0 === "up";
      const wg1Active = tunnelActive && t.wg1 === "up";
      const tsActive  = t.tailscale === "up";

      // wg1 data plane (left of Pi)
      this.nodes.wg1 = new TopoNode(this.nodeLayer, "wg1", ICONS.lock, "DATA PLANE", colPi - 115, rowTunnels, {
        w: 130, active: wg1Active,
        detail: wg1Active ? "wg1 \u2022 10.66.67.x" : "wg1 \u2022 inactive",
        tooltipLines: ["*WireGuard Data Plane (wg1)", `Status: ${wg1Active ? "UP" : "DOWN"}`, "Peer: GhostPort data relay", "Subnet: 10.66.67.0/24"]
      });

      // wg0 control plane (right of Pi)
      this.nodes.wg0 = new TopoNode(this.nodeLayer, "wg0", ICONS.lock, "CONTROL PLANE", colPi + 115, rowTunnels, {
        w: 130, active: wg0Active,
        detail: wg0Active ? "wg0 \u2022 10.66.66.x" : "wg0 \u2022 inactive",
        tooltipLines: ["*WireGuard Control Plane (wg0)", `Status: ${wg0Active ? "UP" : "DOWN"}`, "Peer: GhostPort control relay", "Subnet: 10.66.66.0/24"]
      });

      // Fleet relay (centered below both wg nodes)
      this.nodes.ec2 = new TopoNode(this.nodeLayer, "ec2", ICONS.server, "FLEET RELAY", colPi, rowEc2, {
        active: wg0Active || wg1Active,
        detail: tunnelActive ? "GhostPort fleet" : "standby",
        tooltipLines: ["*Fleet Relay Server", "Control plane + data plane", "Managed by GhostPort"]
      });

      // Tailscale (above Pi, centered — visual counterweight to EC2)
      this.nodes.ts = new TopoNode(this.nodeLayer, "ts", ICONS.cloud, "TAILSCALE", colPi, rowTailscale, {
        w: 130, active: tsActive,
        detail: tsActive ? "Always-on mgmt" : "down",
        tooltipLines: ["*Tailscale (tailscale0)", `Status: ${tsActive ? "UP" : "DOWN"}`, "Management plane", "Always-on remote access"]
      });

      // ── Clients ──
      this._renderClients(colClients, rowMain);

      // ── Connection lines (drawn in lineLayer, under nodes) ──

      // Main horizontal: Internet → WAN (always active)
      this.lines.inetWan = new TopoLine(this.lineLayer, "inetWan",
        this.nodes.internet.getRight(), this.nodes.wan.getLeft(), { active: true });

      if (tunnelActive) {
        // Tunnel modes: client traffic flows AP → Pi → wg1 → EC2 → internet
        // Visual arrows point outward (toward egress).
        this.lines.apPi = new TopoLine(this.lineLayer, "apPi",
          this.nodes.ap.getLeft(), this.nodes.pi.getRight(), { active: true });

        // WAN → Pi: physical link still carries traffic (inactive style = subtle)
        this.lines.wanPi = new TopoLine(this.lineLayer, "wanPi",
          this.nodes.wan.getRight(), this.nodes.pi.getLeft(), { active: false });

        // Pi → wg1 (data): left-bottom anchor on Pi → top of wg1
        this.lines.piWg1 = new TopoLine(this.lineLayer, "piWg1",
          { x: this.nodes.pi.x - 40, y: this.nodes.pi.y + this.nodes.pi.h / 2 },
          this.nodes.wg1.getTop(),
          { active: wg1Active, encrypted: true, variant: "data" });

        // Pi → wg0 (control): right-bottom anchor on Pi → top of wg0
        this.lines.piWg0 = new TopoLine(this.lineLayer, "piWg0",
          { x: this.nodes.pi.x + 40, y: this.nodes.pi.y + this.nodes.pi.h / 2 },
          this.nodes.wg0.getTop(),
          { active: wg0Active, encrypted: true, variant: "control" });

        // wg1 → EC2 (converges toward center-top of EC2, left side)
        this.lines.wg1Ec2 = new TopoLine(this.lineLayer, "wg1Ec2",
          this.nodes.wg1.getBottom(),
          { x: this.nodes.ec2.x - 40, y: this.nodes.ec2.y - this.nodes.ec2.h / 2 },
          { active: wg1Active, encrypted: true, variant: "data" });

        // wg0 → EC2 (converges toward center-top of EC2, right side)
        this.lines.wg0Ec2 = new TopoLine(this.lineLayer, "wg0Ec2",
          this.nodes.wg0.getBottom(),
          { x: this.nodes.ec2.x + 40, y: this.nodes.ec2.y - this.nodes.ec2.h / 2 },
          { active: wg0Active, encrypted: true, variant: "control" });

      } else {
        // ISP / ZeroTrust: direct path Internet → WAN → Pi → AP → Clients
        this.lines.wanPi = new TopoLine(this.lineLayer, "wanPi",
          this.nodes.wan.getRight(), this.nodes.pi.getLeft(), { active: true });

        this.lines.piAp = new TopoLine(this.lineLayer, "piAp",
          this.nodes.pi.getRight(), this.nodes.ap.getLeft(), { active: true });

        // Tunnels shown but inactive (symmetric, anchored to Pi bottom left/right)
        this.lines.piWg1 = new TopoLine(this.lineLayer, "piWg1",
          { x: this.nodes.pi.x - 40, y: this.nodes.pi.y + this.nodes.pi.h / 2 },
          this.nodes.wg1.getTop(),
          { active: false, variant: "data" });
        this.lines.piWg0 = new TopoLine(this.lineLayer, "piWg0",
          { x: this.nodes.pi.x + 40, y: this.nodes.pi.y + this.nodes.pi.h / 2 },
          this.nodes.wg0.getTop(),
          { active: false, variant: "control" });
      }

      // Tailscale: Pi → Tailscale (always present, active if TS is up)
      this.lines.piTs = new TopoLine(this.lineLayer, "piTs",
        this.nodes.pi.getTop(),
        this.nodes.ts.getBottom(),
        { active: tsActive, variant: "control" });

      // Client lines: AP → each client
      this._renderClientLines(colAp, colClients, rowMain);

      // Update mode label
      this._updateModeLabel();
    }

    _renderClients(cx, baseY) {
      const clients = this.state.clients || [];
      const count = Math.min(clients.length, MAX_CLIENTS);
      const spacing = 56;
      const startY = baseY - ((count - 1) * spacing) / 2;

      for (let i = 0; i < count; i++) {
        const c = clients[i];
        const y = startY + i * spacing;
        const rawName = c.hostname || "Unknown";
        const name = rawName.length > 12 ? rawName.slice(0, 11) + "\u2026" : rawName;
        this.nodes[`client${i}`] = new TopoNode(this.nodeLayer, `client${i}`, ICONS.device, name, cx, y, {
          w: 130, h: 44,
          detail: c.ip,
          tooltipLines: [
            `*${c.hostname}`,
            `IP: ${c.ip}`,
            `MAC: ${c.mac}`,
            c.macRandomized ? "MAC: Randomized \u2713" : "MAC: Hardware"
          ]
        });
      }

      // "+N more" label
      if (clients.length > MAX_CLIENTS) {
        const y = startY + count * spacing;
        text(`+${clients.length - MAX_CLIENTS} more`, cx, y, {
          "font-size": "10", class: "topo-dim-label"
        }, this.nodeLayer);
      }

      // "No clients" placeholder
      if (clients.length === 0) {
        text("No clients connected", cx, baseY, {
          "font-size": "10", class: "topo-dim-label", "font-style": "italic"
        }, this.nodeLayer);
      }
    }

    _renderClientLines(apX, clientX, baseY) {
      const clients = this.state.clients || [];
      const count = Math.min(clients.length, MAX_CLIENTS);
      const apRight = this.nodes.ap.getRight();

      for (let i = 0; i < count; i++) {
        const node = this.nodes[`client${i}`];
        if (!node) continue;
        new TopoLine(this.lineLayer, `apClient${i}`, apRight, node.getLeft(), { active: true });
      }
    }

    _modeName(mode) {
      return { isp: "ISP", zerotrust: "ZeroTrust", doublehop: "DoubleHop", zhop: "ZHop" }[mode] || mode;
    }

    _modeDescription(mode) {
      return {
        isp: "DIRECT \u2014 NO TUNNEL",
        zerotrust: "DNS LOCKED",
        doublehop: "DOUBLE TUNNEL ACTIVE",
        zhop: "MAXIMUM PRIVACY"
      }[mode] || "UNKNOWN";
    }

    _updateModeLabel() {
      if (this.modeLabel) {
        this.modeLabel.textContent = `\u25C8 ${this._modeDescription(this.state.mode)} \u25C8`;
      }
    }

    // ── Score Overlay ─────────────────────────────────────────

    _showScoreOverlay() {
      if (this.scoreOverlay) { this.scoreOverlay.remove(); this.scoreOverlay = null; return; }

      const bd = this.state.breakdown || [];
      const ov = document.createElement("div");
      ov.className = "topo-score-overlay";

      const nameMap = {
        mode: "Privacy Mode", encryptedDns: "Encrypted DNS", wgHandshake: "WG Handshake",
        tailscale: "Tailscale", pihole: "Pi-hole", killSwitch: "Kill Switch",
        macRandomization: "MAC Randomization", webrtcBlock: "WebRTC Block",
        antiFingerprint: "Anti-Fingerprint", coverTraffic: "Cover Traffic"
      };

      const h3 = document.createElement("h3");
      h3.textContent = `Security Score: ${Number(this.state.score)}/100`;
      ov.appendChild(h3);

      bd.forEach(b => {
        const label = nameMap[b.name] || String(b.name).replace(/[<>&"]/g, "");
        const val = Number(b.value) || 0;
        const sign = val >= 0 ? "+" : "";
        const row = document.createElement("div");
        row.className = "row";
        const nameSpan = document.createElement("span");
        nameSpan.textContent = label;
        const valSpan = document.createElement("span");
        valSpan.className = "val" + (val < 0 ? " neg" : "");
        valSpan.textContent = `${sign}${val}`;
        row.appendChild(nameSpan);
        row.appendChild(valSpan);
        ov.appendChild(row);
      });

      const hint = document.createElement("div");
      hint.style.cssText = "margin-top:8px; font-size:9px; text-align:center; opacity:0.6;";
      hint.textContent = "click to close";
      ov.appendChild(hint);
      ov.addEventListener("click", () => { ov.remove(); this.scoreOverlay = null; });
      this.container.appendChild(ov);
      this.scoreOverlay = ov;
    }

    // ── Events ────────────────────────────────────────────────

    _attachEvents() {
      this._removeEvents();

      this._onMouseMove = (e) => {
        const rect = this.svg.getBoundingClientRect();
        const scaleX = rect.width / VIEWBOX_W;
        const scaleY = rect.height / VIEWBOX_H;

        // Walk up from target to find topo-node group
        let target = e.target;
        while (target && target !== this.svg) {
          if (target.classList && target.classList.contains("topo-node")) {
            const id = target.getAttribute("data-id");
            const node = Object.values(this.nodes).find(n => n.id === id);
            if (node && node.tooltipLines.length) {
              const containerRect = this.container.getBoundingClientRect();
              const px = (node.x * scaleX) + rect.left - containerRect.left;
              const py = ((node.y - node.h / 2) * scaleY) + rect.top - containerRect.top;
              this.tooltip.show(px - 40, py - 10, node.tooltipLines);
            }
            return;
          }
          target = target.parentNode;
        }
        this.tooltip.hide();
      };
      this.svg.addEventListener("mousemove", this._onMouseMove);

      this._onMouseLeave = () => this.tooltip.hide();
      this.svg.addEventListener("mouseleave", this._onMouseLeave);

      this._onClick = (e) => {
        let target = e.target;
        while (target && target !== this.svg) {
          if (target.classList && target.classList.contains("topo-node")) {
            const id = target.getAttribute("data-id");
            const node = Object.values(this.nodes).find(n => n.id === id);
            if (node && node.onClick) node.onClick();
            return;
          }
          target = target.parentNode;
        }
        // Click on background closes score overlay
        if (this.scoreOverlay) { this.scoreOverlay.remove(); this.scoreOverlay = null; }
      };
      this.svg.addEventListener("click", this._onClick);
    }

    // ── Data Fetching ─────────────────────────────────────────

    _poll() {
      if (this.timers.length) return;

      this._fetchStatus();
      this._fetchClients();
      this._fetchHealth();

      this.timers.push(setInterval(() => this._fetchStatus(), POLL_STATUS));
      this.timers.push(setInterval(() => this._fetchClients(), POLL_CLIENTS));
      this.timers.push(setInterval(() => this._fetchHealth(), POLL_HEALTH));

      if (!this._visibilityBound) {
        this._visibilityBound = true;
        document.addEventListener("visibilitychange", () => {
          if (document.hidden) {
            this.timers.forEach(clearInterval);
            this.timers = [];
          } else {
            this._poll();
          }
        });
      }
    }

    async _fetchStatus() {
      try {
        const r = await fetch("/api/status", { credentials: "same-origin" });
        if (!r.ok) return;
        const d = await r.json();
        if (!d.ok) return;

        const changed = d.activeMode !== this.state.mode ||
          JSON.stringify(d.tunnels) !== JSON.stringify(this.state.tunnels);

        this.state.mode = d.activeMode || "isp";
        this.state.tunnels = d.tunnels || {};
        this.state.ip = d.ip || "unknown";
        this.state.wan = d.wan || { type: "ethernet" };
        this.state.score = d.securityScore || 0;
        this.state.breakdown = d.scoreBreakdown || [];

        if (changed) {
          this._buildLayout();
          this._attachEvents();
        } else {
          this._updateQuick();
        }
      } catch { /* API unavailable, keep last state */ }
    }

    async _fetchClients() {
      try {
        const r = await fetch("/api/arsenal/clients", { credentials: "same-origin" });
        if (!r.ok) return;
        const d = await r.json();
        if (!d.ok) return;

        const newCount = (d.clients || []).length;
        const oldCount = this.state.clients.length;
        this.state.clients = d.clients || [];

        if (newCount !== oldCount) {
          this._buildLayout();
          this._attachEvents();
        }
      } catch { /* keep last state */ }
    }

    async _fetchHealth() {
      try {
        const r = await fetch("/api/system/health", { credentials: "same-origin" });
        if (!r.ok) return;
        const d = await r.json();
        this.state.temp = d.temp_c;

        // Update Pi node detail if available
        if (this.nodes.pi) {
          const tempStr = this.state.temp != null ? ` | ${this.state.temp}\u00B0C` : "";
          this.nodes.pi.setDetail(`${this._modeName(this.state.mode)} | Score: ${this.state.score}${tempStr}`);
          // Update tooltip
          this.nodes.pi.tooltipLines = [
            "*GhostPort Privacy Router",
            `Mode: ${this._modeName(this.state.mode)}`,
            `Security Score: ${this.state.score}/100`,
            this.state.temp != null ? `Temp: ${this.state.temp}\u00B0C` : null,
            d.mem_used_pct != null ? `Memory: ${d.mem_used_pct}%` : null,
            d.disk_used_pct != null ? `Disk: ${d.disk_used_pct}%` : null,
            "Click for score breakdown"
          ].filter(Boolean);
        }
      } catch { /* keep last state */ }
    }

    /** Quick update without full rebuild (score, IP, etc.) */
    _updateQuick() {
      if (this.nodes.internet) this.nodes.internet.setDetail(this.state.ip || "...");
      if (this.nodes.pi) {
        const tempStr = this.state.temp != null ? ` | ${this.state.temp}\u00B0C` : "";
        this.nodes.pi.setDetail(`${this._modeName(this.state.mode)} | Score: ${this.state.score}${tempStr}`);
      }

      // Update WAN
      if (this.nodes.wan) {
        const wt = this.state.wan?.type || "ethernet";
        this.nodes.wan.setDetail(wt === "wifi" ? `WiFi: ${this.state.wan.ssid || "?"}` : "Ethernet");
      }

      // Update tunnel status dots
      const t = this.state.tunnels || {};
      if (this.nodes.wg0) this.nodes.wg0.setActive((this.state.mode === "doublehop" || this.state.mode === "zhop") && t.wg0 === "up");
      if (this.nodes.wg1) this.nodes.wg1.setActive((this.state.mode === "doublehop" || this.state.mode === "zhop") && t.wg1 === "up");
      if (this.nodes.ts) this.nodes.ts.setActive(t.tailscale === "up");
      if (this.nodes.ec2) this.nodes.ec2.setActive(t.wg0 === "up" || t.wg1 === "up");

      this._updateModeLabel();
    }
  }

  // ── Public API ──────────────────────────────────────────────

  const instance = new TopologyMap();

  window.GhostPortTopology = {
    init(containerId) { instance.init(containerId); },
    destroy()         { instance.destroy(); },
    refresh()         { instance.refresh(); },
  };

})();

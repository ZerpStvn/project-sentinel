(() => {
  const alerts = new Map();
  const sensors = new Map();
  const SEV_RANK = { critical: 4, high: 3, medium: 2, low: 1, info: 0 };
  const MAX_ALERTS = 300;

  const recentReceipts = [];
  const recentLatencies = [];

  let soundEnabled = false;
  let audioCtx = null;
  let ws = null;
  let backoff = 500;
  let renderScheduled = false;
  let feedLive = false;

  const el = (id) => document.getElementById(id);
  const statusDot = el("status-dot");
  const connLabel = el("conn-label");
  const alertsList = el("alerts-list");
  const sitesGrid = el("sites-grid");
  const criticalBanner = el("critical-banner");
  const soundToggle = el("sound-toggle");
  const feedModal = el("feed-modal");
  const feedToggle = el("feed-toggle");
  const feedStartModal = el("feed-start-modal");

  function beep() {
    if (!soundEnabled) return;
    try {
      if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.type = "square";
      osc.frequency.value = 880;
      gain.gain.setValueAtTime(0.15, audioCtx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + 0.4);
      osc.connect(gain).connect(audioCtx.destination);
      osc.start();
      osc.stop(audioCtx.currentTime + 0.4);
    } catch (e) {}
  }

  soundToggle.addEventListener("click", () => {
    soundEnabled = !soundEnabled;
    if (soundEnabled && !audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    soundToggle.textContent = soundEnabled ? "Sound: on" : "Sound: off";
  });

  function sendFeedAction(live) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ action: live ? "feed_start" : "feed_stop" }));
    }
  }

  feedToggle.addEventListener("click", () => sendFeedAction(!feedLive));
  feedStartModal.addEventListener("click", () => sendFeedAction(true));

  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws/dashboard/`);

    ws.onopen = () => {
      statusDot.classList.add("connected");
      connLabel.textContent = "live";
      backoff = 500;
    };
    ws.onclose = () => {
      statusDot.classList.remove("connected");
      connLabel.textContent = "reconnecting";
      setTimeout(connect, backoff);
      backoff = Math.min(backoff * 1.7, 10000);
    };
    ws.onerror = () => ws.close();
    ws.onmessage = (evt) => handleMessage(JSON.parse(evt.data));
  }

  function addAlert(a) {
    alerts.set(a.id, a);
    if (alerts.size > MAX_ALERTS) {
      alerts.delete(alerts.keys().next().value);
    }
  }

  function setFeedLive(live) {
    feedLive = live;
    feedModal.classList.toggle("open", !live);
    feedToggle.textContent = live ? "Feed: live" : "Feed: stopped";
    feedToggle.classList.toggle("feed-live", live);
    feedToggle.classList.toggle("feed-stopped", !live);
  }

  function handleMessage(msg) {
    if (msg.type === "snapshot") {
      msg.alerts.forEach(addAlert);
      msg.sensors.forEach((s) => sensors.set(s.sensor_id, s));
      setFeedLive(!!msg.feed_live);
      scheduleRender();
    } else if (msg.type === "feed_status") {
      setFeedLive(!!msg.live);
    } else if (msg.type === "alert") {
      addAlert(msg.alert);
      recentReceipts.push(Date.now());
      if (typeof msg.alert.processing_latency_ms === "number") {
        recentLatencies.push(msg.alert.processing_latency_ms);
        if (recentLatencies.length > 200) recentLatencies.shift();
      }
      if (msg.alert.severity === "critical") beep();
      scheduleRender();
    } else if (msg.type === "alert_update") {
      alerts.set(msg.alert.id, msg.alert);
      scheduleRender();
    } else if (msg.type === "sensor") {
      sensors.set(msg.sensor.sensor_id, msg.sensor);
      scheduleRender();
    }
  }

  function scheduleRender() {
    if (renderScheduled) return;
    renderScheduled = true;
    requestAnimationFrame(() => {
      renderScheduled = false;
      render();
    });
  }

  function sendAction(action, id) {
    const existing = alerts.get(id);
    if (existing) {
      alerts.set(id, { ...existing, status: action === "ack" ? "acknowledged" : "resolved" });
      scheduleRender();
    }
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ action, id }));
    }
  }

  function fmtAgo(iso) {
    if (!iso) return "";
    const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
    if (s < 60) return `${s}s ago`;
    return `${Math.round(s / 60)}m ago`;
  }

  function render() {
    const rows = Array.from(alerts.values())
      .filter((a) => a.status !== "resolved")
      .sort((a, b) => {
        const r = (SEV_RANK[b.severity] ?? 0) - (SEV_RANK[a.severity] ?? 0);
        if (r !== 0) return r;
        return new Date(b.received_at) - new Date(a.received_at);
      });

    alertsList.innerHTML = rows.map((a) => `
      <div class="alert-row ${a.severity} status-${a.status}" data-id="${a.id}">
        <span class="sev ${a.severity}">${a.severity}</span>
        <div class="alert-main">
          <div class="type">${a.type} · ${a.site_id} / ${a.sensor_id}</div>
          <div class="meta">${fmtAgo(a.event_ts)} · confidence ${Math.round((a.confidence||0)*100)}% · latency ${Math.round(a.processing_latency_ms||0)}ms ${a.severity_hint ? `· hint: ${a.severity_hint}` : ""}</div>
        </div>
        <div class="actions">
          ${a.status === "active" ? `<button class="btn" onclick="window.__sentinel.ack(${a.id})">Ack</button>` : ""}
          <button class="btn" onclick="window.__sentinel.resolve(${a.id})">Resolve</button>
        </div>
      </div>
    `).join("") || `<div class="meta" style="padding:16px;">No active alerts.</div>`;

    const activeCount = rows.filter((a) => a.status === "active").length;
    el("m-active").textContent = activeCount;

    const unackedCritical = rows.some((a) => a.severity === "critical" && a.status === "active");
    criticalBanner.style.display = unackedCritical ? "block" : "none";

    renderSites();
    renderMetrics();
  }

  function renderSites() {
    const bySite = new Map();
    for (const s of sensors.values()) {
      if (!bySite.has(s.site_id)) bySite.set(s.site_id, []);
      bySite.get(s.site_id).push(s);
    }
    const siteIds = Array.from(bySite.keys()).sort();
    sitesGrid.innerHTML = siteIds.map((siteId) => {
      const list = bySite.get(siteId).sort((a, b) => a.sensor_id.localeCompare(b.sensor_id));
      const counts = { online: 0, silent: 0, offline: 0 };
      list.forEach((s) => counts[s.status] = (counts[s.status] || 0) + 1);
      const flagged = list.filter((s) => s.status !== "online");
      return `
        <div class="site-card">
          <div class="site-id">${siteId}</div>
          <div class="counts"><span class="online">${counts.online||0} online</span> · <span class="silent">${counts.silent||0} silent</span> · <span class="offline">${counts.offline||0} offline</span></div>
          ${flagged.length ? `<div>${flagged.map((s) => `<span class="sensor-chip ${s.status}">${s.sensor_id}</span>`).join("")}</div>` : ""}
        </div>`;
    }).join("") || `<div class="meta" style="padding:16px;">No sensor activity yet.</div>`;
  }

  function renderMetrics() {
    const now = Date.now();
    while (recentReceipts.length && now - recentReceipts[0] > 5000) recentReceipts.shift();
    const rate = (recentReceipts.length / 5).toFixed(1);
    el("m-rate").textContent = rate;

    const avgLatency = recentLatencies.length
      ? Math.round(recentLatencies.reduce((a, b) => a + b, 0) / recentLatencies.length)
      : 0;
    el("m-latency").textContent = avgLatency;
  }

  criticalBanner.addEventListener("click", () => {
    const firstCritical = document.querySelector(".alert-row.critical");
    if (firstCritical) firstCritical.scrollIntoView({ behavior: "smooth", block: "center" });
  });

  window.__sentinel = {
    ack: (id) => sendAction("ack", id),
    resolve: (id) => sendAction("resolve", id),
  };

  setInterval(renderMetrics, 1000);
  connect();
})();

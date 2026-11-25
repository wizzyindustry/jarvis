/* Jarvis HUD wallpaper.
 * Receives one-way events from the local Jarvis process over a WebSocket.
 * It never sends commands: the only message it sends is the auth token.
 * Open with ?demo=1 to preview without Jarvis running.
 */
(() => {
  "use strict";

  const params = new URLSearchParams(location.search);
  const DEMO = params.has("demo");
  const PORT = Number(params.get("port")) || (window.JARVIS && window.JARVIS.port) || 8765;
  const TOKEN = (window.JARVIS && window.JARVIS.token) || params.get("token") || "";
  const MAX_FEED = 60;
  const MODE_LABEL = {
    offline: "OFFLINE", idle: "ACTIVE MODE", sleeping: "STANDBY", listening: "LISTENING",
    thinking: "PROCESSING", speaking: "SPEAKING", approval: "AWAITING APPROVAL",
  };

  const $ = (id) => document.getElementById(id);
  const body = document.body;
  const core = $("core");

  // ---------- clock (runs even when Jarvis is offline) ----------
  const dateFmt = new Intl.DateTimeFormat("en-US", { weekday: "long", month: "short", day: "numeric", year: "numeric" });
  const pad = (n) => String(n).padStart(2, "0");
  function tickClock() {
    const d = new Date();
    $("clock").textContent = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
    $("date").textContent = dateFmt.format(d).toUpperCase();
  }
  tickClock();
  setInterval(tickClock, 1000);

  // ---------- rendering helpers (textContent only: feed text may come from emails) ----------
  function setState(state) {
    if (!MODE_LABEL[state]) state = "idle";
    body.dataset.state = state;
    $("core-mode").textContent = MODE_LABEL[state];
    if (state !== "speaking") setLevel(0);
  }

  function addFeed(text, kind) {
    const feed = $("feed");
    // keep following new lines unless the user has scrolled up to read older ones
    const atBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 24;
    const li = document.createElement("li");
    li.textContent = String(text).slice(0, 400);
    li.className = "new" + (kind ? " " + kind : "");
    feed.appendChild(li);
    while (feed.children.length > MAX_FEED) feed.removeChild(feed.firstChild);
    if (atBottom) feed.scrollTop = feed.scrollHeight;
    setTimeout(() => li.classList.remove("new"), 500);
  }

  function setBar(name, pct) {
    const v = Math.max(0, Math.min(100, Math.round(Number(pct) || 0)));
    $(name + "-val").textContent = v + "%";
    $(name + "-bar").style.width = v + "%";
  }

  // smoothed voice level drives the core's size while speaking
  let targetLevel = 0, shownLevel = 0, raf = 0;
  function setLevel(v) {
    targetLevel = Math.max(0, Math.min(1, Number(v) || 0));
    if (!raf) raf = requestAnimationFrame(animateLevel);
  }
  function animateLevel() {
    shownLevel += (targetLevel - shownLevel) * 0.35;
    core.style.setProperty("--core-scale", (1 + shownLevel * 0.09).toFixed(3));
    if (Math.abs(targetLevel - shownLevel) > 0.002) raf = requestAnimationFrame(animateLevel);
    else { raf = 0; core.style.setProperty("--core-scale", (1 + targetLevel * 0.09).toFixed(3)); }
  }

  function setConfig(cfg) {
    if (cfg.name) $("core-name").textContent = String(cfg.name).toUpperCase().slice(0, 12);
    if (cfg.location) {
      const loc = cfg.location;
      $("loc-name").textContent = String(loc.name || "").toUpperCase();
      if (typeof loc.lat === "number" && typeof loc.lon === "number") {
        const lat = `${Math.abs(loc.lat).toFixed(4)}° ${loc.lat >= 0 ? "N" : "S"}`;
        const lon = `${Math.abs(loc.lon).toFixed(4)}° ${loc.lon >= 0 ? "E" : "W"}`;
        $("loc-coords").textContent = `LAT: ${lat}  LONG: ${lon}`;
      } else {
        $("loc-coords").textContent = "";
      }
    }
  }

  function handle(msg) {
    switch (msg.type) {
      case "state": setState(msg.state); break;
      case "feed": addFeed(msg.text, msg.kind); break;
      case "stats":
        if ("cpu" in msg) setBar("cpu", msg.cpu);
        if ("ram" in msg) setBar("ram", msg.ram);
        if ("mic" in msg) setBar("mic", msg.mic);
        break;
      case "level": if (body.dataset.state === "speaking") setLevel(msg.value); break;
      case "config": setConfig(msg); break;
      case "reload": location.reload(); break;
      case "feed_reset": $("feed").textContent = ""; (msg.items || []).forEach((i) => addFeed(i.text, i.kind)); break;
    }
  }

  // ---------- connection ----------
  let retry = 1000;
  function connect() {
    let ws;
    try { ws = new WebSocket(`ws://127.0.0.1:${PORT}/ws`); } catch { return schedule(); }
    ws.onopen = () => { retry = 1000; ws.send(JSON.stringify({ type: "hello", token: TOKEN })); };
    ws.onmessage = (ev) => { try { handle(JSON.parse(ev.data)); } catch { /* ignore bad frames */ } };
    ws.onclose = () => { setState("offline"); schedule(); };
    ws.onerror = () => { try { ws.close(); } catch { /* already closed */ } };
  }
  function schedule() {
    setTimeout(connect, retry);
    retry = Math.min(retry * 2, 15000);
  }

  // ---------- demo mode for previewing in a browser ----------
  function demo() {
    setConfig({ name: "JARVIS", location: { name: "Malibu, CA", lat: 34.0259, lon: -118.7798 } });
    setState("idle");
    addFeed("Initializing system protocols...");
    addFeed("[10:48:30 PM] All systems nominal");
    const states = ["idle", "listening", "thinking", "speaking", "idle", "approval", "idle"];
    let i = 0;
    setInterval(() => {
      const s = states[i++ % states.length];
      setState(s);
      addFeed(`State -> ${s}`, s === "approval" ? "warn" : "");
    }, 3500);
    setInterval(() => {
      setBar("cpu", 30 + Math.random() * 40);
      setBar("ram", 55 + Math.random() * 15);
      setBar("mic", Math.random() * 30);
      if (body.dataset.state === "speaking") setLevel(Math.random());
    }, 150);
  }

  setState("offline");
  if (DEMO) demo(); else connect();
})();

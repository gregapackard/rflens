const state = {
  seenEvents: new Set(),
  initialized: false,
  activeTab: "dashboard",
  station: null,
  healthOnline: false,
  adsbUi: { enabled: false, url: "" },
  adsbUiTimer: null,
};

const DATA_FETCH_LIMIT = 1000;
const EVENT_FEED_LIMIT = 50;
const LIVE_SECONDS = 60;
const RECENT_SECONDS = 10 * 60;

const $ = (id) => document.getElementById(id);

function setHtml(id, html) {
  const element = $(id);
  if (element) element.innerHTML = html;
}

function setText(id, text) {
  const element = $(id);
  if (element) element.textContent = text;
}

function setMany(ids, text) {
  ids.forEach((id) => setText(id, text));
}

function setManyHtml(ids, html) {
  ids.forEach((id) => setHtml(id, html));
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[char]);
}

async function getJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error(`${url} ${response.status}`);
  return response.json();
}

function timeMs(value) {
  const time = new Date(value).getTime();
  return Number.isFinite(time) ? time : 0;
}

function secondsAgo(value) {
  const time = timeMs(value);
  if (!time) return Number.POSITIVE_INFINITY;
  return Math.max(0, (Date.now() - time) / 1000);
}

function relTime(value) {
  const seconds = secondsAgo(value);
  if (!Number.isFinite(seconds)) return "No data yet";
  if (seconds < 60) return `${Math.floor(seconds)} sec ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} hr ago`;
  const days = Math.floor(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

function fmtTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function fmtDateTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function parseMetadata(row) {
  if (!row?.metadata_json) return {};
  try {
    return JSON.parse(row.metadata_json);
  } catch {
    return {};
  }
}

function validCoord(lat, lon) {
  const y = Number(lat);
  const x = Number(lon);
  return Number.isFinite(y)
    && Number.isFinite(x)
    && Math.abs(y) <= 90
    && Math.abs(x) <= 180
    && !(y === 0 && x === 0);
}

function statusClass(status) {
  const text = String(status || "").toLowerCase();
  if (text.includes("online") || text.includes("enabled")) return "ok";
  if (text.includes("missing") || text.includes("malformed") || text.includes("warn")) return "warn";
  if (text.includes("error") || text.includes("offline")) return "bad";
  return "idle";
}

function sourceStatus(source) {
  if (!source) return "unknown";
  if (String(source.status || "").toLowerCase().includes("online")) return "online";
  if (source.last_seen && secondsAgo(source.last_seen) <= LIVE_SECONDS) return "online";
  return source.status || "unknown";
}

function expectedLive(source) {
  return ["aprs", "adsb", "satellite"].includes(source?.type);
}

function todayKey() {
  return new Date().toISOString().slice(0, 10);
}

function formatNumber(value, fallback = "No data yet") {
  return Number.isFinite(Number(value)) ? String(value) : fallback;
}

function formatPercent(value) {
  const number = Number(value);
  return Number.isFinite(number) ? `${number.toFixed(0)}%` : "No data yet";
}

function aircraftKey(event) {
  const metadata = parseMetadata(event);
  return String(metadata.hex || metadata.icao || event.callsign || event.id || "").trim();
}

function aircraftLabel(event) {
  const metadata = parseMetadata(event);
  return event.callsign || metadata.flight || metadata.hex || metadata.icao || event.id || "Unknown";
}

function uniqueAircraftEvents(adsb) {
  const byKey = new Map();
  adsb.forEach((event) => {
    const key = aircraftKey(event);
    if (!key) return;
    const current = byKey.get(key);
    if (!current || timeMs(event.timestamp) > timeMs(current.timestamp)) byKey.set(key, event);
  });
  return [...byKey.values()].sort((a, b) => timeMs(b.timestamp) - timeMs(a.timestamp));
}

function adsbRange(event) {
  const metadata = parseMetadata(event);
  const range = Number(metadata.r_dst);
  return Number.isFinite(range) ? range : null;
}

function adsbRssi(event) {
  const metadata = parseMetadata(event);
  const rssi = Number(metadata.rssi);
  return Number.isFinite(rssi) ? rssi : null;
}

function uniqueBy(values, keyFn) {
  const seen = new Set();
  return values.filter((value) => {
    const key = String(keyFn(value) || "").trim();
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function sourceLabel(event) {
  const type = String(event.event_type || "").replace(/_/g, " ");
  if (event.callsign) return `${event.callsign} ${type}`.trim();
  return type || "RF event";
}

function eventLine(event) {
  const metadata = parseMetadata(event);
  if (event.event_type === "adsb_aircraft") {
    const label = event.callsign || metadata.flight || metadata.hex || "aircraft";
    const parts = [`ADS-B heard ${label}`];
    if (event.altitude != null) parts.push(`at ${Number(event.altitude).toLocaleString()} ft`);
    if (Number.isFinite(Number(metadata.r_dst))) parts.push(`${Number(metadata.r_dst).toFixed(0)} nmi`);
    return parts.join(", ");
  }
  if (event.event_type === "aprs_packet") {
    return `APRS heard ${event.callsign || "station"}`;
  }
  if (event.event_type === "satellite_capture") {
    const name = event.callsign || metadata.satellite || metadata.name || "satellite";
    const status = metadata.status || "completed";
    return `Satellite capture ${status}: ${name}`;
  }
  if (String(event.event_type || "").includes("source")) {
    return `Source ${event.callsign || event.event_type}`;
  }
  return sourceLabel(event);
}

function eventDetail(event) {
  const parts = [];
  if (event.altitude != null) parts.push(`${Number(event.altitude).toLocaleString()} ft`);
  if (event.speed != null) parts.push(`${event.speed} kt`);
  if (validCoord(event.lat, event.lon)) parts.push(`${Number(event.lat).toFixed(4)}, ${Number(event.lon).toFixed(4)}`);
  return parts.join(" - ") || eventLine(event);
}

function countBy(values, keyFn) {
  return values.reduce((counts, value) => {
    const key = keyFn(value) || "unknown";
    counts[key] = (counts[key] || 0) + 1;
    return counts;
  }, {});
}

function summarizeCounts(counts, limit = 3) {
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, limit);
  return entries.length ? entries.map(([key, count]) => `${key}: ${count}`).join(", ") : "No data yet";
}

function sourceTypeForEvent(event, sources) {
  const byId = new Map(sources.map((source) => [source.id, source.type]));
  if (byId.has(event.source_id)) return byId.get(event.source_id);
  if (event.event_type === "adsb_aircraft") return "adsb";
  if (event.event_type === "aprs_packet") return "aprs";
  if (event.event_type === "satellite_capture") return "satellite";
  return "unknown";
}

function renderHealthOnline(online) {
  state.healthOnline = online;
  $("health").textContent = online ? "online" : "offline";
  $("health").className = `pulse ${online ? "online" : "offline"}`;
  setText("dash-global-health", online ? "Online" : "Offline");
}

async function pollHealth() {
  try {
    const health = await getJson("/api/health");
    if (health?.station && !state.station) state.station = health.station;
    renderHealthOnline(true);
  } catch (error) {
    renderHealthOnline(false);
    console.error(error);
  }
}

function renderSources(sources) {
  const wanted = ["aprs", "adsb", "satellite"];
  const byType = Object.fromEntries(sources.map((source) => [source.type, source]));
  $("sources").innerHTML = wanted.map((type) => {
    const source = byType[type] || { name: type.toUpperCase(), status: "unknown" };
    const status = sourceStatus(source);
    return `
      <article class="source-card ${statusClass(status)}">
        <div class="source-type">${esc(type)}</div>
        <h2>${esc(source.name)}</h2>
        <dl>
          <div><dt>Status</dt><dd>${esc(status)}</dd></div>
          <div><dt>Frequency</dt><dd>${esc(source.frequency || "-")}</dd></div>
          <div><dt>Last Seen</dt><dd>${esc(source.last_seen ? relTime(source.last_seen) : "No data yet")}</dd></div>
        </dl>
      </article>
    `;
  }).join("");
}

function renderFeed(events, targetId, limit = 20) {
  const html = events.slice(0, limit).map((event) => {
    const isNew = state.initialized && !state.seenEvents.has(event.id);
    const isRecent = secondsAgo(event.timestamp) <= 30;
    return `
      <div class="feed-row ${isNew || isRecent ? "new" : ""}">
        <time>${esc(relTime(event.timestamp))}</time>
        <strong>${esc(eventLine(event))}</strong>
        <span>${esc(event.event_type || "-")}</span>
        <p>${esc(eventDetail(event))}</p>
      </div>
    `;
  }).join("");
  setHtml(targetId, html || `<div class="feed-row"><strong>No data yet</strong></div>`);
}

function eventSquawk(event, metadata) {
  return String(metadata.squawk || event.squawk || "").trim();
}

function flagIsSet(value) {
  if (value === true || value === 1) return true;
  if (typeof value === "string") {
    const text = value.trim().toLowerCase();
    return Boolean(text && !["0", "false", "none", "null", "undefined", "no"].includes(text));
  }
  return value != null && value !== false && value !== 0;
}

function alertFlagIsSet(value) {
  const text = String(value).trim().toLowerCase();
  return value === true || value === 1 || text === "true" || text === "1";
}

function eventRange(event, metadata) {
  const range = Number(metadata.r_dst ?? metadata.range ?? event.r_dst ?? event.range);
  return Number.isFinite(range) ? range : null;
}

function eventAltitude(event, metadata) {
  const altitude = Number(event.altitude ?? metadata.altitude ?? metadata.alt_baro);
  return Number.isFinite(altitude) ? altitude : null;
}

function hasAdsbMetadata(metadata) {
  return ["hex", "flight", "alt_baro", "r_dst", "rssi"].some((key) => Object.prototype.hasOwnProperty.call(metadata, key));
}

function isAdsbLikeEvent(event, sources) {
  const metadata = parseMetadata(event);
  const source = sources.find((item) => String(item.id) === String(event.source_id));
  const sourceType = String(event.source_type || metadata.source_type || source?.type || sourceTypeForEvent(event, sources) || "").toLowerCase();
  const eventType = String(event.event_type || "").toLowerCase();
  return Boolean(
    sourceType === "adsb"
    || eventType.startsWith("adsb")
    || hasAdsbMetadata(metadata)
  );
}

function isExceptionalAdsbEvent(event) {
  const metadata = parseMetadata(event);
  const squawk = eventSquawk(event, metadata);
  return Boolean(
    ["7500", "7600", "7700"].includes(squawk)
    || flagIsSet(metadata.emergency ?? event.emergency)
    || alertFlagIsSet(metadata.alert ?? event.alert)
    || alertFlagIsSet(metadata.spi ?? event.spi)
  );
}

function filterOverviewFeedEvents(events, sources = []) {
  const filteredEvents = events.filter((event) => {
    if (isAdsbLikeEvent(event, sources)) {
      const allowed = isExceptionalAdsbEvent(event);
      if (!allowed) console.log("Filtered out ADS-B:", event.event_type, event.callsign);
      return allowed;
    }
    return true;
  });
  console.log("Overview feed events:", filteredEvents.map((event) => [event.event_type, event.callsign]));
  return filteredEvents;
}

function renderOverviewFeed(events, sources, targetId, limit = 20) {
  const filtered = filterOverviewFeedEvents(events, sources);
  const html = filtered.slice(0, limit).map((event) => {
    const isNew = state.initialized && !state.seenEvents.has(event.id);
    const isRecent = secondsAgo(event.timestamp) <= 30;
    return `
      <div class="feed-row ${isNew || isRecent ? "new" : ""}">
        <time>${esc(relTime(event.timestamp))}</time>
        <strong>${esc(eventLine(event))}</strong>
        <span>${esc(event.event_type || "-")}</span>
        <p>${esc(eventDetail(event))}</p>
      </div>
    `;
  }).join("");
  setHtml(targetId, html || `
    <div class="feed-row">
      <time>normal</time>
      <strong>All systems normal</strong>
      <span>quiet</span>
      <p>No significant RF events detected</p>
    </div>
  `);
  return filtered.length;
}

function renderEvents(events, sources) {
  const newest = events.slice().sort((a, b) => timeMs(b.timestamp) - timeMs(a.timestamp));
  const recent = newest.filter((event) => secondsAgo(event.timestamp) <= RECENT_SECONDS);
  setText("event-count", newest.length);
  setText("events-tab-count", newest.length);
  setText("overview-events-count", renderOverviewFeed(newest, sources, "overview-event-feed", 20));
  setText("dash-events-recent", newest.length ? `${recent.length} events` : "No data yet");
  setText("dash-events-types", summarizeCounts(countBy(newest, (event) => event.event_type)));
  setText("dash-events-sources", summarizeCounts(countBy(newest, (event) => sourceTypeForEvent(event, sources))));
  setHtml("dash-events-lines", newest.slice(0, 10).map((event) => `
    <div><time>${esc(relTime(event.timestamp))}</time><span>${esc(eventLine(event))}</span></div>
  `).join("") || "<p>No data yet</p>");
  renderFeed(newest, "event-feed", 20);
  renderFeed(newest, "events-tab-feed", 20);
  newest.forEach((event) => state.seenEvents.add(event.id));
}

function renderAprsTable(events) {
  const html = events.map((event) => `
    <tr class="${secondsAgo(event.timestamp) <= 30 ? "recent-row" : ""}">
      <td>${esc(fmtTime(event.timestamp))}</td>
      <td>${esc(event.callsign || "-")}</td>
      <td class="mono">${esc(event.raw_text || "")}</td>
    </tr>
  `).join("");
  setHtml("aprs-table", html);
  setHtml("events-aprs-table", html);
}

function renderAdsbTable(events) {
  const html = events.map((event) => `
    <tr class="${secondsAgo(event.timestamp) <= 30 ? "recent-row" : ""}">
      <td>${esc(fmtTime(event.timestamp))}</td>
      <td>${esc(event.callsign || aircraftLabel(event))}</td>
      <td>${esc(validCoord(event.lat, event.lon) ? `${Number(event.lat).toFixed(4)}, ${Number(event.lon).toFixed(4)}` : "-")}</td>
      <td>${esc(event.altitude ?? "-")}</td>
      <td>${esc(event.speed ?? "-")}</td>
    </tr>
  `).join("");
  setHtml("adsb-table", html);
  setHtml("events-adsb-table", html);
}

function shortPath(path) {
  if (!path) return "-";
  const parts = String(path).split(/[\\/]/);
  return parts.slice(-2).join("/");
}

function renderCaptures(captures) {
  const html = captures.map((capture) => `
    <tr>
      <td>${esc(capture.satellite || "-")}</td>
      <td><span class="pill">${esc(capture.status || "-")}</span></td>
      <td class="mono">${esc(shortPath(capture.image_path))}</td>
      <td>${esc(fmtTime(capture.start_time))}</td>
    </tr>
  `).join("");
  setHtml("captures-table", html);
  setHtml("captures-tab-table", html);
}

function renderGlobalCard({ sources }) {
  const liveSources = sources.filter((source) => source.last_seen && secondsAgo(source.last_seen) <= LIVE_SECONDS).length;
  const station = state.station || {};
  setText("dash-global-health", state.healthOnline ? "Online" : "Offline");
  setText("dash-global-station", `${station.name || "RF Node"} ${station.grid || ""}`.trim());
  setText("dash-global-sources", sources.length ? `${liveSources} / ${sources.length}` : "No data yet");
  setText("dash-global-refresh", relTime(new Date().toISOString()));
}

function renderAdsbCard(adsb) {
  const aircraft = uniqueAircraftEvents(adsb);
  const positioned = aircraft.filter((event) => validCoord(event.lat, event.lon));
  const unpositioned = aircraft.length - positioned.length;
  const ranges = aircraft.map((event) => ({ event, range: adsbRange(event) })).filter((item) => Number.isFinite(item.range));
  const maxRange = ranges.length ? Math.max(...ranges.map((item) => item.range)) : null;
  const closest = ranges.length ? ranges.slice().sort((a, b) => a.range - b.range)[0] : null;
  const highest = aircraft
    .map((event) => Number(event.altitude))
    .filter(Number.isFinite)
    .sort((a, b) => b - a)[0];
  const bestRssi = aircraft
    .map(adsbRssi)
    .filter(Number.isFinite)
    .sort((a, b) => b - a)[0];
  const lastUpdate = aircraft[0]?.timestamp;

  setMany(["dash-adsb-total", "overview-adsb-total"], aircraft.length ? aircraft.length : "No data yet");
  setMany(["dash-adsb-positioned", "overview-adsb-positioned"], aircraft.length ? positioned.length : "No data yet");
  setMany(["dash-adsb-unpositioned", "overview-adsb-unpositioned"], aircraft.length ? unpositioned : "No data yet");
  setMany(["dash-adsb-range", "overview-adsb-range"], maxRange == null ? "No data yet" : `${maxRange.toFixed(1)} nmi`);
  setMany(["dash-adsb-highest", "overview-adsb-highest"], Number.isFinite(highest) ? `${highest.toLocaleString()} ft` : "No data yet");
  setMany(["dash-adsb-closest", "overview-adsb-closest"], closest ? `${aircraftLabel(closest.event)}, ${closest.range.toFixed(1)} nmi` : "No data yet");
  setMany(["dash-adsb-rssi", "overview-adsb-rssi"], Number.isFinite(bestRssi) ? `${bestRssi.toFixed(1)} dB` : "No data yet");
  setMany(["dash-adsb-updated", "overview-adsb-updated"], lastUpdate ? relTime(lastUpdate) : "No data yet");
}

function renderAprsCard(aprs) {
  const stations = uniqueBy(aprs, (event) => event.callsign);
  const positioned = uniqueBy(aprs.filter((event) => validCoord(event.lat, event.lon)), (event) => event.callsign || `${event.lat},${event.lon}`);
  const recent = aprs.filter((event) => secondsAgo(event.timestamp) <= RECENT_SECONDS);
  const newest = aprs.slice().sort((a, b) => timeMs(b.timestamp) - timeMs(a.timestamp))[0];
  const stationCount = stations.length;

  setMany(["dash-aprs-stations", "overview-aprs-stations"], aprs.length ? stationCount : "No data yet");
  setMany(["dash-aprs-packets", "overview-aprs-packets"], aprs.length ? aprs.length : "No data yet");
  setMany(["dash-aprs-positioned", "overview-aprs-positioned"], aprs.length ? positioned.length : "No data yet");
  setMany(["dash-aprs-updated", "overview-aprs-updated"], newest ? relTime(newest.timestamp) : "No data yet");
  setMany(["dash-aprs-newest", "overview-aprs-newest"], newest?.callsign || "No data yet");
  setMany(["dash-aprs-recent", "overview-aprs-recent"], aprs.length ? `${recent.length} packets` : "No data yet");
}

function captureTime(capture) {
  return capture?.start_time || capture?.timestamp;
}

function renderSatelliteCard(captures, events) {
  const satelliteEvents = events.filter((event) => event.event_type === "satellite_capture");
  const today = todayKey();
  const capturesToday = captures.filter((capture) => String(captureTime(capture) || "").slice(0, 10) === today);
  const latestCapture = captures.slice().sort((a, b) => timeMs(captureTime(b)) - timeMs(captureTime(a)))[0];
  const latestEvent = satelliteEvents[0];
  const latestMetadata = parseMetadata(latestEvent);
  const satellite = latestCapture?.satellite || latestEvent?.callsign || latestMetadata.satellite || latestMetadata.name || "Unknown";
  const status = latestCapture?.status || latestMetadata.status || (latestEvent ? "completed" : "unknown");
  const imagePath = latestCapture?.image_path || latestMetadata.image_path;
  const imageHtml = imagePath
    ? `<a href="${esc(imagePath)}" target="_blank" rel="noreferrer">${esc(shortPath(imagePath))}</a>`
    : "No data yet";

  setMany(["dash-sat-captures", "overview-sat-captures"], captures.length ? captures.length : "No data yet");
  setMany(["dash-sat-today", "overview-sat-today"], captures.length ? capturesToday.length : "No data yet");
  setMany(["dash-sat-name", "overview-sat-name"], latestCapture || latestEvent ? satellite : "No data yet");
  setMany(["dash-sat-updated", "overview-sat-capture"], latestCapture || latestEvent ? relTime(captureTime(latestCapture) || latestEvent.timestamp) : "No data yet");
  setMany(["dash-sat-status", "overview-sat-status"], latestCapture || latestEvent ? status : "No data yet");
  setManyHtml(["dash-sat-image", "overview-sat-image"], imageHtml);
}

function renderSystemCard(system, sources) {
  const byType = Object.fromEntries(sources.map((source) => [source.type, source]));
  const memory = system?.memory?.percent;
  const disk = system?.disk?.percent;
  const warnings = [];
  if (Number(disk) > 85) warnings.push("disk");
  sources.forEach((source) => {
    if (expectedLive(source) && (!source.last_seen || secondsAgo(source.last_seen) > LIVE_SECONDS)) warnings.push(source.type);
  });
  const sourceText = (type) => {
    const source = byType[type];
    if (!source) return "No data yet";
    const status = sourceStatus(source);
    return source.last_seen ? `${status}, ${relTime(source.last_seen)}` : status;
  };

  setMany(["dash-system-cpu", "overview-system-cpu"], formatPercent(system?.cpu_percent));
  setMany(["dash-system-memory", "overview-system-memory"], formatPercent(memory));
  setMany(["dash-system-disk", "overview-system-disk"], formatPercent(disk));
  setMany(["dash-system-aprs", "overview-system-aprs"], sourceText("aprs"));
  setMany(["dash-system-adsb", "overview-system-adsb"], sourceText("adsb"));
  setMany(["dash-system-satellite", "overview-system-satellite"], sourceText("satellite"));
  setMany(["dash-system-warnings", "overview-system-warnings"], warnings.length);
}

function renderOverview({ sources, adsb, aprs, captures, events, system }) {
  renderGlobalCard({ sources });
  renderAdsbCard(adsb);
  renderAprsCard(aprs);
  renderSatelliteCard(captures, events);
  renderSystemCard(system, sources);
  const newest = events.slice().sort((a, b) => timeMs(b.timestamp) - timeMs(a.timestamp));
  setText("overview-events-count", renderOverviewFeed(newest, sources, "overview-event-feed", 20));
  setText("overview-updated", `updated ${fmtTime(new Date().toISOString())}`);
}

function showAdsbFallback(configured) {
  const wrap = $("adsb-ui-wrap");
  if (!wrap || !configured) return;
  wrap.classList.add("iframe-failed");
}

function renderAdsbUi(config) {
  const frame = $("adsb-ui-frame");
  const wrap = $("adsb-ui-wrap");
  const status = $("adsb-ui-status");
  const open = $("adsb-ui-open");
  if (!frame || !wrap) return;
  const enabled = Boolean(config?.enabled);
  const url = String(config?.url || "").trim();
  const configured = enabled && url.length > 0;
  wrap.classList.toggle("configured", configured);
  if (status) status.textContent = configured ? "tar1090 embedded" : "setup needed";
  if (open) open.href = configured ? url : "#";
  if (configured && frame.getAttribute("src") === url && wrap.classList.contains("iframe-loaded")) return;
  wrap.classList.remove("iframe-failed", "iframe-loaded");
  if (state.adsbUiTimer) clearTimeout(state.adsbUiTimer);

  frame.onload = null;
  frame.onerror = null;
  if (!configured) {
    frame.removeAttribute("src");
    return;
  }

  frame.onload = () => {
    wrap.classList.add("iframe-loaded");
    wrap.classList.remove("iframe-failed");
    if (state.adsbUiTimer) clearTimeout(state.adsbUiTimer);
  };
  frame.onerror = () => showAdsbFallback(configured);
  if (frame.getAttribute("src") !== url) frame.src = url;
  state.adsbUiTimer = setTimeout(() => {
    if (!wrap.classList.contains("iframe-loaded")) showAdsbFallback(configured);
  }, 5000);
}

function switchTab(tabName) {
  state.activeTab = tabName;
  document.querySelectorAll(".tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === tabName);
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.id === `tab-${tabName}`);
  });
  if (tabName === "adsb") renderAdsbUi(state.adsbUi);
}

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => switchTab(button.dataset.tab));
});

document.addEventListener("click", (event) => {
  const tabButton = event.target.closest("[data-open-tab]");
  if (tabButton) switchTab(tabButton.dataset.openTab);
});

async function refreshData() {
  try {
    const [station, adsbUi, sources, adsb, aprs, captures, events, system] = await Promise.all([
      getJson("/api/station"),
      getJson("/api/adsb/ui"),
      getJson("/api/sources"),
      getJson(`/api/adsb/recent?limit=${DATA_FETCH_LIMIT}`),
      getJson(`/api/aprs/recent?limit=${DATA_FETCH_LIMIT}`),
      getJson("/api/captures"),
      getJson(`/api/events/recent?limit=${EVENT_FEED_LIMIT}`),
      getJson("/api/system"),
    ]);
    state.station = station || {};
    state.adsbUi = adsbUi || { enabled: false, url: "" };
    $("station").textContent = `${state.station?.name || "RF Node"} ${state.station?.grid || ""}`.trim();
    renderSources(sources);
    renderAdsbUi(state.adsbUi);
    renderEvents(events, sources);
    renderAprsTable(aprs);
    renderAdsbTable(adsb);
    renderCaptures(captures);
    renderOverview({ sources, adsb, aprs, captures, events, system });
    state.initialized = true;
  } catch (error) {
    console.error(error);
  }
}

pollHealth();
refreshData();
setInterval(pollHealth, 5000);
setInterval(refreshData, 5000);

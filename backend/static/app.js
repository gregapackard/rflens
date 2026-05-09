const state = {
  seenEvents: new Set(),
  initialized: false,
  activeTab: "dashboard",
  station: null,
  healthOnline: false,
  adsbUi: { enabled: false, url: "" },
  adsbUiTimer: null,
  eventFilters: { adsb: false, aprs: true, satellite: true, system: true },
  latestEvents: [],
  latestSources: [],
  latestAdsb: [],
  latestAprs: [],
  aprsStatus: {},
  insights: {},
  selectedAprsCallsign: "",
  selectedAprsEvents: [],
  selectedAprsEventsByCallsign: {},
  aprsStationSort: "packet_count_desc",
  aprsStationsExpanded: false,
  profileSummary: "",
  allTimeRecords: [],
  records: {
    initialized: false,
    adsbMaxRange: null,
    adsbMaxAltitude: null,
    aprsCallsigns: new Set(),
    latestCaptureKey: null,
    sourceStates: {},
    diskHigh: false,
    alerts: [],
  },
};

const DATA_FETCH_LIMIT = 250;
const EVENT_FEED_LIMIT = 30;
const APRS_STATION_DEFAULT_LIMIT = 24;
const HEALTH_REFRESH_MS = 5000;
const DASHBOARD_REFRESH_MS = 30000;
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

const RECORD_DEFINITIONS = [
  { record_type: "adsb_max_range", label: "ADS-B max range" },
  { record_type: "adsb_highest_altitude", label: "ADS-B highest altitude" },
  { record_type: "adsb_strongest_signal", label: "ADS-B strongest signal" },
  { record_type: "satellite_total_captures", label: "Satellite total captures" },
  { record_type: "satellite_latest_capture", label: "Satellite latest capture" },
];

function recordByType(records) {
  return Object.fromEntries((records || []).map((record) => [record.record_type, record]));
}

function readableValue(value) {
  if (value == null || value === "") return "-";
  if (Array.isArray(value)) {
    if (!value.length) return "No values";
    return value.map(readableValue).join(", ");
  }
  if (typeof value === "object") {
    const entries = Object.entries(value);
    if (!entries.length) return "No values";
    return entries.map(([key, item]) => `${key}: ${readableValue(item)}`).join("; ");
  }
  return String(value);
}

function metadataRows(record) {
  const metadata = parseMetadata(record);
  return Object.entries(metadata)
    .filter(([key]) => key !== "raw_json")
    .map(([key, value]) => ({ key, value: readableValue(value) }))
    .filter((row) => row.value && row.value !== "-");
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

function formatInteger(value, fallback = "No data yet") {
  const number = Number(value);
  return Number.isFinite(number) ? number.toLocaleString() : fallback;
}

function formatCoord(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(4) : null;
}

function yesNo(value, fallback = "No data yet") {
  if (value === true) return "Yes";
  if (value === false) return "No";
  return fallback;
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

function titleCase(value) {
  return String(value || "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function stationTypeLabel(value) {
  const text = String(value || "").toLowerCase();
  const labels = {
    igate: "iGate",
    digipeater: "Digi",
    likely_igate: "Likely iGate",
    possible_digipeater: "Possible Digi",
    mobile: "Mobile",
    handheld: "Handheld",
    weather: "Weather",
    repeater_object: "Repeater object",
    aircraft: "Aircraft",
    packet_node: "Packet node",
    ax25_node: "AX.25 node",
  };
  return labels[text] || titleCase(value);
}

function aprsMetadata(event) {
  return parseMetadata(event);
}

function aprsTransportLabel(metadata) {
  const transport = String(metadata.heard_transport || "").toUpperCase();
  const prefix = metadata.rf_channel_prefix ? ` ${metadata.rf_channel_prefix}` : "";
  if (transport === "RF") return `RF${prefix}`;
  if (transport === "IP") return "IP";
  return transport || "APRS";
}

function aprsDistanceLabel(metadata) {
  const miles = Number(metadata.distance_miles);
  return Number.isFinite(miles) ? `${miles.toFixed(0)} mi` : "";
}

function cleanPathToken(value) {
  return String(value || "").trim().toUpperCase().replace(/\*$/, "");
}

function isWideAlias(value) {
  return /^WIDE\d*(?:-\d+)?\*?$/i.test(String(value || "").trim());
}

function metadataPath(metadata) {
  if (Array.isArray(metadata.path)) return metadata.path.map(cleanPathToken).filter(Boolean);
  const parts = String(metadata.path_raw || "").split(",").map(cleanPathToken).filter(Boolean);
  return parts.length ? parts.slice(1) : [];
}

function preferredHeardVia(metadata) {
  const explicit = cleanPathToken(metadata.preferred_heard_via || metadata.heard_via || metadata.last_used_digipeater);
  if (!explicit || explicit === "DIRECT") {
    return metadata.was_direct === true || String(metadata.heard_via || "").toLowerCase() === "direct" ? "direct" : "";
  }
  if (!isWideAlias(explicit)) return explicit;
  const path = metadataPath(metadata);
  for (let index = path.length - 1; index >= 0; index -= 1) {
    const token = path[index];
    if (token && token !== explicit && !isWideAlias(token)) return token;
  }
  return explicit;
}

function aprsViaLabel(metadata) {
  const via = preferredHeardVia(metadata);
  if (via && via !== "direct") return `via ${via}`;
  if (via === "direct") return "direct";
  return "";
}

function aprsAudioLabel(metadata) {
  const level = Number(metadata.audio_level);
  if (!Number.isFinite(level)) return "";
  return metadata.audio_quality ? `${level} (${metadata.audio_quality})` : String(level);
}

function aprsMotionLabels(metadata) {
  const labels = [];
  const speed = Number(metadata.speed_mph);
  const course = Number(metadata.course_degrees);
  const altitude = Number(metadata.altitude_ft);
  if (Number.isFinite(speed)) labels.push(`${speed.toFixed(0)} MPH`);
  if (Number.isFinite(course)) labels.push(`course ${course.toFixed(0)}°`);
  if (Number.isFinite(altitude)) labels.push(`${altitude.toLocaleString()} ft`);
  return labels;
}

function aprsGateLabel(metadata) {
  if (metadata.confirmed_gated_by_me === true) return `confirmed gated by ${metadata.gated_by || "local station"}`;
  if (metadata.gated_by_other === true && metadata.gated_by) return `gated by ${metadata.gated_by}`;
  if (metadata.gate_eligible === true) return "gate eligible, unconfirmed";
  if (metadata.heard_over_rf === true) return "RF only";
  return "";
}

function aprsCategoryLabel(metadata) {
  const category = String(metadata.heard_category || "").toLowerCase();
  if (category === "direct_rf" || (metadata.direct_rf_heard === true)) return "Direct RF";
  if (category === "digipeated_rf" || (metadata.digipeated_rf_heard === true)) return "Digipeated RF";
  if (category === "aprs_is" || (metadata.network_seen === true)) return "APRS-IS/network-side";
  return "";
}

function aprsDistanceQualityLabel(metadata) {
  const quality = String(metadata.distance_quality || "").toLowerCase();
  if (quality === "questionable") return "Questionable distance";
  if (quality === "long_range") return "Long range";
  return "";
}

function aprsPacketLabels(event) {
  const metadata = aprsMetadata(event);
  return [
    aprsCategoryLabel(metadata),
    aprsTransportLabel(metadata),
    aprsDistanceLabel(metadata),
    aprsViaLabel(metadata),
    metadata.station_type ? stationTypeLabel(metadata.station_type) : "",
    ...aprsMotionLabels(metadata),
    aprsAudioLabel(metadata) ? `audio ${aprsAudioLabel(metadata)}` : "",
    aprsGateLabel(metadata),
    aprsDistanceQualityLabel(metadata),
    validCoord(event.lat ?? metadata.lat, event.lon ?? metadata.lon) ? "position" : "",
  ].filter(Boolean);
}

function aprsCallsign(event) {
  const metadata = aprsMetadata(event);
  return normalizeAprsCallsign(event?.callsign || metadata.source_callsign || metadata.callsign || "");
}

function normalizeAprsCallsign(value) {
  const text = String(value || "").trim().toUpperCase().replace(/\*$/, "");
  const match = text.match(/[A-Z0-9]{1,6}(?:-\d{1,2})?/);
  return match ? match[0] : "";
}

function aprsCallsignButton(callsign, extraClass = "") {
  const value = normalizeAprsCallsign(callsign);
  if (!value) return "-";
  return `<button class="callsign-button ${extraClass}" type="button" data-aprs-callsign="${esc(value)}">${esc(value)}</button>`;
}

function eventLineHtml(event) {
  const line = eventLine(event);
  if (event.event_type !== "aprs_packet") return esc(line);
  const callsign = aprsCallsign(event);
  if (!callsign) return esc(line);
  return esc(line).replace(esc(callsign), aprsCallsignButton(callsign, "inline-callsign"));
}

function categoryKey(metadata) {
  const category = String(metadata.heard_category || "").toLowerCase();
  if (category === "direct_rf" || metadata.direct_rf_heard === true) return "direct_rf";
  if (category === "digipeated_rf" || metadata.digipeated_rf_heard === true) return "digipeated_rf";
  if (category === "aprs_is" || metadata.network_seen === true) return "aprs_is";
  return "unknown";
}

function stationDetailDistance(metadata) {
  const distance = aprsDistanceLabel(metadata);
  if (!distance) return "";
  const quality = aprsDistanceQualityLabel(metadata);
  return quality ? `${distance} (${quality.toLowerCase()})` : distance;
}

function packetSnippet(event, metadata) {
  const text = metadata.comment || metadata.payload || metadata.payload_text || event.raw_text || "";
  const trimmed = String(text).replace(/\s+/g, " ").trim();
  return trimmed.length > 140 ? `${trimmed.slice(0, 137)}...` : trimmed;
}

function decodedNote(metadata) {
  const decodedText = String(metadata.direwolf_decoded_text || "");
  if (/mic-e/i.test(decodedText)) return "MIC-E decoded by Direwolf";
  if (hasValue(metadata.decoded_lat) && hasValue(metadata.decoded_lon)) return "Position decoded by Direwolf";
  return "";
}

function gateStatusLabel(metadata) {
  if (metadata.confirmed_gated_by_me === true) return "Local gate confirmed by APRS-IS proof";
  if (metadata.gated_by_other === true && metadata.gated_by) return `Gated by ${metadata.gated_by}`;
  if (metadata.heard_over_rf === true && metadata.gate_eligible === true) return "Local gate unconfirmed";
  if (metadata.gate_eligible === true) return "Gate eligible, unconfirmed";
  return "No gate evidence";
}

function countStationValues(events, valueFn) {
  return events.reduce((counts, event) => {
    const value = valueFn(event);
    if (value) counts[value] = (counts[value] || 0) + 1;
    return counts;
  }, {});
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

function adsbAltitude(event) {
  const metadata = parseMetadata(event);
  const altitude = Number(metadata.alt_baro ?? event.altitude);
  return Number.isFinite(altitude) ? altitude : null;
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
    const metadata = parseMetadata(event);
    const category = aprsCategoryLabel(metadata);
    const prefix = category ? `${category}:` : "APRS heard";
    const parts = [`${prefix} ${event.callsign || metadata.source_callsign || "station"}`];
    const distance = aprsDistanceLabel(metadata);
    const via = aprsViaLabel(metadata);
    if (distance) parts.push(distance);
    if (via) parts.push(via);
    return parts.join(", ");
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
  const metadata = parseMetadata(event);
  if (event.event_type === "aprs_packet") {
    aprsPacketLabels(event).forEach((label) => parts.push(label));
  }
  if (event.altitude != null) parts.push(`${Number(event.altitude).toLocaleString()} ft`);
  if (event.speed != null) parts.push(`${event.speed} kt`);
  if (validCoord(event.lat ?? metadata.lat, event.lon ?? metadata.lon)) {
    parts.push(`${Number(event.lat ?? metadata.lat).toFixed(4)}, ${Number(event.lon ?? metadata.lon).toFixed(4)}`);
  }
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

function topEntries(counts, limit = 5) {
  return Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, limit);
}

function sourceTypeForEvent(event, sources) {
  const byId = new Map(sources.map((source) => [String(source.id), source.type]));
  if (byId.has(event.source_id)) return byId.get(event.source_id);
  if (byId.has(String(event.source_id))) return byId.get(String(event.source_id));
  if (event.event_type === "adsb_aircraft") return "adsb";
  if (event.event_type === "aprs_packet") return "aprs";
  if (event.event_type === "satellite_capture") return "satellite";
  return "unknown";
}

function eventFilterType(event, sources) {
  const metadata = parseMetadata(event);
  const sourceType = String(event.source_type || metadata.source_type || sourceTypeForEvent(event, sources) || "").toLowerCase();
  const eventType = String(event.event_type || "").toLowerCase();
  if (isAdsbLikeEvent(event, sources)) return "adsb";
  if (sourceType === "aprs" || eventType.startsWith("aprs")) return "aprs";
  if (sourceType === "satellite" || eventType.includes("satellite") || eventType.includes("capture")) return "satellite";
  return "system";
}

function eventFilterEnabled(event, sources) {
  return Boolean(state.eventFilters[eventFilterType(event, sources)]);
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
        <strong>${eventLineHtml(event)}</strong>
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

function insightHighlightLines(insights = {}) {
  const daily = insights.daily || {};
  return [
    daily.farthest_direct_rf_today ? `APRS farthest direct RF today: ${daily.farthest_direct_rf_today}.` : "",
    daily.farthest_digipeated_rf_today ? `APRS farthest digipeated RF today: ${daily.farthest_digipeated_rf_today}.` : "",
    daily.farthest_network_seen_today_note || "",
    daily.gate_notice_today || insights.aprs?.gate?.notice || "",
    daily.best_aprs_audio_today ? `Best APRS audio today: ${daily.best_aprs_audio_today}.` : "",
    insights.aprs?.notable?.latest_rf ? `Newest RF packet: ${insights.aprs.notable.latest_rf}.` : "",
    daily.adsb_max_range_today ? `ADS-B max range today: ${daily.adsb_max_range_today}.` : "",
    daily.adsb_highest_altitude_today ? `ADS-B highest altitude today: ${daily.adsb_highest_altitude_today}.` : "",
  ].filter(Boolean);
}

function renderOverviewFeed(events, sources, targetId, limit = 20, insights = {}) {
  const filtered = filterOverviewFeedEvents(events, sources);
  const html = filtered.slice(0, limit).map((event) => {
    const isNew = state.initialized && !state.seenEvents.has(event.id);
    const isRecent = secondsAgo(event.timestamp) <= 30;
    return `
      <div class="feed-row ${isNew || isRecent ? "new" : ""}">
        <time>${esc(relTime(event.timestamp))}</time>
        <strong>${eventLineHtml(event)}</strong>
        <span>${esc(event.event_type || "-")}</span>
        <p>${esc(eventDetail(event))}</p>
      </div>
    `;
  }).join("");
  const highlightLines = insightHighlightLines(insights);
  setHtml(targetId, html || `
    ${highlightLines.length ? highlightLines.slice(0, 5).map((line) => `
      <div class="feed-row">
        <time>today</time>
        <strong>${esc(line.split(":")[0])}</strong>
        <span>insight</span>
        <p>${esc(line)}</p>
      </div>
    `).join("") : `
      <div class="feed-row">
        <time>waiting</time>
        <strong>RFLens is listening</strong>
        <span>insight</span>
        <p>No notable APRS, ADS-B, or station health observations are available yet</p>
      </div>
    `}
  `);
  return filtered.length || highlightLines.length;
}

function notableTimelineItems(events, insights = {}) {
  const daily = insights?.daily || {};
  const items = [];
  const add = (type, title, detail, time = "today") => {
    if (detail) items.push({ type, title, detail, time });
  };

  (Array.isArray(insights.summary) ? insights.summary : []).slice(0, 3).forEach((line) => {
    add("insight", "RFLens insight", line);
  });
  (insights.aprs?.plain_english || []).slice(0, 4).forEach((line) => {
    add("aprs", "APRS observation", line);
  });
  add("aprs", "Farthest direct APRS", daily.farthest_direct_rf_today);
  add("aprs", "Farthest digipeated APRS", daily.farthest_digipeated_rf_today);
  add("aprs", "APRS gate notice", daily.gate_notice_today || insights.aprs?.gate?.notice);
  (insights.adsb?.plain_english || []).slice(0, 3).forEach((line) => {
    add("adsb", "ADS-B observation", line);
  });
  add("adsb", "ADS-B max range", daily.adsb_max_range_today);
  add("adsb", "ADS-B highest aircraft", daily.adsb_highest_altitude_today);
  add("adsb", "ADS-B strongest signal", daily.adsb_strongest_signal_today);

  events
    .filter((event) => !["aprs_packet", "adsb_aircraft"].includes(event.event_type))
    .slice(0, 8)
    .forEach((event) => {
      const type = eventFilterType(event, state.latestSources);
      add(type, eventLine(event), eventDetail(event), relTime(event.timestamp));
    });
  return items.slice(0, 18);
}

function renderNotableTimeline(events, insights = {}) {
  const items = notableTimelineItems(events, insights);
  setText("events-tab-count", `${items.length.toLocaleString()} notable`);
  setHtml("events-tab-feed", items.map((item) => `
    <div class="feed-row notable ${esc(item.type)}">
      <time>${esc(item.time)}</time>
      <strong>${esc(item.title)}</strong>
      <span>${esc(item.type)}</span>
      <p>${esc(item.detail)}</p>
    </div>
  `).join("") || `
    <div class="feed-row">
      <time>today</time>
      <strong>No notable station events yet</strong>
      <span>timeline</span>
      <p>RFLens will surface APRS, ADS-B, service, and capture observations here.</p>
    </div>
  `);
  return items.length;
}

function renderEvents(events, sources) {
  const newest = events.slice().sort((a, b) => timeMs(b.timestamp) - timeMs(a.timestamp));
  const eventsTabEvents = newest.filter((event) => eventFilterEnabled(event, sources));
  const recent = newest.filter((event) => secondsAgo(event.timestamp) <= RECENT_SECONDS);
  setText("event-count", newest.length);
  renderNotableTimeline(newest, state.insights);
  setText("overview-events-count", renderOverviewFeed(newest, sources, "overview-event-feed", 20));
  setText("dash-events-recent", newest.length ? `${recent.length} events` : "No data yet");
  setText("dash-events-types", summarizeCounts(countBy(newest, (event) => event.event_type)));
  setText("dash-events-sources", summarizeCounts(countBy(newest, (event) => sourceTypeForEvent(event, sources))));
  setHtml("dash-events-lines", newest.slice(0, 10).map((event) => `
    <div><time>${esc(relTime(event.timestamp))}</time><span>${eventLineHtml(event)}</span></div>
  `).join("") || "<p>No data yet</p>");
  renderFeed(newest, "event-feed", 20);
  renderFeed(eventsTabEvents, "events-raw-feed", 30);
  newest.forEach((event) => state.seenEvents.add(event.id));
}

function stationDetailMetric(label, value) {
  return `<div><dt>${esc(label)}</dt><dd>${esc(value || "No data yet")}</dd></div>`;
}

function stationSummary(events) {
  const enriched = events.map((event) => ({ event, metadata: aprsMetadata(event) }));
  const latest = enriched.slice().sort((a, b) => timeMs(b.event.timestamp) - timeMs(a.event.timestamp))[0];
  const categoryCounts = countBy(enriched, (item) => categoryKey(item.metadata));
  const viaCounts = countStationValues(events, (event) => {
    const via = preferredHeardVia(aprsMetadata(event));
    return via && via !== "direct" ? via : "";
  });
  const bestDistance = enriched
    .map((item) => ({ metadata: item.metadata, miles: Number(item.metadata.distance_miles) }))
    .filter((item) => Number.isFinite(item.miles) && String(item.metadata.distance_quality || "").toLowerCase() !== "questionable")
    .sort((a, b) => b.miles - a.miles)[0]?.metadata;
  const audioValues = enriched
    .map((item) => ({ metadata: item.metadata, level: Number(item.metadata.audio_level) }))
    .filter((item) => Number.isFinite(item.level))
    .sort((a, b) => b.level - a.level);
  const topCategory = Object.entries(categoryCounts).sort((a, b) => b[1] - a[1])[0]?.[0] || "unknown";
  return {
    latest: latest?.event,
    latestMetadata: latest?.metadata || {},
    categoryCounts,
    topCategory,
    topVia: topEntries(viaCounts, 1)[0]?.[0] || "",
    bestDistance,
    bestAudio: audioValues[0] ? aprsAudioLabel(audioValues[0].metadata) : "",
  };
}

function stationCategorySummary(summary) {
  const counts = summary.categoryCounts || {};
  const active = ["direct_rf", "digipeated_rf", "aprs_is"]
    .filter((key) => Number(counts[key]) > 0);
  if (active.length > 1) return "Mixed";
  if (active[0] === "direct_rf") return "Direct RF";
  if (active[0] === "digipeated_rf") {
    return summary.topVia ? `Digipeated RF via ${summary.topVia}` : "Digipeated RF";
  }
  if (active[0] === "aprs_is") return "Network-side";
  return "Unknown";
}

function sortAprsStationRows(rows) {
  const byLastHeard = (a, b) => timeMs(b.summary.latest?.timestamp) - timeMs(a.summary.latest?.timestamp);
  const byPackets = (a, b) => (b.events.length - a.events.length) || byLastHeard(a, b);
  const byDistance = (a, b) => {
    const aMiles = Number(a.summary.bestDistance?.distance_miles);
    const bMiles = Number(b.summary.bestDistance?.distance_miles);
    const aHas = Number.isFinite(aMiles);
    const bHas = Number.isFinite(bMiles);
    if (aHas && bHas && bMiles !== aMiles) return bMiles - aMiles;
    if (aHas !== bHas) return aHas ? -1 : 1;
    return byLastHeard(a, b);
  };
  const byCallsign = (a, b) => a.callsign.localeCompare(b.callsign) || byLastHeard(a, b);
  const sorters = {
    packet_count_desc: byPackets,
    last_heard_desc: byLastHeard,
    distance_desc: byDistance,
    callsign_asc: byCallsign,
  };
  return rows.sort(sorters[state.aprsStationSort] || byPackets);
}

function renderAprsStations(events) {
  const groups = {};
  events.forEach((event) => {
    const callsign = aprsCallsign(event);
    if (!callsign) return;
    if (!groups[callsign]) groups[callsign] = [];
    groups[callsign].push(event);
  });
  const rows = sortAprsStationRows(Object.entries(groups)
    .map(([callsign, stationEvents]) => ({ callsign, events: stationEvents, summary: stationSummary(stationEvents) })));
  const total = rows.length;
  const visibleRows = state.aprsStationsExpanded ? rows : rows.slice(0, APRS_STATION_DEFAULT_LIMIT);
  const toggle = $("aprs-stations-toggle");
  const showing = $("aprs-stations-showing");

  setText("aprs-tab-count", `${total.toLocaleString()} stations`);
  if (showing) {
    showing.textContent = state.aprsStationsExpanded
      ? `Showing all ${total.toLocaleString()} stations`
      : `Showing top ${Math.min(APRS_STATION_DEFAULT_LIMIT, total).toLocaleString()} of ${total.toLocaleString()} stations`;
  }
  if (toggle) {
    toggle.hidden = total <= APRS_STATION_DEFAULT_LIMIT;
    toggle.textContent = state.aprsStationsExpanded ? `Show top ${APRS_STATION_DEFAULT_LIMIT}` : `Show all ${total.toLocaleString()}`;
  }
  setHtml("aprs-stations-list", visibleRows.map(({ callsign, events: stationEvents, summary }) => {
    const metadata = summary.latestMetadata;
    const distance = summary.bestDistance ? stationDetailDistance(summary.bestDistance) : "";
    const category = stationCategorySummary(summary);
    return `
      <article class="station-row" role="button" tabindex="0" data-aprs-callsign="${esc(callsign)}" aria-label="Show APRS station detail for ${esc(callsign)}">
        <div class="station-row-top">
          <strong>${esc(callsign)}</strong>
          ${metadata.station_type ? `<span class="pill">${esc(stationTypeLabel(metadata.station_type))}</span>` : ""}
          <span class="pill">${esc(stationEvents.length.toLocaleString())} packet${stationEvents.length === 1 ? "" : "s"}</span>
        </div>
        <p>${esc(category)} · ${esc(distance || "No distance")} · last heard ${esc(summary.latest ? relTime(summary.latest.timestamp) : "No data yet")}</p>
      </article>
    `;
  }).join("") || "No APRS stations in the current sample yet.");
}

function renderStationPacketRows(events) {
  return events.slice(0, 6).map((event) => {
    const metadata = aprsMetadata(event);
    const labels = [
      aprsCategoryLabel(metadata) || "Unknown",
      aprsViaLabel(metadata),
      stationDetailDistance(metadata),
      aprsAudioLabel(metadata) ? `audio ${aprsAudioLabel(metadata)}` : "",
    ].filter(Boolean);
    return `
      <div class="station-packet-row">
        <time>${esc(fmtDateTime(event.timestamp))}</time>
        <div>
          <div>${labels.map((label) => `<span class="pill">${esc(label)}</span>`).join(" ")}</div>
          <p>${esc(packetSnippet(event, metadata) || "No packet text stored")}</p>
        </div>
      </div>
    `;
  }).join("");
}

function renderAprsStationDetail(events = state.latestAprs) {
  const target = $("aprs-station-detail");
  const subtitle = $("aprs-station-subtitle");
  const selected = state.selectedAprsCallsign;
  if (!target) return;
  if (!selected) {
    if (subtitle) subtitle.textContent = "Click a callsign to inspect how your station heard it.";
    target.className = "aprs-station-detail empty";
    target.innerHTML = "No APRS station selected.";
    return;
  }

  const matching = events
    .filter((event) => aprsCallsign(event) === selected)
    .sort((a, b) => timeMs(b.timestamp) - timeMs(a.timestamp));
  if (matching.length) {
    state.selectedAprsEventsByCallsign[selected] = matching;
    state.selectedAprsEvents = matching;
  }
  const lastKnown = state.selectedAprsEventsByCallsign[selected] || [];
  const stationEvents = matching.length ? matching : lastKnown;
  const stale = !matching.length;
  if (!stationEvents.length) {
    if (subtitle) subtitle.textContent = selected;
    target.className = "aprs-station-detail empty";
    target.innerHTML = `No recent packets for this station in the current APRS sample.`;
    return;
  }

  const enriched = stationEvents.map((event) => ({ event, metadata: aprsMetadata(event) }));
  const latest = enriched[0];
  const latestMetadata = latest.metadata;
  const categoryCounts = countBy(enriched, (item) => categoryKey(item.metadata));
  const viaCounts = countStationValues(stationEvents, (event) => {
    const via = preferredHeardVia(aprsMetadata(event));
    return via && via !== "direct" ? via : "";
  });
  const topVia = topEntries(viaCounts, 1)[0]?.[0] || (categoryCounts.direct_rf ? "direct" : "No data yet");
  const distances = enriched
    .map((item) => ({ metadata: item.metadata, miles: Number(item.metadata.distance_miles) }))
    .filter((item) => Number.isFinite(item.miles) && String(item.metadata.distance_quality || "").toLowerCase() !== "questionable")
    .sort((a, b) => b.miles - a.miles);
  const farthest = distances[0]?.metadata;
  const latestDistance = stationDetailDistance(latestMetadata);
  const audioValues = enriched
    .map((item) => ({ metadata: item.metadata, level: Number(item.metadata.audio_level) }))
    .filter((item) => Number.isFinite(item.level))
    .sort((a, b) => b.level - a.level);
  const recentAudio = aprsAudioLabel(latestMetadata);
  const bestAudio = audioValues[0] ? aprsAudioLabel(audioValues[0].metadata) : "";
  const motion = aprsMotionLabels(latestMetadata).join(", ");
  const note = decodedNote(latestMetadata);
  const gate = gateStatusLabel(latestMetadata);
  const categorySummary = [
    `Direct RF ${categoryCounts.direct_rf || 0}`,
    `Digipeated RF ${categoryCounts.digipeated_rf || 0}`,
    `APRS-IS/network-side ${categoryCounts.aprs_is || 0}`,
    `Unknown ${categoryCounts.unknown || 0}`,
  ].join(" / ");

  if (subtitle) {
    subtitle.textContent = stale
      ? `${selected} - no recent packets in the current sample; showing last known detail.`
      : `${selected} - ${stationEvents.length} packet${stationEvents.length === 1 ? "" : "s"} in the current sample.`;
  }
  target.className = "aprs-station-detail";
  target.innerHTML = `
    ${stale ? `<p class="station-detail-note">No recent packets for this station in the current APRS sample. Showing last known detail.</p>` : ""}
    <div class="station-detail-top">
      <div>
        <span class="eyebrow">callsign</span>
        <h3>${esc(selected)}</h3>
      </div>
      <div class="station-detail-badges">
        <span class="pill">${esc(stationTypeLabel(latestMetadata.station_type || "station"))}</span>
        <span class="pill">${esc(aprsCategoryLabel(latestMetadata) || "Unknown")}</span>
        <span class="pill">${esc(gate)}</span>
      </div>
    </div>
    <dl class="station-detail-grid">
      ${stationDetailMetric("Last heard", relTime(latest.event.timestamp))}
      ${stationDetailMetric("Heard summary", categorySummary)}
      ${stationDetailMetric("Most recent distance", latestDistance)}
      ${stationDetailMetric("Farthest non-questionable", farthest ? stationDetailDistance(farthest) : "")}
      ${stationDetailMetric("Most common via", topVia)}
      ${stationDetailMetric("Recent audio", recentAudio)}
      ${stationDetailMetric("Best audio", bestAudio)}
      ${stationDetailMetric("Decoded motion", motion)}
      ${stationDetailMetric("Direwolf note", note)}
      ${stationDetailMetric("Latest gate status", gate)}
    </dl>
    <section class="station-packet-list">
      <h4>Recent packets</h4>
      ${renderStationPacketRows(stationEvents)}
    </section>
  `;
}

function renderAprsTable(events) {
  renderAprsStations(events);
  const html = events.map((event) => `
    <tr class="${secondsAgo(event.timestamp) <= 30 ? "recent-row" : ""}">
      <td>${esc(fmtTime(event.timestamp))}</td>
      <td>${aprsCallsignButton(aprsCallsign(event))}</td>
      <td>
        <div>${aprsPacketLabels(event).map((label) => `<span class="pill">${esc(label)}</span>`).join(" ")}</div>
        <div class="mono">${esc(event.raw_text || "")}</div>
      </td>
    </tr>
  `).join("");
  setHtml("aprs-table", html);
  setHtml("aprs-tab-table", html || `<tr><td colspan="3">No APRS packets yet</td></tr>`);
  setHtml("events-aprs-table", state.eventFilters.aprs ? html : `<tr><td colspan="3">APRS hidden by filter</td></tr>`);
  renderAprsStationDetail(events);
}

function fallbackText(value) {
  return hasValue(value) ? value : "No data yet";
}

function renderInsights(insights = {}) {
  const summary = Array.isArray(insights.summary) ? insights.summary : [];
  setHtml("insights-summary", (summary.length ? summary : ["RFLens is waiting for fresh station observations."]).slice(0, 6).map((line) => `
    <article class="insight-card">
      <p>${esc(line)}</p>
    </article>
  `).join(""));

  const daily = insights.daily || {};
  setText("insights-daily-aprs-packets", fallbackText(daily.aprs_packets_heard_today));
  setText("insights-daily-aprs-stations", fallbackText(daily.unique_aprs_stations_heard_today));
  setText("insights-daily-direct-rf", fallbackText(daily.direct_rf_heard_today));
  setText("insights-daily-digipeated-rf", fallbackText(daily.digipeated_rf_heard_today));
  setText("insights-daily-network-seen", fallbackText(daily.network_seen_today));
  setText("insights-daily-farthest-direct", fallbackText(daily.farthest_direct_rf_today));
  setText("insights-daily-farthest-digipeated", fallbackText(daily.farthest_digipeated_rf_today));
  setText("insights-daily-farthest-any-rf", fallbackText(daily.farthest_any_rf_today));
  setText("insights-daily-farthest-network", fallbackText(daily.farthest_network_seen_today));
  setText("insights-daily-aprs-audio", fallbackText(daily.best_aprs_audio_today));
  setText("insights-daily-aprs-digi", fallbackText(daily.most_common_digipeater_path_today));
  setText("insights-daily-gate-eligible", fallbackText(daily.gate_eligible_today));
  setText("insights-daily-gate-confirmed", fallbackText(daily.gate_confirmed_today));
  setText("insights-daily-gate-me", fallbackText(daily.confirmed_gated_by_kf8gbu_10_today));
  setText("insights-daily-gate-unconfirmed", fallbackText(daily.gate_unconfirmed_today));
  setText("insights-daily-gate-notice", fallbackText(daily.gate_notice_today || insights.aprs?.gate?.notice));
  setText("insights-daily-gate-competition", fallbackText(daily.gate_competition_note_today));
  setText("insights-daily-adsb-range", fallbackText(daily.adsb_max_range_today));
  setText("insights-daily-adsb-altitude", fallbackText(daily.adsb_highest_altitude_today));
  setText("insights-daily-adsb-signal", fallbackText(daily.adsb_strongest_signal_today));

  const aprsLines = insights.aprs?.plain_english || [];
  const adsbLines = insights.adsb?.plain_english || [];
  setHtml("insights-aprs-list", aprsLines.length ? aprsLines.slice(0, 8).map((line) => `<p>${esc(line)}</p>`).join("") : "<p>No APRS insights yet.</p>");
  setHtml("insights-adsb-list", adsbLines.length ? adsbLines.slice(0, 5).map((line) => `<p>${esc(line)}</p>`).join("") : "<p>No ADS-B insights yet.</p>");
}

function renderDonut(targetId, values) {
  const total = values.reduce((sum, item) => sum + item.value, 0);
  if (!total) {
    setHtml(targetId, "No data yet");
    return;
  }
  let cursor = 0;
  const colors = ["var(--green)", "var(--cyan)", "var(--amber)"];
  const stops = values.map((item, index) => {
    const start = cursor;
    cursor += (item.value / total) * 100;
    return `${colors[index % colors.length]} ${start}% ${cursor}%`;
  }).join(", ");
  setHtml(targetId, `
    <div class="donut" style="background: conic-gradient(${stops});">
      <span>${esc(total.toLocaleString())}</span>
    </div>
    <div class="chart-legend">
      ${values.map((item, index) => `
        <div><i style="background:${colors[index % colors.length]}"></i><span>${esc(item.label)}</span><strong>${esc(item.value.toLocaleString())}</strong></div>
      `).join("")}
    </div>
  `);
}

function renderHourBars(targetId, events) {
  const today = new Date().toDateString();
  const buckets = Array.from({ length: 24 }, () => 0);
  events.forEach((event) => {
    const date = new Date(event.timestamp);
    if (Number.isNaN(date.getTime()) || date.toDateString() !== today) return;
    buckets[date.getHours()] += 1;
  });
  const max = Math.max(...buckets);
  if (!max) {
    setHtml(targetId, "No APRS activity in the current fetched sample yet.");
    return;
  }
  setHtml(targetId, buckets.map((count, hour) => `
    <div class="hour-bar" title="${hour}:00 ${count} packet${count === 1 ? "" : "s"}">
      <span style="height:${Math.max(8, (count / max) * 100)}%"></span>
      <small>${hour % 6 === 0 ? hour : ""}</small>
    </div>
  `).join(""));
}

function renderRankBars(targetId, entries, options = {}) {
  if (!entries.length) {
    setHtml(targetId, "No data yet");
    return;
  }
  const max = Math.max(...entries.map((entry) => entry[1]));
  setHtml(targetId, entries.map(([label, count]) => `
    <div class="rank-row">
      <span>${options.callsign ? aprsCallsignButton(label, "inline-callsign") : esc(label)}</span>
      <div><i style="width:${Math.max(6, (count / max) * 100)}%"></i></div>
      <strong>${esc(count.toLocaleString())}</strong>
    </div>
  `).join(""));
}

function renderVisuals({ aprs, adsb, sources, insights, system }) {
  const daily = insights?.daily || {};
  renderDonut("visual-aprs-categories", [
    { label: "Direct RF", value: Number(daily.direct_rf_heard_today) || 0 },
    { label: "Digipeated RF", value: Number(daily.digipeated_rf_heard_today) || 0 },
    { label: "APRS-IS/network-side", value: Number(daily.network_seen_today) || 0 },
  ]);
  renderHourBars("visual-aprs-hourly", aprs);
  const digiCounts = {};
  aprs.map(aprsMetadata).forEach((metadata) => {
    const via = preferredHeardVia(metadata);
    if (via && via !== "direct") digiCounts[via] = (digiCounts[via] || 0) + 1;
  });
  renderRankBars("visual-aprs-digis", topEntries(digiCounts, 5), { callsign: true });
  renderRankBars("visual-station-types", topEntries(countBy(aprs.map(aprsMetadata), (metadata) => (
    metadata.station_type ? stationTypeLabel(metadata.station_type) : "Station"
  )), 5));
  const aircraft = uniqueAircraftEvents(adsb);
  setHtml("visual-adsb-summary", [
    ["Aircraft sample", aircraft.length ? aircraft.length.toLocaleString() : "No data yet"],
    ["Max range today", fallbackText(daily.adsb_max_range_today)],
    ["Highest today", fallbackText(daily.adsb_highest_altitude_today)],
    ["Strongest signal", fallbackText(daily.adsb_strongest_signal_today)],
  ].map(([label, value]) => `<div><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`).join(""));
  setHtml("visual-services", stationHealthCards(sources, system));
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
  setHtml("events-adsb-table", state.eventFilters.adsb ? html : `<tr><td colspan="5">ADS-B hidden by filter</td></tr>`);
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

function yesNo(value) {
  return value === true ? "yes" : "no";
}

function hasValue(value) {
  return value !== null && value !== undefined && value !== "";
}

function onlineText(value) {
  if (value === true) return "Online";
  if (value === false) return "Offline";
  return "No data yet";
}

function aprsIsText(status) {
  if (status.aprs_is_connected === true && status.aprs_is_verified === true) return "Connected + Verified";
  if (status.aprs_is_connected === true) return "Connected";
  if (status.aprs_is_connected === false) return "Disconnected";
  return "No data yet";
}

function numberOrFallback(value, fallback = "No data yet") {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function audioText(level, quality) {
  const number = Number(level);
  if (!Number.isFinite(number)) return "No data yet";
  return hasValue(quality) ? `${number} (${quality})` : String(number);
}

function renderAprsCard(aprs, aprsStatus = {}) {
  const stations = uniqueBy(aprs, (event) => event.callsign);
  const positioned = uniqueBy(aprs.filter((event) => validCoord(event.lat, event.lon)), (event) => event.callsign || `${event.lat},${event.lon}`);
  const recent = aprs.filter((event) => secondsAgo(event.timestamp) <= RECENT_SECONDS);
  const newest = aprs.slice().sort((a, b) => timeMs(b.timestamp) - timeMs(a.timestamp))[0];
  const enriched = aprs.map((event) => ({ event, metadata: aprsMetadata(event) }));
  const farthest = enriched
    .filter((item) => ["direct_rf", "digipeated_rf"].includes(String(item.metadata.heard_category || "").toLowerCase()))
    .map((item) => ({ event: item.event, metadata: item.metadata, miles: Number(item.metadata.distance_miles) }))
    .filter((item) => Number.isFinite(item.miles))
    .sort((a, b) => b.miles - a.miles)[0];
  const directCount = enriched.filter((item) => String(item.metadata.heard_category || "").toLowerCase() === "direct_rf").length;
  const digipeatedCount = enriched.filter((item) => String(item.metadata.heard_category || "").toLowerCase() === "digipeated_rf").length;
  const networkCount = enriched.filter((item) => String(item.metadata.heard_category || "").toLowerCase() === "aprs_is").length;
  const gateEligible = enriched.filter((item) => item.metadata.gate_eligible === true).length;
  const gateConfirmed = enriched.filter((item) => item.metadata.confirmed_gated_by_me === true).length;
  const gateUnconfirmed = Math.max(gateEligible - gateConfirmed, 0);
  const topDigi = summarizeCounts(countBy(enriched.filter((item) => {
    const via = preferredHeardVia(item.metadata);
    return via && via !== "direct";
  }), (item) => preferredHeardVia(item.metadata)), 1);
  const stationCount = Number(aprsStatus.unique_callsigns_seen);
  const heardTotal = Number(aprsStatus.rf_packets_heard_total);
  const lastAudio = Number(aprsStatus.last_audio_level);
  const bestAudio = Number(aprsStatus.best_audio_level);
  const audioQuality = aprsStatus.last_audio_quality;
  const lastRf = aprsStatus.last_rf_callsign && aprsStatus.last_rf_packet_at
    ? `${aprsStatus.last_rf_callsign}, ${relTime(aprsStatus.last_rf_packet_at)}`
    : (aprsStatus.last_rf_callsign || (newest ? `${newest.callsign || "station"}, ${relTime(newest.timestamp)}` : "No data yet"));

  setMany(["dash-aprs-source", "overview-aprs-source"], onlineText(aprsStatus.online));
  setMany(["dash-aprs-callsign", "overview-aprs-callsign"], aprsStatus.callsign || "No data yet");
  setMany(["dash-aprs-is", "overview-aprs-is"], aprsIsText(aprsStatus));
  setMany(["dash-aprs-last-rf", "overview-aprs-last-rf"], lastRf);
  setMany(["dash-aprs-stations", "overview-aprs-stations"], Number.isFinite(stationCount) ? stationCount : (aprs.length ? stations.length : "No data yet"));
  setMany(["dash-aprs-packets", "overview-aprs-packets"], Number.isFinite(heardTotal) ? heardTotal : (aprs.length ? aprs.length : "No data yet"));
  setMany(["dash-aprs-positioned", "overview-aprs-positioned"], aprs.length ? positioned.length : "No data yet");
  setMany(["dash-aprs-farthest", "overview-aprs-farthest"], farthest ? `${farthest.event.callsign || "station"}, ${farthest.miles.toFixed(0)} mi` : "No data yet");
  setMany(["dash-aprs-direct-digi", "overview-aprs-direct-digi"], aprs.length ? `${directCount} direct / ${digipeatedCount} digipeated` : "No data yet");
  setMany(["dash-aprs-network", "overview-aprs-network"], aprs.length ? networkCount : "No data yet");
  setMany(["dash-aprs-gate-eligible", "overview-aprs-gate-eligible"], aprs.length ? gateEligible : "No data yet");
  setMany(["dash-aprs-gate-confirmed", "overview-aprs-gate-confirmed"], aprs.length ? gateConfirmed : "No data yet");
  setMany(["dash-aprs-gate-unconfirmed", "overview-aprs-gate-unconfirmed"], aprs.length ? gateUnconfirmed : "No data yet");
  setMany(["dash-aprs-top-digi", "overview-aprs-top-digi"], topDigi);
  setMany(["dash-aprs-recent", "overview-aprs-recent"], aprs.length ? `${recent.length} packets` : "No data yet");
  setMany(["dash-aprs-audio", "overview-aprs-audio"], audioText(lastAudio, audioQuality));
  setMany(["dash-aprs-best-audio", "overview-aprs-best-audio"], numberOrFallback(bestAudio));
  setMany(["dash-aprs-server", "overview-aprs-server"], hasValue(aprsStatus.aprs_is_server) ? aprsStatus.aprs_is_server : "No data yet");
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

function stationLocationLabel(station) {
  const lat = formatCoord(station?.lat);
  const lon = formatCoord(station?.lon);
  return lat && lon ? `${lat}, ${lon}` : "No data yet";
}

function profileServiceHtml(source) {
  const status = sourceStatus(source);
  const online = status === "online";
  const seen = source?.last_seen ? relTime(source.last_seen) : "No recent heartbeat";
  const name = source?.name || titleCase(source?.type || "service");
  return `
    <div class="profile-service ${online ? "online" : "offline"}">
      <strong>${esc(name)}</strong>
      <span>${esc(status)} - ${esc(seen)}</span>
    </div>
  `;
}

function systemHealthItems(system) {
  return [
    ["CPU", formatPercent(system?.cpu_percent)],
    ["Memory", formatPercent(system?.memory?.percent)],
    ["Disk", formatPercent(system?.disk?.percent)],
  ].filter(([, value]) => value !== "No data yet");
}

function systemHealthHtml(system) {
  const items = systemHealthItems(system);
  if (!items.length) return "";
  return `
    <div class="profile-service system">
      <strong>System</strong>
      <span>${items.map(([label, value]) => `${esc(label)} ${esc(value)}`).join(" · ")}</span>
    </div>
  `;
}

function stationHealthCards(sources, system) {
  const cards = sources.map(profileServiceHtml);
  const systemCard = systemHealthHtml(system);
  if (systemCard) cards.push(systemCard);
  return cards.join("") || "No service health data yet";
}

function cleanSnapshotValue(value) {
  if (!hasValue(value)) return "";
  const text = String(value)
    .replace(/\s+/g, " ")
    .trim();
  if (!text || /^(no data yet|unknown|null|undefined|nan)$/i.test(text)) return "";
  return text
    .replace(/\bat approximately\b/gi, "—")
    .replace(/\s+—\s+/g, " — ")
    .trim();
}

function snapshotMetric(value) {
  return cleanSnapshotValue(value);
}

function stationHealthLines(sources, system) {
  const lines = [];
  if (sources.length) {
    const liveSources = sources.filter((source) => sourceStatus(source) === "online").length;
    lines.push(`${liveSources}/${sources.length} sources online`);
  }
  systemHealthItems(system).forEach(([label, value]) => {
    lines.push(`${label}: ${value}`);
  });
  return lines;
}

function buildProfileSummary({ station, aprsStatus, insights, sources, system }) {
  const daily = insights?.daily || {};
  const stationName = cleanSnapshotValue(station?.name) || "unknown";
  const callsign = cleanSnapshotValue(aprsStatus?.callsign) || "unknown";
  const grid = cleanSnapshotValue(station?.grid) || "unknown";
  const aprsPackets = formatInteger(daily.aprs_packets_heard_today, "unknown");
  const direct = snapshotMetric(daily.farthest_direct_rf_today);
  const digipeated = snapshotMetric(daily.farthest_digipeated_rf_today);
  const adsbRange = snapshotMetric(daily.adsb_max_range_today);
  const health = stationHealthLines(sources, system);
  const lines = [
    `${stationName} — RFLens Station Snapshot`,
    "",
    `Callsign: ${callsign}`,
    `Grid: ${grid}`,
    "",
    "Today's APRS:",
    `${aprsPackets} packets heard`,
  ];
  if (direct) lines.push(`Farthest direct APRS: ${direct}`);
  if (digipeated) lines.push(`Farthest digipeated APRS: ${digipeated}`);
  if (adsbRange) lines.push("", "ADS-B:", `Max range today: ${adsbRange}`);
  if (health.length) lines.push("", "Station Health:", ...health);
  return lines.join("\n");
}

function renderStationProfile({ station, sources, aprsStatus, insights, system }) {
  const daily = insights?.daily || {};
  const stationName = station?.name || "RF Node";
  const callsign = aprsStatus?.callsign || "No data yet";
  const confirmedGated = Number(daily.confirmed_gated_by_kf8gbu_10_today ?? daily.gate_confirmed_today);
  const gateEligible = Number(daily.gate_eligible_today);
  const gateNote = Number.isFinite(confirmedGated) && confirmedGated > 0
    ? `${confirmedGated.toLocaleString()} packet${confirmedGated === 1 ? "" : "s"} confirmed by APRS-IS path proof today.`
    : "No APRS-IS proof yet that this station gated a packet today.";

  setText("profile-station-name", stationName);
  setText("profile-station-subtitle", `${station?.grid || "Local RF node"} - See what your station hears.`);
  setText("profile-local-first", "Local-first");
  setText("profile-callsign", callsign);
  setText("profile-grid", station?.grid || "No data yet");
  setText("profile-location", stationLocationLabel(station));
  setText("profile-aprs-is-connected", yesNo(aprsStatus?.aprs_is_connected));
  setText("profile-aprs-is-verified", yesNo(aprsStatus?.aprs_is_verified));
  setText("profile-last-aprs", aprsStatus?.last_rf_packet_at ? `${aprsStatus.last_rf_callsign || "APRS"}, ${relTime(aprsStatus.last_rf_packet_at)}` : "No data yet");
  setText("profile-aprs-packets-total", formatInteger(aprsStatus?.rf_packets_heard_total));
  setText("profile-aprs-unique", formatInteger(aprsStatus?.unique_callsigns_seen));
  setText("profile-aprs-packets-today", formatInteger(daily.aprs_packets_heard_today));
  setText("profile-direct-rf-today", formatInteger(daily.direct_rf_heard_today));
  setText("profile-digipeated-rf-today", formatInteger(daily.digipeated_rf_heard_today));
  setText("profile-farthest-direct", fallbackText(daily.farthest_direct_rf_today));
  setText("profile-farthest-digipeated", fallbackText(daily.farthest_digipeated_rf_today));
  setText("profile-gate-eligible", Number.isFinite(gateEligible) ? gateEligible.toLocaleString() : "No data yet");
  setText("profile-gate-confirmed", Number.isFinite(confirmedGated) ? confirmedGated.toLocaleString() : "No data yet");
  setText("profile-gate-note", gateNote);
  setText("profile-adsb-range", fallbackText(daily.adsb_max_range_today));
  setText("profile-adsb-altitude", fallbackText(daily.adsb_highest_altitude_today));
  setText("profile-adsb-signal", fallbackText(daily.adsb_strongest_signal_today));
  setHtml("profile-services", stationHealthCards(sources, system));
  state.profileSummary = buildProfileSummary({ station, aprsStatus, insights, sources, system });
}

function renderHighlights({ records, insights }) {
  const byType = recordByType(records);
  const insightCards = insightHighlightLines(insights).map((line) => {
    const [title, ...rest] = line.split(":");
    return `
      <section class="highlight-card">
        <h3>${esc(title)}</h3>
        <strong>${esc(rest.join(":").trim() || line)}</strong>
        <span>Insight</span>
      </section>
    `;
  });
  const html = insightCards.concat(RECORD_DEFINITIONS.map((definition) => {
    const record = byType[definition.record_type];
    const value = record?.value_text || (record?.value != null ? record.value : "No data yet");
    const detail = record?.callsign || (record?.timestamp ? fmtDateTime(record.timestamp) : "Waiting for an event");
    return `
      <button class="highlight-card record-card" type="button" data-record-type="${esc(definition.record_type)}" ${record ? "" : "disabled"}>
        <h3>${esc(record?.label || definition.label)}</h3>
        <strong>${esc(value)}</strong>
        <span>${esc(detail)}</span>
      </button>
    `;
  })).join("");
  setHtml("highlight-grid", html);
}

function closeRecordModal() {
  const modal = $("record-modal");
  if (!modal) return;
  modal.hidden = true;
  modal.classList.remove("open");
}

function showRecordModal(recordType) {
  const record = recordByType(state.allTimeRecords)[recordType];
  if (!record) return;
  const rows = metadataRows(record);
  setText("record-modal-title", record.label || "Record");
  setHtml("record-modal-body", `
    <dl class="record-detail-list">
      <div><dt>Label</dt><dd>${esc(record.label || "-")}</dd></div>
      <div><dt>record_type</dt><dd>${esc(record.record_type || "-")}</dd></div>
      <div><dt>Value</dt><dd>${esc(record.value_text || record.value || "-")}</dd></div>
      <div><dt>Callsign</dt><dd>${esc(record.callsign || "-")}</dd></div>
      <div><dt>Timestamp</dt><dd>${esc(record.timestamp ? fmtDateTime(record.timestamp) : "-")}</dd></div>
      <div><dt>source_event_id</dt><dd>${esc(record.source_event_id || "-")}</dd></div>
    </dl>
    <div class="metadata-detail">
      <h3>Metadata</h3>
      ${rows.length ? `
        <table>
          <tbody>
            ${rows.map((row) => `
              <tr>
                <th>${esc(row.key)}</th>
                <td>${esc(row.value)}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      ` : `<p>No additional details stored.</p>`}
    </div>
  `);
  const modal = $("record-modal");
  modal.hidden = false;
  modal.classList.add("open");
}

function addRecordAlert(kind, title, detail) {
  const id = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  state.records.alerts.unshift({
    id,
    kind,
    title,
    detail,
    time: new Date().toISOString(),
  });
  state.records.alerts = state.records.alerts.slice(0, 10);
}

function renderRecordAlerts() {
  if (!state.records.alerts.length) {
    setHtml("records-alerts-list", `<div class="record-alert-empty">No alerts yet.</div>`);
    return;
  }
  setHtml("records-alerts-list", state.records.alerts.map((alert) => `
    <div class="record-alert ${esc(alert.kind)}">
      <time>${esc(fmtTime(alert.time))}</time>
      <strong>${esc(alert.title)}</strong>
      <span>${esc(alert.detail)}</span>
    </div>
  `).join(""));
}

function captureKey(capture) {
  if (!capture) return "";
  return String(capture.id ?? capture.image_path ?? `${capture.satellite || "capture"}-${captureTime(capture) || ""}`);
}

function sourceAlertState(source) {
  const status = String(source?.status || "").toLowerCase();
  if (status.includes("offline") || status.includes("error") || status.includes("down")) return "offline";
  if (!source?.last_seen || secondsAgo(source.last_seen) > LIVE_SECONDS) return "stale";
  return "ok";
}

function renderRecordsAndAlerts({ sources, adsb, aprs, captures, system }) {
  const aircraft = uniqueAircraftEvents(adsb);
  const ranges = aircraft.map(adsbRange).filter(Number.isFinite);
  const altitudes = aircraft.map(adsbAltitude).filter(Number.isFinite);
  const maxRange = ranges.length ? Math.max(...ranges) : null;
  const maxAltitude = altitudes.length ? Math.max(...altitudes) : null;
  const callsigns = uniqueBy(aprs, (event) => event.callsign).map((event) => String(event.callsign || "").trim()).filter(Boolean);
  const latestCapture = captures.slice().sort((a, b) => timeMs(captureTime(b)) - timeMs(captureTime(a)))[0];
  const latestKey = captureKey(latestCapture);
  const disk = Number(system?.disk?.percent);

  if (!state.records.initialized) {
    state.records.adsbMaxRange = maxRange;
    state.records.adsbMaxAltitude = maxAltitude;
    callsigns.forEach((callsign) => state.records.aprsCallsigns.add(callsign));
    state.records.latestCaptureKey = latestKey || null;
    sources.filter(expectedLive).forEach((source) => {
      state.records.sourceStates[source.type] = sourceAlertState(source);
    });
    state.records.diskHigh = Number.isFinite(disk) && disk > 85;
    state.records.initialized = true;
    renderRecordAlerts();
    return;
  }

  if (Number.isFinite(maxRange) && (state.records.adsbMaxRange == null || maxRange > state.records.adsbMaxRange)) {
    state.records.adsbMaxRange = maxRange;
    addRecordAlert("adsb", "ADS-B range record", `${maxRange.toFixed(1)} nmi`);
  }
  if (Number.isFinite(maxAltitude) && (state.records.adsbMaxAltitude == null || maxAltitude > state.records.adsbMaxAltitude)) {
    state.records.adsbMaxAltitude = maxAltitude;
    addRecordAlert("adsb", "ADS-B altitude record", `${maxAltitude.toLocaleString()} ft`);
  }
  callsigns.forEach((callsign) => {
    if (!state.records.aprsCallsigns.has(callsign)) {
      state.records.aprsCallsigns.add(callsign);
      addRecordAlert("aprs", "New APRS station", callsign);
    }
  });
  if (latestKey && latestKey !== state.records.latestCaptureKey) {
    state.records.latestCaptureKey = latestKey;
    addRecordAlert("satellite", "New satellite capture", `${latestCapture.satellite || "Satellite"} ${shortPath(latestCapture.image_path)}`);
  }
  sources.filter(expectedLive).forEach((source) => {
    const current = sourceAlertState(source);
    const previous = state.records.sourceStates[source.type];
    if (current !== previous) {
      state.records.sourceStates[source.type] = current;
      if (current !== "ok") addRecordAlert("system", `${source.name || source.type} ${current}`, source.last_seen ? `Last seen ${relTime(source.last_seen)}` : "No recent data");
    }
  });
  if (Number.isFinite(disk)) {
    if (disk > 85 && !state.records.diskHigh) {
      state.records.diskHigh = true;
      addRecordAlert("system", "Disk usage high", `${disk.toFixed(0)}% used`);
    } else if (disk <= 85) {
      state.records.diskHigh = false;
    }
  }

  renderRecordAlerts();
}

function renderOverview({ sources, adsb, aprs, aprsStatus, captures, events, system, records, insights }) {
  renderGlobalCard({ sources });
  renderInsights(insights);
  renderAdsbCard(adsb);
  renderAprsCard(aprs, aprsStatus);
  renderSatelliteCard(captures, events);
  renderSystemCard(system, sources);
  renderStationProfile({ station: state.station, sources, aprsStatus, insights, system });
  renderVisuals({ aprs, adsb, sources, insights, system });
  renderHighlights({ records, insights });
  renderRecordsAndAlerts({ sources, adsb, aprs, captures, system });
  const newest = events.slice().sort((a, b) => timeMs(b.timestamp) - timeMs(a.timestamp));
  setText("overview-events-count", renderOverviewFeed(newest, sources, "overview-event-feed", 20, insights));
  setText("overview-updated", `updated ${fmtTime(new Date().toISOString())}`);
}

async function copyProfileSummary() {
  const button = $("copy-profile-summary");
  const summary = state.profileSummary || "RFLens Node: RF Node";
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(summary);
    } else {
      const textarea = document.createElement("textarea");
      textarea.value = summary;
      textarea.setAttribute("readonly", "");
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand("copy");
      textarea.remove();
    }
    if (button) {
      button.textContent = "Copied station snapshot";
      setTimeout(() => { button.textContent = "Copy profile summary"; }, 1600);
    }
  } catch (error) {
    console.warn("Profile summary copy failed", error);
    if (button) {
      button.textContent = "Copy failed";
      setTimeout(() => { button.textContent = "Copy profile summary"; }, 1600);
    }
  }
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

function selectAprsStation(callsign, openTab = true) {
  const value = normalizeAprsCallsign(callsign);
  if (!value) return;
  state.selectedAprsCallsign = value;
  const matching = state.latestAprs
    .filter((event) => aprsCallsign(event) === value)
    .sort((a, b) => timeMs(b.timestamp) - timeMs(a.timestamp));
  state.selectedAprsEvents = matching;
  if (matching.length) state.selectedAprsEventsByCallsign[value] = matching;
  renderAprsStationDetail(state.latestAprs);
  if (openTab) {
    switchTab("aprs");
    requestAnimationFrame(() => {
      const panel = $("aprs-station-panel");
      panel?.scrollIntoView({ behavior: "smooth", block: "start" });
      panel?.focus?.();
    });
  }
}

function clearAprsStation() {
  state.selectedAprsCallsign = "";
  state.selectedAprsEvents = [];
  renderAprsStationDetail(state.latestAprs);
}

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => switchTab(button.dataset.tab));
});

document.addEventListener("click", (event) => {
  const aprsCallsign = event.target.closest("[data-aprs-callsign]");
  if (aprsCallsign) {
    event.preventDefault();
    event.stopPropagation();
    selectAprsStation(aprsCallsign.dataset.aprsCallsign);
    return;
  }
  if (event.target.closest("#clear-aprs-station")) {
    event.preventDefault();
    clearAprsStation();
    return;
  }
  if (event.target.closest("#aprs-stations-toggle")) {
    event.preventDefault();
    state.aprsStationsExpanded = !state.aprsStationsExpanded;
    renderAprsStations(state.latestAprs);
    return;
  }
  const tabButton = event.target.closest("[data-open-tab]");
  if (tabButton) switchTab(tabButton.dataset.openTab);
  if (event.target.closest("#copy-profile-summary")) copyProfileSummary();
  const recordCard = event.target.closest("[data-record-type]");
  if (recordCard) showRecordModal(recordCard.dataset.recordType);
  const modalClose = event.target.closest("[data-close-record-modal]");
  if (modalClose || event.target.id === "record-modal") closeRecordModal();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeRecordModal();
  if ((event.key === "Enter" || event.key === " ") && event.target.matches?.("[data-aprs-callsign]")) {
    event.preventDefault();
    selectAprsStation(event.target.dataset.aprsCallsign);
  }
});

const aprsStationSort = $("aprs-station-sort");
if (aprsStationSort) {
  aprsStationSort.value = state.aprsStationSort;
  aprsStationSort.addEventListener("change", () => {
    state.aprsStationSort = aprsStationSort.value || "packet_count_desc";
    renderAprsStations(state.latestAprs);
  });
}

function syncEventFilterControls() {
  document.querySelectorAll("[data-event-filter]").forEach((input) => {
    input.checked = Boolean(state.eventFilters[input.dataset.eventFilter]);
  });
}

function rerenderEventsTab() {
  renderEvents(state.latestEvents, state.latestSources);
  renderAprsTable(state.latestAprs);
  renderAdsbTable(state.latestAdsb);
}

document.querySelectorAll("[data-event-filter]").forEach((input) => {
  input.addEventListener("change", () => {
    state.eventFilters[input.dataset.eventFilter] = input.checked;
    rerenderEventsTab();
  });
});

const showAllEvents = $("events-show-all");
if (showAllEvents) {
  showAllEvents.addEventListener("click", () => {
    state.eventFilters = { adsb: true, aprs: true, satellite: true, system: true };
    syncEventFilterControls();
    rerenderEventsTab();
  });
}

async function getAprsStatus() {
  try {
    return await getJson("/api/aprs/status");
  } catch (error) {
    console.warn("APRS status fetch failed", error);
    return state.aprsStatus || {};
  }
}

async function getInsights() {
  try {
    return await getJson("/api/insights");
  } catch (error) {
    console.warn("Insights fetch failed", error);
    return state.insights || {};
  }
}

async function refreshData() {
  try {
    const [station, adsbUi, sources, adsb, aprs, aprsStatus, insights, captures, events, system, records] = await Promise.all([
      getJson("/api/station"),
      getJson("/api/adsb/ui"),
      getJson("/api/sources"),
      getJson(`/api/adsb/recent?limit=${DATA_FETCH_LIMIT}`),
      getJson(`/api/aprs/recent?limit=${DATA_FETCH_LIMIT}`),
      getAprsStatus(),
      getInsights(),
      getJson("/api/captures"),
      getJson(`/api/events/recent?limit=${EVENT_FEED_LIMIT}`),
      getJson("/api/system"),
      getJson("/api/records"),
    ]);
    state.station = station || {};
    state.adsbUi = adsbUi || { enabled: false, url: "" };
    state.latestEvents = events;
    state.latestSources = sources;
    state.latestAdsb = adsb;
    state.latestAprs = aprs;
    state.aprsStatus = aprsStatus || {};
    state.insights = insights || {};
    state.allTimeRecords = records || [];
    $("station").textContent = `${state.station?.name || "RF Node"} ${state.station?.grid || ""}`.trim();
    renderSources(sources);
    renderAdsbUi(state.adsbUi);
    renderEvents(events, sources);
    renderAprsTable(aprs);
    renderAdsbTable(adsb);
    renderCaptures(captures);
    renderOverview({ sources, adsb, aprs, aprsStatus: state.aprsStatus, captures, events, system, records: state.allTimeRecords, insights: state.insights });
    state.initialized = true;
  } catch (error) {
    console.error(error);
  }
}

pollHealth();
refreshData();
setInterval(pollHealth, HEALTH_REFRESH_MS);
setInterval(refreshData, DASHBOARD_REFRESH_MS);

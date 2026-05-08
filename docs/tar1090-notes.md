# tar1090 Notes for RFLens

tar1090 is an ADS-B web interface for readsb / dump1090-fa. RFLens should not copy tar1090 wholesale, but several ideas fit the local ham station dashboard.

## Useful Patterns

- Layered map design: tar1090 separates base maps, aircraft icons, trails, receiver/site circles, and optional overlays.
- Offline/local maps: tar1090 supports offline tiles as a map layer. RFLens mirrors this with `/ui/tiles/osm/{z}/{x}/{y}.png`.
- Range rings: tar1090 has receiver-centered site circles. RFLens uses station range rings where map views are enabled.
- Aircraft visual state: tar1090 styles aircraft by properties such as type, altitude, data source, selection, and stale state. RFLens should surface the station-relevant parts: local range, aircraft counts, signal quality, and freshness.
- Tracks/history: tar1090 emphasizes tracks and adjustable history. RFLens should keep short local-only aircraft history only where it supports station range awareness.
- Map controls: tar1090 exposes map layer selection and display toggles. RFLens can grow map controls only where they support local station observability.

## RFLens Integration Direction

- Keep Leaflet and vanilla JS for the RFLens frontend.
- Keep all tiles, icons, and scripts local under `backend/static/`.
- Avoid public tile requests from the browser.
- Prefer lightweight in-memory animation and stale fading over long browser-side history for now.
- Add persistent tracks later only if the SQLite event volume stays manageable.

## Sources

- tar1090 README: https://github.com/wiedehopf/tar1090
- tar1090 offline map notes: https://github-wiki-see.page/m/ADSBexchange/wiki/wiki/tar1090-offline-map
- tar1090 map visualization overview: https://deepwiki.com/wiedehopf/tar1090/4.1-map-visualization
- tar1090 aircraft display overview: https://deepwiki.com/wiedehopf/tar1090/4.2-aircraft-display-and-formatting

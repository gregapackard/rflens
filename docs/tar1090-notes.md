# tar1090 Notes for RF Lens

tar1090 is an ADS-B web interface for readsb / dump1090-fa. RF Lens should not copy tar1090 wholesale, but several ideas fit the local multi-RF dashboard.

## Useful Patterns

- Layered map design: tar1090 separates base maps, aircraft icons, trails, receiver/site circles, and optional overlays.
- Offline/local maps: tar1090 supports offline tiles as a map layer. RF Lens mirrors this with `/ui/tiles/osm/{z}/{x}/{y}.png`.
- Range rings: tar1090 has receiver-centered site circles. RF Lens uses station range rings in the Map tab.
- Aircraft visual state: tar1090 styles aircraft by properties such as type, altitude, data source, selection, and stale state. RF Lens currently uses source-specific icons plus fresh/stale classes.
- Tracks/history: tar1090 emphasizes tracks and adjustable history. RF Lens should eventually add short local-only aircraft trails using recent event history.
- Map controls: tar1090 exposes map layer selection and display toggles. RF Lens now has a local tile overlay toggle and can grow into label/trail toggles.

## RF Lens Integration Direction

- Keep Leaflet and vanilla JS for the RF Lens frontend.
- Keep all tiles, icons, and scripts local under `backend/static/`.
- Avoid public tile requests from the browser.
- Prefer lightweight in-memory animation and stale fading over long browser-side history for now.
- Add persistent tracks later only if the SQLite event volume stays manageable.

## Sources

- tar1090 README: https://github.com/wiedehopf/tar1090
- tar1090 offline map notes: https://github-wiki-see.page/m/ADSBexchange/wiki/wiki/tar1090-offline-map
- tar1090 map visualization overview: https://deepwiki.com/wiedehopf/tar1090/4.1-map-visualization
- tar1090 aircraft display overview: https://deepwiki.com/wiedehopf/tar1090/4.2-aircraft-display-and-formatting

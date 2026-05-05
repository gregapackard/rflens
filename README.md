# RF Lens

RF Lens is a local-only multi-SDR observability dashboard for ham radio and RF monitoring.

It combines:
- APRS packets from Direwolf
- ADS-B aircraft data from readsb
- Satellite captures from SatDump

into one SQLite-backed FastAPI dashboard.

For ADS-B map rendering, RF Lens intentionally reuses the local readsb/tar1090 web UI instead of reimplementing a full aircraft tracker. tar1090 is the recommended ADS-B map renderer; RF Lens focuses on unified RF dashboarding, service health, event timelines, APRS overview, SatDump capture/pass status, and links or embeds to best-in-class tools.

RF Lens is not a map tool. Its RF Overview is a text dashboard for operational awareness.

Designed for a 24/7 RF closet node, homelab, field station, or Hamvention demo.

## Setup

```bash
cd rflens
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
python -m scripts.init_db
```

## Run the API

```bash
uvicorn backend.main:app --host 0.0.0.0 --port 8080
```

Open the dashboard at:

```text
http://rflens:8080/ui
http://rflens.local:8080/ui
```

RF Lens is hostname-friendly. The frontend uses relative `/api/...` and `/ui/...` paths, so it works through DNS, mDNS, or a reverse proxy such as `http://rflens/`.

## ADS-B Tracking

The ADS-B tab embeds your existing local readsb/tar1090 interface in an iframe. Configure it in `config.yaml`:

```yaml
adsb_ui:
  enabled: true
  url: "http://rfnode.local/tar1090/"
```

If `adsb_ui.enabled` is false or `adsb_ui.url` is empty, RF Lens shows a setup message instead of the iframe. If the iframe cannot load, RF Lens shows an “Open ADS-B Map” button that opens tar1090 in a new tab. ADS-B ingestion still runs through RF Lens so dashboard counts, event tables, and historical observability keep working.

## RF Overview

The RF Overview tab is a text-based operations dashboard. It summarizes ADS-B aircraft counts and max range, APRS stations and packet timing, SatDump capture/pass status, system CPU/disk usage, SDR source health, and the newest cross-source RF events. It does not contain a map.

## Run Everything

Edit `config.yaml` to enable or disable ingestors, then run:

```bash
./scripts/run_all.sh
```

Logs are written to `./data/logs/`.

## Run With systemd

Production-style systemd unit files live in `deploy/systemd/`. They assume RF Lens is installed at `/home/rfnode/rflens` with its virtualenv at `/home/rfnode/rflens/venv`.

Install and start the API and ADS-B ingestor:

```bash
cd /home/rfnode/rflens
bash ./deploy/install_systemd.sh
```

The installer copies units into `/etc/systemd/system`, then runs:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now rflens-api rflens-adsb
```

Check service status:

```bash
sudo systemctl status rflens-api
sudo systemctl status rflens-adsb
```

Follow logs:

```bash
journalctl -u rflens-api -f
journalctl -u rflens-adsb -f
```

An optional APRS ingestor unit is installed but not enabled by default. It only runs `backend.ingestors.aprs_direwolf`; it does not start `rtl_fm` or Direwolf.

```bash
sudo systemctl enable --now rflens-aprs
journalctl -u rflens-aprs -f
```

Remove installed units:

```bash
bash ./deploy/uninstall_systemd.sh
```

## Ingestors

Each ingestor can also be run directly.

```bash
python -m backend.ingestors.adsb_readsb
python -m backend.ingestors.aprs_direwolf
python -m backend.ingestors.satdump_watcher
```

### ADS-B readsb

RF Lens reads readsb aircraft JSON from the configured path:

```yaml
sources:
  adsb:
    enabled: true
    aircraft_json_path: "/run/readsb/aircraft.json"
    poll_seconds: 2
```

Common paths are `/run/readsb/aircraft.json` and `/var/run/readsb/aircraft.json`.

### APRS Direwolf

For the MVP, RF Lens tails a Direwolf text log:

```yaml
sources:
  aprs:
    enabled: true
    log_path: "./data/direwolf.log"
```

It stores packet-like lines with the raw line and best-effort parsed fields.

### SatDump Captures

RF Lens watches a SatDump output directory:

```yaml
sources:
  satellite:
    enabled: true
    captures_path: "/home/rfnode/meteor/captures"
```

New capture folders and image files are inserted into the captures table and mirrored as `satellite_capture` events.

## API

- `GET /api/health`
- `GET /api/station`
- `GET /api/adsb/ui`
- `GET /api/system`
- `GET /api/sources`
- `GET /api/events/recent?limit=100`
- `GET /api/aprs/recent?limit=1000`
- `GET /api/adsb/recent?limit=1000`
- `GET /api/captures`
- `POST /api/events`

## Notes

- RF Lens is local-only and does not call cloud APIs.
- The frontend uses no external CDNs or assets.
- SQLite data lives in `./data/rflens.db` by default.

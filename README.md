# RFLens

RFLens helps amateur radio operators see, measure, and improve their RF footprint across APRS, ADS-B, satellite/weather captures, and local SDR services — using affordable hardware and open-source software.

It gives hams, RF experimenters, and homelab operators a plain-English view of what their station is hearing, how those signals arrived, how far away they were, and whether local RF services are healthy.

It combines:
- APRS packets from Direwolf
- ADS-B aircraft data from readsb/tar1090
- Satellite and weather captures from SatDump
- Local SDR/service health
- SQLite-backed event history and RF insights

For ADS-B map rendering, RFLens intentionally reuses the local readsb/tar1090 web UI instead of reimplementing a full aircraft tracker. tar1090 is the recommended ADS-B map renderer; RFLens focuses on unified RF dashboarding, service health, event timelines, APRS overview, SatDump capture/pass status, and links or embeds to best-in-class tools.

RFLens is built for operational awareness on a 24/7 RF closet node, homelab, field station, or Hamvention demo.

## What RFLens Is For

RFLens is designed to answer practical station questions:

- What did my antenna hear today?
- Was that packet direct, digipeated, or network-side?
- How far away was it?
- What was the farthest direct RF heard today?
- What was digipeated versus actually heard direct?
- Is my iGate connected and eligible to gate?
- Was gating confirmed or unconfirmed?
- Which RF services are healthy?
- Are my SDR services running cleanly?

## Current Scope

### APRS

RF-heard packets, station hints, distance, audio/decode quality, iGate status, and gating evidence. RFLens uses honest RF language. It should distinguish direct RF, digipeated RF, APRS-IS/network-side observations, gate eligible, gate unconfirmed, and confirmed gated. Do not claim a packet was gated by the local station unless APRS-IS path evidence confirms it, such as `qAR`, `qAO`, or `qAS` with the local callsign.

### ADS-B

readsb ingestion, aircraft events, local range/count summaries, and tar1090 integration for map rendering.

### Satellite/weather captures

SatDump capture watching and capture event history.

### Local SDR services

API, ingestor, radio pipeline, and source health visibility.

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

RFLens is hostname-friendly. The frontend uses relative `/api/...` and `/ui/...` paths, so it works through DNS, mDNS, or a reverse proxy such as `http://rflens/`.

## ADS-B Tracking

The ADS-B tab embeds your existing local readsb/tar1090 interface in an iframe. Configure it in `config.yaml`:

```yaml
adsb_ui:
  enabled: true
  url: "http://rfnode.local/tar1090/"
```

If `adsb_ui.enabled` is false or `adsb_ui.url` is empty, RFLens shows a setup message instead of the iframe. If the iframe cannot load, RFLens shows an "Open ADS-B Map" button that opens tar1090 in a new tab. ADS-B ingestion still runs through RFLens so dashboard counts, event tables, and historical observability keep working.

## RF Overview

The RF Overview tab is a text-based operations dashboard. It summarizes ADS-B aircraft counts and max range, APRS stations and packet timing, SatDump capture/pass status, system CPU/disk usage, SDR source health, and the newest cross-source RF events. It does not contain a map.

## Run Everything

Edit `config.yaml` to enable or disable ingestors, then run:

```bash
./scripts/run_all.sh
```

Logs are written to `./data/logs/`.

## Run With systemd

Production-style systemd unit files live in `deploy/systemd/`. They assume RFLens is installed at `/home/rfnode/rflens` with its virtualenv at `/home/rfnode/rflens/venv`.

Install and start the API, ADS-B ingestor, APRS radio pipeline, and APRS ingestor:

```bash
cd /home/rfnode/rflens
bash ./deploy/install_systemd.sh
```

The installer copies units into `/etc/systemd/system`, then runs:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now rflens-api rflens-adsb rflens-aprs-radio rflens-aprs
```

Check service status:

```bash
sudo systemctl status rflens-api
sudo systemctl status rflens-adsb
sudo systemctl status rflens-aprs-radio
sudo systemctl status rflens-aprs
```

Follow logs:

```bash
journalctl -u rflens-api -f
journalctl -u rflens-adsb -f
journalctl -u rflens-aprs-radio -f
journalctl -u rflens-aprs -f
```

The APRS radio unit runs `rtl_fm` for SDR serial `APRS001` at `144.390M` and pipes audio into Direwolf using `/home/rfnode/aprs/direwolf.conf`. It appends Direwolf output to `/home/rfnode/rflens/data/direwolf.log` with `tee`; RFLens tails that log from the APRS ingestor service.

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

RFLens reads readsb aircraft JSON from the configured path:

```yaml
sources:
  adsb:
    enabled: true
    aircraft_json_path: "/run/readsb/aircraft.json"
    poll_seconds: 2
```

Common paths are `/run/readsb/aircraft.json` and `/var/run/readsb/aircraft.json`.

### APRS Direwolf

For the MVP, RFLens tails a Direwolf text log:

```yaml
sources:
  aprs:
    enabled: true
    log_path: "./data/direwolf.log"
```

It stores packet-like lines with the raw line and best-effort parsed fields.

### SatDump Captures

RFLens watches a SatDump output directory:

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

## Project Principles

- Local-first: RFLens runs on the operator's node and does not require cloud services for the dashboard.
- Affordable hardware: it is designed around practical SDRs and local RF node hardware.
- Honest RF language: direct RF, digipeated RF, network-side, gate-eligible, gate-unconfirmed, and confirmed-gated observations should stay clearly separated.
- Open-source software: it builds on tools such as Direwolf, readsb, tar1090, SatDump, SQLite, and FastAPI.
- Operator-focused insight: the dashboard should explain what is notable, not just list raw rows.

## Notes

- RFLens is local-only and does not call cloud APIs.
- The frontend uses no external CDNs or assets.
- SQLite data lives in `./data/rflens.db` by default.

# RFLens

**See what your station hears.**

RFLens is a local-first ham radio observability dashboard for APRS, ADS-B, and station health. It helps amateur radio operators see, measure, and improve their RF footprint using affordable hardware and open-source software.

> RFLens is early alpha software. It is currently best suited for technical users comfortable with Linux, Direwolf, readsb/tar1090, and editing YAML configuration files.

RFLens is ham-forward, station-forward, callsign-forward, club-demo-friendly, and focused on operating. It sits above local ham and RF tools and explains what the station is hearing.

## Why RFLens?

Most ham radio tools are excellent at one job: Direwolf decodes APRS, readsb/tar1090 handles ADS-B, and SatDump captures satellites. RFLens sits above those local tools and turns station activity into a clean operating dashboard.

RFLens is designed to answer practical station questions:

- What is my station hearing today?
- What was direct RF versus digipeated or network-side?
- How far is my APRS and ADS-B receive footprint?
- Are my local RF services healthy?
- What changed over time?

## What RFLens Is

- A local-first observability layer for ham radio stations and RF nodes.
- APRS station intelligence from Direwolf logs.
- Direct RF, digipeated RF, and APRS-IS/network-side separation.
- Honest iGate and gating proof language using APRS-IS path evidence when available.
- ADS-B receiver performance summaries from readsb/tar1090 aircraft data.
- Station health visibility for sources, CPU, memory, disk, and service state.
- A shareable Station Profile snapshot for club demos, station notes, and operating context.
- A dashboard for quick operating awareness.

## What RFLens Is Not

- RFLens is not a SIGINT suite.
- RFLens is not a scanner suite.
- RFLens is not a decode-everything SDR platform.
- RFLens is not an iNTERCEPT clone.
- RFLens is not a replacement for Direwolf, readsb, tar1090, SatDump, or OpenWebRX.
- tar1090 remains the full ADS-B aircraft map and detail view.
- RFLens focuses on receiver performance, station health, and notable observations.

## Current Status

RFLens is early alpha software.

Good fit right now:

- Linux users.
- Hams already running or willing to run Direwolf.
- Users with readsb/tar1090 for ADS-B.
- People comfortable editing YAML and systemd service files.

Not yet ideal for:

- One-click installs.
- Non-technical users.
- Public internet exposure without review.

## Features

- Local-first FastAPI dashboard.
- SQLite event history.
- APRS ingest from Direwolf logs.
- APRS station grid, sorting, show-more, and callsign drilldown.
- Direct RF vs digipeated RF vs APRS-IS/network-side classification.
- Conservative iGate confirmation using APRS-IS q-construct proof.
- Direwolf MIC-E follow-up enrichment when available.
- ADS-B summary from readsb aircraft data.
- tar1090 embed/link for the full ADS-B map view.
- Station Profile share snapshot.
- System/source health: CPU, memory, disk, APRS, ADS-B, and SatDump.
- No cloud dependency required.

## Screenshots

Screenshots will be added as the v0.1 alpha install flow stabilizes.

Planned:

- Dashboard
- APRS Stations Heard
- Station Profile
- ADS-B Receiver Summary

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

RFLens is hostname-friendly. The frontend uses relative `/api/...` and `/ui/...` paths, so it works through DNS, mDNS, or a reviewed reverse proxy such as `http://rflens/`.

## ADS-B Tracking

The ADS-B tab embeds your existing local readsb/tar1090 interface in an iframe. Configure it in `config.yaml`:

```yaml
adsb_ui:
  enabled: true
  url: "http://rfnode.local/tar1090/"
```

If `adsb_ui.enabled` is false or `adsb_ui.url` is empty, RFLens shows a setup message instead of the iframe. If the iframe cannot load, RFLens shows an "Open ADS-B Map" button that opens tar1090 in a new tab. ADS-B ingestion still runs through RFLens so dashboard counts, event tables, and historical observability keep working.

## Station Overview

The Station Overview tab is a text-based operating dashboard. It summarizes ADS-B aircraft counts and max range, APRS stations and packet timing, SatDump capture/pass status, system CPU/disk usage, SDR source health, and the newest station timeline events. It does not replace dedicated tools such as tar1090.

## Run Everything

Edit `config.yaml` to enable or disable ingestors, then run:

```bash
./scripts/run_all.sh
```

Logs are written to `./data/logs/`.

## Run With systemd

Technical-alpha systemd unit files live in `deploy/systemd/`. They assume RFLens is installed at `/home/rfnode/rflens` with its virtualenv at `/home/rfnode/rflens/venv`. Review paths, callsigns, device identifiers, and service permissions before using them on your station.

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

The APRS radio unit runs `rtl_fm` for the configured APRS SDR at `144.390M` and pipes audio into Direwolf using the configured Direwolf path. It appends Direwolf output to `data/direwolf.log` with `tee`; RFLens tails that log from the APRS ingestor service.

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

For the alpha, RFLens tails a Direwolf text log:

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
- `GET /api/aprs/recent?limit=100`
- `GET /api/adsb/recent?limit=100`
- `GET /api/captures`
- `POST /api/events`

## Project Principles

- Local-first: RFLens runs on the operator's node and does not require cloud services for the dashboard.
- Ham-forward: callsigns, station context, iGate language, and RF footprint matter.
- Station-forward: RFLens explains what this local node is hearing and how healthy it is.
- Affordable hardware: it is designed around practical SDRs and local RF node hardware.
- Honest RF language: direct RF, digipeated RF, network-side, gate-eligible, gate-unconfirmed, and confirmed-gated observations should stay clearly separated.
- Open-source software: it builds on tools such as Direwolf, readsb, tar1090, SatDump, SQLite, and FastAPI.
- Operator-focused insight: the dashboard should explain what is notable, not just list raw rows.

## GitHub Metadata

Suggested repo description:

```text
Local-first ham radio observability dashboard for APRS, ADS-B, and station health.
```

Suggested topics:

```text
ham-radio
amateur-radio
aprs
ads-b
sdr
rtl-sdr
direwolf
readsb
tar1090
fastapi
sqlite
station-monitoring
rf
observability
```

## Notes

- RFLens is local-only and does not call cloud APIs.
- The frontend uses no external CDNs or assets.
- SQLite data lives in `./data/rflens.db` by default.
- Review any public internet exposure carefully; the alpha target is local station and trusted-network use.

# Connect Local RF Sources

RFLens does not generate RF data by itself. It reads local outputs from tools you already run on your station:

- Direwolf text logs for APRS packets
- readsb `aircraft.json` for ADS-B aircraft state
- tar1090 for the full ADS-B aircraft map/detail view
- SatDump capture folders for optional satellite/weather capture events

A fresh GitHub clone can run the API and UI with station/system health only. APRS and ADS-B will stay empty until you edit `config.yaml`, enable the matching source, and run the ingestor.

## Manual Startup

```bash
bash ./scripts/setup.sh
nano config.yaml
./venv/bin/python scripts/check_setup.py
bash ./scripts/run_all.sh
```

`run_all.sh` starts the API and only the ingestors whose `sources.<name>.enabled` value is `true`.

## APRS From an Existing Direwolf Log

Use this when Direwolf is already running outside RFLens and writing a text log.

```yaml
station:
  callsign: "N0CALL"
  aprs_callsign: "N0CALL-10"

sources:
  aprs:
    enabled: true
    name: "APRS Direwolf"
    callsign: "N0CALL-10"
    igate_callsign: "N0CALL-10"
    log_path: "/var/log/direwolf/direwolf.log"
```

Replace `N0CALL-10` with your APRS or iGate callsign and set `log_path` to the text log that Direwolf writes on your node. RFLens tails this file; it does not start Direwolf unless you separately configure the optional radio/systemd example.

## ADS-B From readsb aircraft.json

Use this when readsb is already running and updating `aircraft.json`.

```yaml
sources:
  adsb:
    enabled: true
    name: "ADS-B readsb"
    aircraft_json_path: "/run/readsb/aircraft.json"
```

Common paths are:

```text
/run/readsb/aircraft.json
/var/run/readsb/aircraft.json
```

## tar1090 UI Link or Embed

RFLens summarizes ADS-B locally, but tar1090 remains the full aircraft map/detail tool. Point RFLens at your local tar1090 URL:

```yaml
adsb_ui:
  enabled: true
  url: "http://rflens.local/tar1090/"
```

Use the hostname, IP, or reverse-proxy URL that works from the browser viewing RFLens.

## Optional SatDump Captures

Use this when SatDump saves images or capture folders on the same node or a mounted path.

```yaml
sources:
  satellite:
    enabled: true
    name: "SatDump Captures"
    captures_path: "/home/rflens/SatDump"
```

RFLens watches the folder and records new image files or capture directories as station timeline/capture events.

## Verify

After starting RFLens, check the API from the node:

```bash
curl http://localhost:8080/api/health
curl "http://localhost:8080/api/aprs/recent?limit=5"
curl "http://localhost:8080/api/adsb/recent?limit=5"
curl http://localhost:8080/api/insights
```

If you run on a different port, replace `8080` in the curl commands with the port you actually started.

## Troubleshooting

### UI Loads but APRS Is Empty

- Confirm `sources.aprs.enabled: true`.
- Confirm `sources.aprs.log_path` points to the real Direwolf text log.
- Confirm Direwolf is writing packet lines to that file.
- Run `./venv/bin/python scripts/check_setup.py` and fix any APRS path warning.
- Start the APRS ingestor with `bash ./scripts/run_all.sh`, or run `python -m backend.ingestors.aprs_direwolf` from an activated venv.

### UI Loads but ADS-B Is Empty

- Confirm `sources.adsb.enabled: true`.
- Confirm `sources.adsb.aircraft_json_path` points to the real readsb `aircraft.json`.
- Confirm readsb is updating that file.
- Run `./venv/bin/python scripts/check_setup.py` and fix any ADS-B path warning.
- Start the ADS-B ingestor with `bash ./scripts/run_all.sh`, or run `python -m backend.ingestors.adsb_readsb` from an activated venv.

### Default N0CALL Still Showing

Edit `config.yaml`, not `config.example.yaml`, and set:

```yaml
station:
  callsign: "YOURCALL"
  aprs_callsign: "YOURCALL-10"

sources:
  aprs:
    callsign: "YOURCALL-10"
    igate_callsign: "YOURCALL-10"
```

Restart the API after editing config.

### /api/health Shows 8080 While Manually Running Another Port

`/api/health` reports the configured `server.port` from `config.yaml`. If you start manually with an override such as:

```bash
RFLENS_PORT=8081 bash ./scripts/run_api.sh
```

then use the actual runtime port in your browser and curl commands:

```bash
curl http://localhost:8081/api/health
```

To make the reported config match the manual run, update `server.port` in `config.yaml`.

# RFLens Alpha Testing

RFLens is early alpha software for technical hams and RF experimenters.

RFLens is a local-first ham radio observability dashboard. It sits above local tools such as Direwolf, readsb, tar1090, and SatDump to explain what your station hears.

## Who This Is For

- Linux users
- Hams comfortable editing YAML
- Users who already run or are willing to run Direwolf for APRS
- Users who already run or are willing to run readsb/tar1090 for ADS-B
- People comfortable with systemd, journalctl, and basic troubleshooting

## Who This Is Not Yet For

- One-click installs
- Non-technical users
- Public internet exposure without review
- Users expecting RFLens to replace Direwolf, readsb, tar1090, SatDump, or OpenWebRX

## What To Test

- Fresh clone install
- Station-health-only startup
- APRS-only setup
- ADS-B-only setup
- APRS + ADS-B setup
- Station Profile copy summary
- APRS Stations Heard grid
- APRS callsign drilldown
- ADS-B receiver summary
- Timeline / notable events
- systemd service templates

## What To Report

- Hardware used
- OS and version
- Python version
- SDR hardware
- Enabled sources
- Setup/check output
- What worked
- What failed
- Screenshots or log snippets if helpful

Remove secrets, exact private paths, private IPs, and any location/callsign details you do not want public before sharing config or logs.

## Useful Commands

```bash
python3 scripts/check_setup.py
bash scripts/run_api.sh
journalctl -u rflens-api -n 100 --no-pager
journalctl -u rflens-aprs -n 100 --no-pager
ps -eo pid,ppid,cmd,%cpu,%mem --sort=-%cpu | head -20
```

## Suggested First Pass

1. Start with station health only.
2. Confirm `python3 scripts/check_setup.py` returns no blocking failures.
3. Run `bash scripts/run_api.sh`.
4. Open `http://localhost:8080/ui`.
5. Enable APRS, ADS-B, and SatDump one at a time only after the dashboard starts.
6. Try systemd only after manual startup works.

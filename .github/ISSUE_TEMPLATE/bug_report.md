---
name: Bug report
about: Report a problem with RFLens
title: "[Bug]: "
labels: bug
assignees: ""
---

## RFLens Version / Commit

Paste the commit hash or describe the version you tested.

## OS / Hardware

- OS and version:
- Hardware / host:
- Python version:
- SDR hardware, if relevant:

## Enabled Sources

- Station health only:
- APRS / Direwolf:
- ADS-B / readsb / tar1090:
- SatDump captures:

## Expected Behavior

What did you expect RFLens to do?

## Actual Behavior

What happened instead?

## Logs

Paste relevant logs, for example:

```text
journalctl -u rflens-api -n 100 --no-pager
journalctl -u rflens-aprs -n 100 --no-pager
```

## Config Excerpt

Paste only the relevant config excerpt. Remove secrets, private IPs, exact private paths, and any location/callsign details you do not want public.

```yaml

```

## Screenshots

Attach screenshots if this is a UI issue.

## Additional Context

Anything else that might help reproduce or understand the issue.

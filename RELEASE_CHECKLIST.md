# Release Checklist

## v0.1-alpha

- [ ] README updated
- [ ] screenshots present
- [ ] LICENSE present
- [ ] ALPHA_TESTING.md present
- [ ] ROADMAP.md present
- [ ] issue templates present
- [ ] config.example.yaml safe
- [ ] fresh-clone station-health-only test passes
- [ ] check_setup.py passes
- [ ] bash -n scripts passes on Linux
- [ ] python3 compileall passes
- [ ] no hardcoded personal callsign defaults
- [ ] no personal IPs/secrets in example config
- [ ] tag release:

```bash
git tag v0.1-alpha
git push origin v0.1-alpha
```

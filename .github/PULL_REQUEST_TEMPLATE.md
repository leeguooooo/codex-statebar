## Summary

## Test plan

- [ ] `python -m pytest -q`
- [ ] `python -m compileall -q src`
- [ ] Real rollout smoke contains no prompt/session text
- [ ] Layout changes checked with `cxs preview`

## Data and release safety

- [ ] Does not read `auth.json` or select another cwd's rollout
- [ ] Does not add PyPI/npm publishing

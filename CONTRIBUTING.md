# Contributing

Use Python 3.9+ and keep the runtime stdlib-only.

```bash
python -m pip install -e . pytest
python -m pytest -q
python -m compileall -q src
cxs --no-color
```

Codex-specific invariants:

- Do not read `auth.json`.
- Do not emit prompt or tool arguments in diagnostics.
- Resolve rollouts by explicit thread id or matching cwd.
- Keep rendering side-effect free.
- Preserve all three styles and nine themes when changing layout.

Releases are created by pushing a `v*` tag. GitHub Actions builds standalone
platform binaries and attaches `.tar.gz` and `.sha256` assets. Do not publish
this project to PyPI or npm.

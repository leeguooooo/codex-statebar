# Repository Guidelines

## Layout

- `src/codex_statebar/rollout.py` reads local Codex rollout JSONL.
- `core.py` builds the status snapshot; `styles.py`, `progress.py`, and
  `themes.py` render it; `cli.py` provides `cxs`.
- `setup.py` may edit Codex config only after explicit `cxs --setup`.
- `tests/` contains the inherited renderer suite plus Codex-specific rollout,
  setup, doctor, and release tests.

## Commands

- `PYTHONPATH=src python3 -m pytest -q`
- `python3 -m compileall -q src`
- `PYTHONPATH=src python3 -m codex_statebar --no-color`
- `PYTHONPATH=src python3 -m codex_statebar doctor`

## Constraints

- Python 3.9+ and stdlib-only runtime.
- Never read `~/.codex/auth.json` or print prompt/session content.
- Rollout selection must match cwd or explicit thread id; never fall back to
  another project's newest session.
- Rendering is read-only. Do not modify `~/.codex/config.toml` except through
  explicit setup commands.
- Releases are GitHub Release binaries (`.tar.gz` + `.sha256`), not PyPI/npm.
- New user-facing documents are self-contained HTML unless the platform
  requires Markdown.

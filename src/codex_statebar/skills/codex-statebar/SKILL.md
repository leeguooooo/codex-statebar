---
name: codex-statebar
description: Manage cxs (Codex Statebar): inspect usage, switch themes/styles, preview, diagnose, configure native fallback, or run the live dashboard.
---

# Codex Statebar

Use `cxs` for Codex CLI status-bar requests. Prefer commands over editing
`~/.codex/codex-statebar.json` manually.

| Intent | Command |
|---|---|
| Render current session | `cxs` |
| Live dashboard | `cxs watch` |
| Diagnose | `cxs doctor` |
| Configure Codex native fallback | `cxs --setup` |
| Inspect config | `cxs config show` |
| Switch theme | `cxs config set theme <name>` |
| Switch style | `cxs config set style <classic\|capsule\|hairline>` |
| Change density | `cxs config set density <compact\|regular\|cozy>` |
| Preview combinations | `cxs preview` |
| List themes/styles | `cxs themes` / `cxs styles` |
| Machine-readable status | `cxs --json-output` |

Stable Codex supports built-in `[tui].status_line` item IDs but does not yet
run external status commands. `cxs --setup` configures that native fallback.
The rich multi-line renderer works in `cxs`, `cxs watch`, tmux, shell prompts,
and any patched/future Codex build that pipes its session payload to `cxs`.

Do not claim the rich renderer is embedded in stable Codex when `cxs doctor`
reports the external command hook as unavailable.

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
| Pick a look interactively | `cxs theme` |
| Check for a release | `cxs upgrade --check` |
| Upgrade installed binaries | `cxs upgrade` |
| Live dashboard | `cxs watch --style aurora` |
| Pin a dashboard to a thread | `cxs watch --session <thread-id>` |
| Diagnose | `cxs doctor` |
| Reclaim disposable cxs cache | `cxs gc` |
| Configure Codex native fallback | `cxs --setup` |
| Inspect config | `cxs config show` |
| Switch theme | `cxs config set theme <name>` |
| Switch style | `cxs config set style <aurora\|classic\|capsule\|hairline>` |
| Change density | `cxs config set density <compact\|regular\|cozy>` |
| Preview combinations | `cxs preview` |
| List themes/styles | `cxs themes` / `cxs styles` |
| Machine-readable status | `cxs --json-output` |

The managed `codex` launcher automatically enables the embedded multi-line
renderer when the installed patched Codex is at least as new as the official
CLI. The patch targets Codex 0.164.0. `cxs doctor` reports the selected binary
and whether its rich command hook is available.

`cxs --setup` keeps native `[tui].status_line` items as the fallback. The
external argv is passed only to the selected patched process, so a newer
official CLI can still start normally. `cxs watch` is optional for standalone
use. Do not claim embedding works when doctor selects an unpatched CLI.

The bar checks GitHub Releases in the background every six hours. `auto_upgrade=false` keeps checks but disables installation; `CODEX_STATEBAR_NO_UPDATE=1` disables checks. `show_ocs` controls exact-thread OCS address/unread/LAN display. Worktree counts exclude deleted checkouts. New-look notices are acknowledged by `cxs theme`; never edit config during rendering.

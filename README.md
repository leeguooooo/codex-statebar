> Using **Claude Code**? Try the sibling project **[Claude Status Bar](https://github.com/leeguooooo/claude-code-usage-bar)** — the same status bar, built for Claude Code.

# Codex Statebar

**English** · [简体中文](README.zh-CN.md)

Quota, context, model, reasoning effort, Git state, activity and usage projections for the Codex CLI.

![Aurora — the default look, shown under the Codex input box](https://raw.githubusercontent.com/leeguooooo/codex-statebar/main/docs/images/look-aurora.png)

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/leeguooooo/codex-statebar/main/install.sh | sh
cxs doctor
```

The installer verifies SHA-256 checksums, installs `cxs` and the patched `codex-cxs`, then adds a user-level `codex` launcher. An existing `~/.local/bin/codex` is backed up first; the binary bundled with the ChatGPT app is never modified.

The launcher compares the official Codex on your PATH with the patched build. When the patched build is the same version or newer, it runs `codex-cxs`. When the official Codex is newer, it runs the official one and the embedded status bar pauses until a matching patched build ships; `cxs doctor` shows which one was picked. An official update is never silently hidden behind an older patched build. `cxs upgrade` verifies and updates `cxs` and `codex-cxs` together.

GitHub Actions builds standalone binaries for macOS and Linux, so you need neither Python nor Rust. To skip the user-level launcher:

```bash
curl -fsSL https://raw.githubusercontent.com/leeguooooo/codex-statebar/main/install.sh \
  | CODEX_STATEBAR_INSTALL_CODEX_DEFAULT=0 sh
```

On macOS and Linux the runtime unpacks once to `~/.local/lib/codex-statebar/<content-hash>/` and later launches reuse it, instead of creating a `_MEI…` directory on every refresh. A file lock guards concurrent first runs, and a directory left incomplete by an interrupted run is rebuilt on the next start. Set `CODEX_STATEBAR_RUNTIME_DIR` to choose the location. Old runtimes stay in place for sessions still using them; `cxs upgrade` updates the entry point.

Version probing skips cmux's forwarding shim. If another wrapper routes back into `cxs`, the re-entrant probe fails immediately and an independent Codex install is chosen instead, so no recursive process chain can form.

## Usage

| Goal | Command |
| --- | --- |
| Show the current status | `cxs` |
| Live standalone panel | `cxs watch` |
| Diagnose data and wiring | `cxs doctor` |
| Pick a look (live preview) | `cxs theme` |
| Check for updates | `cxs upgrade --check` |
| Preview themes and styles | `cxs preview` |
| Switch theme | `cxs config set theme nord` |
| Switch style | `cxs config set style capsule` |
| Set up the embedded status bar | `cxs --setup` |
| JSON output | `cxs --json-output` |
| Pick an exact session | `cxs --session <thread-id>` |

Data comes from the local `$CODEX_HOME/sessions` and `$CODEX_HOME/archived_sessions`. `cxs` never reads `auth.json` and never uploads session content. Run standalone, it only picks top-level Codex CLI sessions in the current directory, never Codex Desktop or VS Code sessions from the same directory.

Visual settings come from `~/.codex/codex-statebar.json` first. Without a Codex-specific file they inherit `~/.claude/claude-statusbar.json`, so `cs` and `cxs` share one theme, palette and density.

## Embedded in the Codex TUI

The installer configures everything; open a new terminal and run `codex`. The current patch targets Codex 0.164.0, and Aurora and the other multi-line styles appear under the input box. `config.toml` keeps the native fields as a fallback, and the rich status command is passed only to the selected patched process. If the official Codex is newer than the patched build, the launcher prefers the official one.

## New looks, worktrees and OCS

Three looks: Aurora with block bars (default), Aurora with a thin line, and Classic.

![Aurora, block bars (default)](https://raw.githubusercontent.com/leeguooooo/codex-statebar/main/docs/images/look-aurora.png)
![Aurora, thin line](https://raw.githubusercontent.com/leeguooooo/codex-statebar/main/docs/images/look-thin.png)
![Classic](https://raw.githubusercontent.com/leeguooooo/codex-statebar/main/docs/images/look-classic.png)

- When a new set of looks ships, the status bar shows `New looks · cxs theme`. Run `cxs theme` and use the arrow keys to pick Aurora block, Aurora thin or Classic; cancelling keeps your current style. `cxs themes` still lists the palettes.
- The worktree line shows the checkout name, the main repository and the number of live checkouts; leftover records of deleted checkouts are not counted. The main checkout shows the count too, so you can tell which tree you are editing. Configure it with `worktree_glyph` and `color_worktree`.
- With OCS installed, a separate line shows the current Codex session's address, unread messages and LAN state. It is matched by the full thread ID, so another session in the same directory is never shown; status comes only from the read-only OCS CLI and refreshes about every 30 seconds.
- Every 6 hours a background check looks for a new GitHub Release and installs it after verification. `cxs config set auto_upgrade false` keeps the check and the update hint but turns off automatic installs; `CODEX_STATEBAR_NO_UPDATE=1` turns off the check. Run `cxs upgrade --check` or `cxs upgrade` by hand. When an update changes the native Codex version, it only shows a hint: exit Codex, then upgrade by hand, so running sessions keep working. On Windows it only suggests reinstalling.

Things Codex does not report — a five-hour quota, Claude's prompt-cache TTL and per-Claude-model quotas — are never faked. Git, activity, tasks, reasoning effort, themes, projections, the standalone terminal panel and diagnostics are all kept.

Full install notes and resource retention rules (Chinese): [README.html](README.html).

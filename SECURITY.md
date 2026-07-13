# Security Policy

Only the latest release receives security fixes.

Codex Statebar reads local rollout files under `$CODEX_HOME/sessions`, its own
config under `$CODEX_HOME/codex-statebar.json`, and Git metadata for the active
workspace. It must never read `$CODEX_HOME/auth.json` or send session data to a
remote service.

Report command injection, path traversal, cross-workspace rollout disclosure,
unsafe executable replacement, or config corruption privately to
`leeguooooo@gmail.com`, or through GitHub private vulnerability reporting at
`https://github.com/leeguooooo/codex-statebar/security/advisories/new`.

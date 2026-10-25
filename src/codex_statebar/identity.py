"""Project + branch identity resolution from Claude Code stdin payload
and the local filesystem. Pure functions; no top-level subprocess."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _resolve_gitdir(start: Path) -> Optional[Path]:
    """Return the directory that actually contains HEAD, or None.

    Handles three cases:
      - `<start>/.git/` is a directory → return that directory
      - `<start>/.git` is a file with `gitdir: <path>` → return resolved path
      - neither → walk upward and retry
    """
    cur = start.resolve() if start.exists() else start
    for candidate in [cur, *cur.parents]:
        dotgit = candidate / ".git"
        if dotgit.is_dir():
            return dotgit
        if dotgit.is_file():
            try:
                text = dotgit.read_text(encoding="utf-8").strip()
            except OSError:
                return None
            if text.startswith("gitdir:"):
                raw = text.split("gitdir:", 1)[1].strip()
                p = Path(raw)
                if not p.is_absolute():
                    p = (candidate / p).resolve()
                return p if p.exists() else None
            return None
    return None


def read_head(start: Path) -> Optional[Tuple[str, bool]]:
    """Return (branch_or_sha7, detached) or None when not in a git repo.

    - `ref: refs/heads/<name>` → (`<name>`, False) even if the ref file
      doesn't exist yet (unborn branch).
    - 40-char hex SHA → (sha[:7], True)
    - anything else → None
    """
    gitdir = _resolve_gitdir(start)
    if gitdir is None:
        return None
    head_file = gitdir / "HEAD"
    try:
        text = head_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if text.startswith("ref:"):
        ref = text.split("ref:", 1)[1].strip()
        if ref.startswith("refs/heads/"):
            return ref[len("refs/heads/"):], False
        return ref.split("/")[-1], False
    if _SHA_RE.match(text):
        return text[:7], True
    return None


@dataclass
class IdentityInfo:
    project_name: str
    in_git: bool
    branch: Optional[str]
    detached: bool
    worktree_name: Optional[str]
    toplevel: Optional[str]
    is_worktree: bool = False
    worktree_count: int = 0
    web_url: Optional[str] = None


def _worktree_details(start: Path):
    gitdir = _resolve_gitdir(start)
    if gitdir is None:
        return None
    # commondir also covers git repositories whose metadata isn't named .git.
    try:
        common = (gitdir / "commondir").read_text(encoding="utf-8").strip()
        common = (gitdir / common).resolve()
    except OSError:
        common = gitdir.parent.parent if gitdir.parent.name == "worktrees" else gitdir
    linked = common != gitdir
    return (gitdir.name if linked else None, common.parent.name,
            common / "worktrees", linked)


_WORKTREE_COUNTS = {}


def count_live_worktrees(registry: Path) -> int:
    import time
    now = time.monotonic()
    try:
        st = registry.stat()
    except OSError:
        return 0
    key = str(registry)
    signature = (st.st_ino, st.st_mtime_ns)
    old = _WORKTREE_COUNTS.get(key)
    if old and old[0] == signature and now - old[1] < 30:
        return old[2]
    count = 0
    try:
        for entry in registry.iterdir():
            try:
                raw = (entry / "gitdir").read_text(encoding="utf-8").strip()
                if raw and Path(raw).exists():
                    count += 1
            except OSError:
                continue
    except OSError:
        return 0
    if len(_WORKTREE_COUNTS) >= 128:
        _WORKTREE_COUNTS.clear()
    _WORKTREE_COUNTS[key] = (signature, now, count)
    return count


def _detect_worktree(start: Path) -> bool:
    details = _worktree_details(start)
    return bool(details and details[3])


def strip_repo_prefix(name: str, anchor: str) -> str:
    for separator in ("-", "_", "."):
        prefix = anchor + separator
        if name.startswith(prefix) and len(name) > len(prefix):
            return name[len(prefix):]
    return name


def _resolve_toplevel(start: Path) -> Optional[Path]:
    """Best-effort working-tree root for a path inside a git checkout.

    For a normal `.git/` directory layout, returns the directory that
    contains `.git/`. For a linked worktree (`.git` is a file pointing
    elsewhere), returns the directory containing that `.git` file
    (the checkout dir), not the linked gitdir.
    """
    cur = start.resolve() if start.exists() else start
    for candidate in [cur, *cur.parents]:
        dotgit = candidate / ".git"
        if dotgit.exists():
            return candidate
    return None


_REMOTE_CACHE: dict = {}


def remote_web_url(remote: str) -> Optional[str]:
    """`git@host:o/r.git`, `ssh://git@host:22/o/r`, `https://user:tok@host/o/r.git`
    → `https://host/o/r`. Credentials never survive; anything else → None."""
    import re
    remote = remote.strip()
    m = (re.fullmatch(r"[\w.-]+@([\w.-]+):(?!\d+/)([\w./-]+?)(?:\.git)?/?", remote)
         or re.fullmatch(r"(?:ssh|git\+ssh|https?)://(?:[^@/]*@)?([\w.-]+)(?::\d+)?/([\w./-]+?)(?:\.git)?/?",
                         remote))
    if not m or ".." in m.group(2):
        return None
    return f"https://{m.group(1)}/{m.group(2)}"


def branch_web_url(repo_url: Optional[str], branch: Optional[str]) -> Optional[str]:
    """Branch page on the forge: GitLab `/-/tree/`, Bitbucket `/src/`, else `/tree/`."""
    if not repo_url or not branch:
        return None
    from urllib.parse import quote
    host = repo_url.split("/")[2]
    path = "/-/tree/" if "gitlab" in host else "/src/" if "bitbucket" in host else "/tree/"
    return repo_url + path + quote(branch, safe="/")


def _read_web_url(start: Path) -> Optional[str]:
    """origin's URL (else the first remote's) from the repo's own config file —
    no git subprocess; memoized on the config's mtime."""
    gitdir = _resolve_gitdir(start)
    if gitdir is None:
        return None
    common = gitdir
    try:
        rel = (gitdir / "commondir").read_text(encoding="utf-8").strip()
        common = (gitdir / rel).resolve()
    except OSError:
        pass
    cfg = common / "config"
    try:
        sig = (str(cfg), cfg.stat().st_mtime_ns)
    except OSError:
        return None
    if sig in _REMOTE_CACHE:
        return _REMOTE_CACHE[sig]
    import configparser
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    url = None
    try:
        parser.read(cfg, encoding="utf-8")
        sections = [s for s in parser.sections() if s.startswith('remote "')]
        sections.sort(key=lambda s: s != 'remote "origin"')
        for s in sections:
            raw = parser.get(s, "url", fallback=None)
            url = remote_web_url(raw) if raw else None
            if url:
                break
    except (configparser.Error, OSError, UnicodeDecodeError):
        url = None
    _REMOTE_CACHE.clear()
    _REMOTE_CACHE[sig] = url
    return url


def resolve_identity(stdin: dict) -> IdentityInfo:
    repo_name = stdin.get("workspace_repo_name")
    project_dir = stdin.get("workspace_project_dir")
    current_dir = stdin.get("workspace_current_dir")
    worktree_name = stdin.get("workspace_git_worktree")

    if repo_name:
        project_name = repo_name
    elif project_dir:
        project_name = os.path.basename(project_dir.rstrip("/")) or project_dir
    elif current_dir:
        project_name = os.path.basename(current_dir.rstrip("/")) or current_dir
    else:
        project_name = os.path.basename(os.getcwd()) or "?"

    start = Path(current_dir or project_dir or os.getcwd())
    head = read_head(start)
    toplevel = _resolve_toplevel(start) if head else None
    # Trust the local filesystem first (works even when CC omits the field),
    # fall back to the stdin hint for the rare case the cwd isn't on disk.
    details = _worktree_details(start) if head else None
    is_worktree = bool(details and details[3]) or bool(worktree_name)
    if details:
        local_name, anchor, registry, linked = details
        if linked:
            worktree_name = worktree_name or local_name
            if not repo_name:
                project_name = anchor
    return IdentityInfo(
        worktree_count=count_live_worktrees(details[2]) if details else 0,
        web_url=_read_web_url(start) if head else None,
        project_name=project_name,
        in_git=head is not None,
        branch=head[0] if head else None,
        detached=head[1] if head else False,
        worktree_name=worktree_name,
        toplevel=str(toplevel) if toplevel else None,
        is_worktree=is_worktree,
    )


def dirty_with_async_refresh(toplevel: str) -> Optional[bool]:
    """Return the cached dirty state, kicking off a background refresh
    if the cache is stale or missing. Never blocks on git.

    Lazy-imports `subprocess` so `test_import_perf.py` invariants hold
    on the render hot path when the cache is fresh.
    """
    from . import git_cache  # local import keeps top-level imports clean

    entry = git_cache.read_cache(toplevel)
    if git_cache.is_fresh(entry):
        return bool(entry.get("dirty"))

    if not git_cache.is_inflight(toplevel):
        git_cache.mark_inflight(toplevel)
        try:
            import subprocess  # lazy
            import sys
            from .codex_launcher import module_command, independent_process_environment
            subprocess.Popen(
                module_command("codex_statebar._git_refresh", toplevel),
                env=independent_process_environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True,
            )
        except (OSError, ValueError):
            git_cache.clear_inflight(toplevel)

    return None if entry is None else bool(entry.get("dirty"))


def read_ahead_behind(toplevel: str) -> Tuple[Optional[int], Optional[int]]:
    """Return (ahead, behind) from the git cache without triggering a refresh.

    The refresh is already kicked off by ``dirty_with_async_refresh`` (both
    values come from the same ``git status --branch`` call), so this is a
    cheap cache read. (None, None) when unknown / no upstream / cache miss.
    """
    from . import git_cache

    entry = git_cache.read_cache(toplevel)
    if entry is None:
        return None, None
    return entry.get("ahead"), entry.get("behind")

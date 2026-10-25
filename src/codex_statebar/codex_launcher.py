"""Choose between the patched and official Codex binaries.

The installer places a small ``codex`` wrapper beside ``cxs``.  The wrapper
calls this module so an older patched build can never silently shadow a newer
official Codex found later on PATH.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence, Tuple


_VERSION_RE = re.compile(
    r"(?<!\d)(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?"
)
_PROBE_TIMEOUT_SECONDS = 5
_VERSION_PROBE_ENV = "CODEX_STATEBAR_VERSION_PROBE"


@dataclass(frozen=True)
class CodexBinary:
    path: Path
    version_text: str
    version: Tuple[int, int, int, int]
    patched: bool


@dataclass(frozen=True)
class LaunchDecision:
    selected: Optional[CodexBinary]
    patched: Optional[CodexBinary]
    official: Optional[CodexBinary]
    reason: str


def independent_process_environment(source=None) -> dict[str, str]:
    """Return an environment safe for processes independent of frozen cxs."""
    environment = dict(os.environ if source is None else source)
    if getattr(sys, "frozen", False):
        for name in tuple(environment):
            if name.startswith("_PYI_"):
                environment.pop(name, None)
        environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return environment


def module_command(module: str, *arguments: str) -> list[str]:
    """Frozen helpers enter through the installed launcher and acquire a runtime lease."""
    executable = sys.executable
    if getattr(sys, "frozen", False):
        executable = os.environ.get("CODEX_STATEBAR_ENTRYPOINT") or executable
    return [executable, "-m", module, *arguments]


def _version_key(value: str) -> Optional[Tuple[int, int, int, int]]:
    match = _VERSION_RE.search(value)
    if not match:
        return None
    major, minor, patch = (int(match.group(index)) for index in (1, 2, 3))
    stable = 1 if match.group(4) is None else 0
    return major, minor, patch, stable


def _probe(path: Path) -> Optional[CodexBinary]:
    if os.environ.get(_VERSION_PROBE_ENV):
        return None
    environment = independent_process_environment()
    # A PATH entry can be an unrecognized wrapper that routes back through cxs.
    # Its child must fail before starting another set of version probes.
    environment[_VERSION_PROBE_ENV] = "1"
    try:
        completed = subprocess.run(
            [str(path), "--version"],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_SECONDS,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    lines = (completed.stdout.strip() or completed.stderr.strip()).splitlines()
    if not lines:
        return None
    text = lines[0]
    version = _version_key(text)
    if version is None:
        return None
    return CodexBinary(
        path=path,
        version_text=text,
        version=version,
        patched=path.stem == "codex-cxs",
    )


def _same_file(left: Path, right: Path) -> bool:
    try:
        return os.path.samefile(left, right)
    except OSError:
        return left.absolute() == right.absolute()


def _is_managed_launcher(path: Path) -> bool:
    try:
        with path.open("rb") as launcher:
            return b"managed by codex-statebar" in launcher.read(512)
    except OSError:
        return False


def _is_app_embedded_binary(path: Path) -> bool:
    normalized = str(path).replace("\\", "/")
    return ".app/Contents/Resources/" in normalized


def _is_cmux_launcher(path: Path) -> bool:
    # cmux forwards --version to the first non-cmux codex on PATH, which can be
    # our managed launcher. It is a routing layer, not an independent install.
    try:
        if "cmux-cli-shims" in path.parts or "cmux-cli-shims" in path.resolve().parts:
            return True
        with path.open("rb") as handle:
            prefix = handle.read(4096)
        return prefix.startswith(b"#!") and any(
            marker in prefix for marker in (
                b"CMUX_CODEX_WRAPPER_SHIM", b"cmux-codex-wrapper",
                b"cmux_codex_wrapper_is_self_or_shim",
            )
        )
    except OSError:
        return False


def _managed_dir() -> Path:
    configured = os.environ.get("CODEX_STATEBAR_MANAGED_DIR")
    if configured:
        return Path(configured).expanduser().absolute()
    entrypoint = os.environ.get("CODEX_STATEBAR_ENTRYPOINT")
    if getattr(sys, "frozen", False) and entrypoint:
        return Path(entrypoint).absolute().parent
    command = shutil.which("cxs") or shutil.which("codex-statebar")
    if command:
        return Path(command).absolute().parent
    return Path(sys.executable).absolute().parent


def _official_paths(
    path_value: Optional[str],
    managed_dir: Path,
    patched_path: Path,
) -> Iterable[Path]:
    seen = set()
    executable = "codex.exe" if os.name == "nt" else "codex"
    search_path = (
        path_value if path_value is not None else os.environ.get("PATH", "")
    )
    for entry in search_path.split(os.pathsep):
        if not entry:
            continue
        candidate = Path(entry).expanduser().absolute() / executable
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            continue
        if _is_app_embedded_binary(candidate):
            continue
        if _is_managed_launcher(candidate):
            continue
        if _is_cmux_launcher(candidate):
            continue
        if _same_file(candidate, managed_dir / executable):
            continue
        if patched_path.exists() and _same_file(candidate, patched_path):
            continue
        try:
            identity = str(candidate.resolve())
        except OSError:
            identity = str(candidate)
        if identity in seen:
            continue
        seen.add(identity)
        yield candidate


def inspect_install(
    *,
    managed_dir: Optional[Path] = None,
    path_value: Optional[str] = None,
) -> LaunchDecision:
    root = (managed_dir or _managed_dir()).absolute()
    patched_path = root / ("codex-cxs.exe" if os.name == "nt" else "codex-cxs")
    patched = _probe(patched_path) if patched_path.is_file() else None
    officials = [
        binary
        for binary in (
            _probe(path) for path in _official_paths(path_value, root, patched_path)
        )
        if binary is not None
    ]
    stable_officials = [item for item in officials if item.version[3] == 1]
    official = max(
        stable_officials or officials,
        key=lambda item: item.version,
        default=None,
    )

    mode = os.environ.get("CODEX_STATEBAR_CODEX_MODE", "auto").strip().lower()
    if mode == "patched":
        return LaunchDecision(
            patched,
            patched,
            official,
            "forced patched Codex" if patched else "patched Codex is not installed",
        )
    if mode == "official":
        return LaunchDecision(
            official,
            patched,
            official,
            "forced official Codex" if official else "official Codex was not found",
        )
    if official and patched is None:
        return LaunchDecision(
            official,
            patched,
            official,
            "patched Codex is not installed",
        )
    if official and patched and official.version > patched.version:
        return LaunchDecision(
            official,
            patched,
            official,
            "official Codex is newer than the patched build",
        )
    if patched:
        return LaunchDecision(patched, patched, official, "patched Codex is current")
    if official:
        return LaunchDecision(
            official,
            patched,
            official,
            "patched Codex is not installed",
        )
    return LaunchDecision(None, None, None, "no usable Codex binary was found")


def _code_mode_host_name() -> str:
    return "codex-code-mode-host.exe" if os.name == "nt" else "codex-code-mode-host"


def _host_platform() -> Optional[Tuple[str, str]]:
    arch = {"arm64": "aarch64", "aarch64": "aarch64",
            "x86_64": "x86_64", "amd64": "x86_64"}.get(platform.machine().lower())
    system = {"darwin": "apple-darwin", "linux": "linux"}.get(platform.system().lower())
    return (arch, system) if arch and system else None


def _code_mode_host_candidates(
    patched: CodexBinary,
    official: Optional[CodexBinary],
) -> Iterable[Path]:
    # The host speaks a version-specific protocol, so only exact version and
    # architecture matches are acceptable.
    host_platform = _host_platform()
    if host_platform is None:
        return
    arch, system = host_platform
    name = _code_mode_host_name()
    version = ".".join(str(part) for part in patched.version[:3])
    releases = Path(
        os.environ.get("CODEX_HOME") or Path.home() / ".codex"
    ) / "packages" / "app-server-daemon" / "releases"
    yield from sorted(releases.glob(f"{version}-{arch}-*{system}*/bin/{name}"))
    if official is None or official.version[:3] != patched.version[:3]:
        return
    try:
        resolved = official.path.resolve()
    except OSError:
        return
    yield resolved.parent / name
    # npm installs keep native binaries in a platform package beside bin/codex.js.
    yield from sorted(resolved.parent.parent.glob(
        f"node_modules/@openai/codex-*/vendor/{arch}-*{system}*/bin/{name}"
    ))


def ensure_code_mode_host(
    patched: CodexBinary,
    official: Optional[CodexBinary],
) -> Optional[Path]:
    """Link Codex's tool host beside codex-cxs when the release copy is absent.

    Codex resolves ``codex-code-mode-host`` next to its own executable. A real
    file was installed from the matching release and is trusted; a link is
    our own fallback and is re-validated so it never outlives a version match.
    """
    target = patched.path.parent / _code_mode_host_name()
    if target.is_file() and not target.is_symlink():
        return target
    candidates = [
        candidate
        for candidate in _code_mode_host_candidates(patched, official)
        if candidate.is_file() and os.access(candidate, os.X_OK)
    ]
    if target.is_symlink():
        try:
            current = target.resolve()
        except OSError:
            current = None
        if current is not None and any(
            _same_file(current, candidate) for candidate in candidates
        ):
            return target
    if not candidates:
        if target.is_symlink():
            # A stale link to another version is worse than a clear failure.
            try:
                target.unlink()
            except OSError:
                pass
        return None
    staging = target.with_name(f".{target.name}.{os.getpid()}")
    try:
        staging.unlink(missing_ok=True)
        staging.symlink_to(candidates[0])
        os.replace(staging, target)
    except OSError:
        staging.unlink(missing_ok=True)
        return None
    return target


_EXEC_ALIAS_DIR = ".cxs-exec"
_VERSION_KEY_RE = re.compile(r"\d+-\d+-\d+")


def _alias_key(source: os.stat_result) -> str:
    return f"{source.st_dev}-{source.st_ino}-{source.st_mtime_ns}"


def exec_alias_path(selected: CodexBinary) -> Path:
    """Return the private path that execs ``selected``; it only stats the binary.

    Herdr recognizes Codex by the executable's file name, so ``codex-cxs`` is not
    detected. A hard link named ``codex`` in a private directory runs the same
    binary under the name Herdr expects. Each binary version gets its own
    directory keyed by inode and mtime, so a stale launcher cannot overwrite the
    link another launcher published for a newer version.
    """
    source = os.stat(selected.path)
    return selected.path.parent / _EXEC_ALIAS_DIR / _alias_key(source) / "codex"


def _is_private_dir(path: Path) -> bool:
    # lstat, not stat: a symlink (even to a directory) is not ours to write or delete.
    try:
        info = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()


def _prune_alias_versions(root: Path, keep: str) -> None:
    # Each link keeps an old binary's disk space alive, so old versions go.
    # Only our own version directories are candidates; unknown entries stay.
    # The newest other version is kept too, so a session started just before an
    # upgrade still finds its own code-mode host beside its executable.
    # Active sessions hold a shared lease across exec. Only remove an unused
    # directory while holding its exclusive lease, including during deletion.
    try:
        children = [
            child for child in root.iterdir()
            if child.name != keep and _VERSION_KEY_RE.fullmatch(child.name) and _is_private_dir(child)
        ]
        children.sort(key=lambda child: os.lstat(child).st_mtime_ns, reverse=True)
    except OSError:
        return
    for child in children[1:]:
        lease = _alias_lease(child, exclusive=True)
        if lease is not None:
            with lease:
                shutil.rmtree(child, ignore_errors=True)


def _alias_lease(version_dir: Path, *, exclusive: bool = False):
    """Lock a stable file outside the directory that cleanup may remove.

    Never unlink lease files: another process may already have opened one.
    Replacing it would create two independently locked inodes for one version.
    """
    import fcntl

    root = version_dir.parent
    descriptor = None
    try:
        if not _is_private_dir(root):
            return None
        leases = root / ".leases"
        leases.mkdir(mode=0o700, exist_ok=True)
        if not _is_private_dir(leases):
            return None
        descriptor = os.open(
            leases / (version_dir.name + ".lock"),
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
        )
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise OSError("alias lease is not a private regular file")
        fcntl.flock(descriptor, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        handle = os.fdopen(descriptor, "a+")
        descriptor = None
        return handle
    except OSError:
        return None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _same_inode(a: os.stat_result, b: os.stat_result) -> bool:
    return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


def _link_code_mode_host(version_dir: Path, host: Path) -> None:
    """Pin the code-mode host beside the alias.

    Codex looks for its tool host beside the executable it was started as. A
    release copy is hard-linked, so a later upgrade of the managed file cannot
    pair this binary with another version's host. A fallback link from
    ensure_code_mode_host was already version-matched and is followed once.
    """
    link = version_dir / _code_mode_host_name()
    staging = version_dir / f".{link.name}.{os.getpid()}.tmp"
    try:
        if host.is_symlink():
            target = str(host.resolve(strict=True))
            try:
                if os.readlink(link) == target:
                    return
            except OSError:
                pass
            staging.unlink(missing_ok=True)
            staging.symlink_to(target)
        else:
            try:
                if _same_inode(os.lstat(link), os.stat(host)):
                    return
            except OSError:
                pass
            staging.unlink(missing_ok=True)
            os.link(host, staging)
        os.replace(staging, link)
    finally:
        try:
            staging.unlink(missing_ok=True)
        except OSError:
            pass


def _shared_upgrade_lock(managed: Path):
    """Hold the updater's lock in shared mode, or return None while an upgrade runs.

    The updater replaces codex-cxs and its host under this lock in exclusive
    mode, so a shared hold never observes a half-upgraded pair.
    install.sh swaps the pair under the same lock (via perl's flock); without
    perl it swaps unlocked, a documented ceiling there.
    """
    if os.name == "nt":
        return None
    import fcntl
    try:
        handle = (managed / ".cxs-upgrade.lock").open("a+")
    except OSError:
        return None
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def _publish_alias(selected: CodexBinary, host: Optional[Path] = None) -> Optional[Path]:
    """Publish the hard link for ``selected``; return None when that is not possible.

    Runs under the updater's lock in shared mode. The link is created under a
    temp name and moved into place with ``os.replace``; identities are checked
    again after linking. When ``host`` is given it is pinned beside the alias;
    if that fails the alias is not used.
    """
    root = selected.path.parent / _EXEC_ALIAS_DIR
    lock = _shared_upgrade_lock(selected.path.parent)
    if lock is None:
        return None
    temp = None
    try:
        source = os.stat(selected.path)
        key = _alias_key(source)
        root.mkdir(mode=0o755, exist_ok=True)
        if not _is_private_dir(root):
            raise OSError("alias root is not a private directory")
        version_dir = root / key
        version_dir.mkdir(mode=0o755, exist_ok=True)
        if not _is_private_dir(version_dir):
            raise OSError("alias version directory is not a private directory")
        alias = version_dir / "codex"
        if host is not None:
            _link_code_mode_host(version_dir, host)
        try:
            current = os.lstat(alias)
            if stat.S_ISREG(current.st_mode) and _same_inode(current, source):
                _prune_alias_versions(root, key)
                return alias
        except FileNotFoundError:
            pass
        temp = version_dir / f".codex.{os.getpid()}.tmp"
        temp.unlink(missing_ok=True)
        os.link(selected.path, temp)
        if _alias_key(os.stat(temp)) != key:
            raise OSError("patched Codex changed while publishing its link")
        os.replace(temp, alias)
        _prune_alias_versions(root, key)
        return alias
    except OSError:
        if temp is not None:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
        return None
    finally:
        lock.close()


def _candidate_paths(selected: CodexBinary, host: Optional[Path] = None) -> list[Path]:
    if not selected.patched:
        return [selected.path]
    alias = _publish_alias(selected, host)
    return [alias, selected.path] if alias else [selected.path]


def _exec_chain(paths: Sequence[Path], args: Sequence[str], environment: Optional[dict] = None) -> int:
    """Try each path once with the same argv and environment; never retry recursively."""
    failure: Optional[OSError] = None
    for path in paths:
        lease = None
        try:
            if (path.name == "codex" and path.parent.parent.name == _EXEC_ALIAS_DIR
                    and _VERSION_KEY_RE.fullmatch(path.parent.name)):
                lease = _alias_lease(path.parent)
                if lease is None:
                    raise OSError("cannot lease patched Codex alias")
                # Cleanup may have won the lock before us. Do not exec an alias
                # whose directory was removed while waiting to acquire a lease.
                if not path.is_file():
                    raise FileNotFoundError(path)
                os.set_inheritable(lease.fileno(), True)
            os.execve(
                str(path),
                [str(path), *args],
                independent_process_environment() if environment is None else environment,
            )
        except OSError as exc:
            failure = exc
            continue
        finally:
            if lease is not None:
                lease.close()
        return 127
    print(f"codex-statebar: could not start {paths[-1]}: {failure}", file=sys.stderr)
    return 127


def launch(args: Sequence[str]) -> int:
    if os.environ.get(_VERSION_PROBE_ENV):
        print("codex-statebar: refused recursive Codex version probe", file=sys.stderr)
        return 126
    decision = inspect_install()
    selected = decision.selected
    if selected is None:
        print(f"codex-statebar: {decision.reason}", file=sys.stderr)
        return 127
    # Selecting a newer official Codex is the healthy default. The native
    # status-line fallback remains available, and `cxs doctor` reports the full
    # routing decision when diagnostics are needed, so normal launches stay quiet.
    #
    # A frozen cxs process can be replaced by Codex, which may later invoke cxs
    # again through hooks. PyInstaller otherwise treats that nested invocation as
    # its own child even though Codex is now between the two processes, and recent
    # bootloaders reject it because the immediate parent executable is different.
    # Mark later frozen descendants as independent before replacing this process.
    environment = independent_process_environment()
    environment.pop("CODEX_STATEBAR_STATUS_COMMAND", None)
    host = None
    if selected.patched:
        import json
        host = ensure_code_mode_host(selected, decision.official)
        entrypoint = os.environ.get("CODEX_STATEBAR_ENTRYPOINT")
        command = ([entrypoint, "render"] if entrypoint else
                   ([sys.executable, "render"] if getattr(sys, "frozen", False) else
                    [sys.executable, "-m", "codex_statebar", "render"]))
        environment["CODEX_STATEBAR_STATUS_COMMAND"] = json.dumps(command)
    return _exec_chain(_candidate_paths(selected, host), args, environment)

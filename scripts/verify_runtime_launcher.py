"""Smoke-test the actual POSIX release, including a killed cold extraction."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def verify(entrypoint: Path) -> dict:
    entrypoint = entrypoint.resolve()
    with tempfile.TemporaryDirectory(prefix="cxs-runtime-smoke-") as directory:
        root = Path(directory)
        cache = root / "runtime"
        temp = root / "tmp"
        temp.mkdir()
        env = {**os.environ, "CODEX_STATEBAR_RUNTIME_DIR": str(cache),
               "TMPDIR": str(temp), "PYINSTALLER_RESET_ENVIRONMENT": "1"}

        def launch(_=None):
            result = subprocess.run([str(entrypoint), "--version"], env=env,
                                    capture_output=True, text=True, timeout=30)
            assert result.returncode == 0, result.stderr
            return result.stdout.strip()

        with ThreadPoolExecutor(max_workers=8) as pool:
            versions = list(pool.map(launch, range(16)))
        runtimes = [p for p in cache.iterdir() if p.is_dir()]
        assert len(runtimes) == 1, runtimes
        executable = runtimes[0] / "cxs"
        identity = (executable.stat().st_ino, executable.stat().st_mtime_ns)
        for _ in range(16):
            assert launch() == versions[0]
        assert identity == (executable.stat().st_ino, executable.stat().st_mtime_ns)
        assert not list(temp.glob("_MEI*")), "per-launch extraction returned"

        interrupted = root / "interrupted"
        env["CODEX_STATEBAR_RUNTIME_DIR"] = str(interrupted)
        process = subprocess.Popen([str(entrypoint), "--version"], env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        killed = False
        try:
            deadline = time.monotonic() + 10
            while process.poll() is None and time.monotonic() < deadline:
                if any(interrupted.glob("*.staging")):
                    process.kill()
                    killed = True
                    break
                time.sleep(0.001)
            process.wait(timeout=15)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=15)
        assert killed, "did not observe the cold extraction window"
        assert launch() == versions[0]
        assert len([p for p in interrupted.iterdir() if p.is_dir()]) == 1
        assert not list(interrupted.glob("*.staging"))
        assert not list(temp.glob("_MEI*"))
        return {"version": versions[0], "parallel_starts": 16,
                "warm_starts": 16, "runtime_directories": 1,
                "new_MEI_directories": 0, "killed_extract_recovered": True}


if __name__ == "__main__":
    print(json.dumps(verify(Path(sys.argv[1])), indent=2))

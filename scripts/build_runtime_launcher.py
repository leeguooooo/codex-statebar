"""Embed a PyInstaller onedir bundle in a persistent-runtime POSIX launcher.

The resulting single cxs file remains compatible with pre-0.1.8 installers and
upgraders, which extract only the cxs archive member.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import tarfile
import tempfile


def build(bundle: Path, destination: Path) -> str:
    if not (bundle / "cxs").is_file():
        raise ValueError(f"Missing onedir executable: {bundle / 'cxs'}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cxs-package-") as directory:
        work = Path(directory)
        payload = work / "runtime.tar.gz"
        with tarfile.open(payload, "w:gz", dereference=False) as archive:
            for child in sorted(bundle.iterdir()):
                archive.add(child, arcname=child.name)
        data = payload.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        (work / "runtime_bundle.h").write_text(
            f'#define CXS_PAYLOAD_HASH "{digest}"\n'
            f'#define CXS_PAYLOAD_SIZE {len(data)}UL\n'
        )
        mac = platform.system() == "Darwin"
        symbol = "_cxs_payload_start" if mac else "cxs_payload_start"
        assembly = work / "payload.S"
        assembly.write_text(
            (".section __TEXT,__const\n" if mac else ".section .rodata\n")
            + f".globl {symbol}\n{symbol}:\n.incbin {json.dumps(str(payload))}\n"
            + ("" if mac else '.section .note.GNU-stack,"",@progbits\n')
        )
        subprocess.run([
            os.environ.get("CC", "cc"), "-std=c99", "-O2", "-Wall", "-Wextra",
            "-Werror", "-I", str(work),
            str(Path(__file__).with_name("runtime_launcher.c")), str(assembly),
            "-o", str(destination),
        ], check=True)
        if mac:
            subprocess.run(["codesign", "--force", "--sign", "-", str(destination)],
                           check=True)
        return digest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    build(args.bundle, args.destination)

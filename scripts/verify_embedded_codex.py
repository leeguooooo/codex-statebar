"""Exercise the real TUI footer in an isolated PTY without user auth or prompts."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import pty
import re
import select
import signal
import struct
import tempfile
import termios
import time


ANSI = re.compile(rb'\x1b\[[0-?]*[ -/]*[@-~]')


def verify(binary, expect_native=False):
    binary = str(Path(binary).resolve())
    with tempfile.TemporaryDirectory(prefix='codex-footer-acceptance-') as directory:
        root = Path(directory)
        home = root / 'home'
        home.mkdir()
        codex = home / '.codex'
        codex.mkdir()
        project = root / 'workspace'
        project.mkdir()
        config = '''model = "gpt-ui-probe"
model_provider = "footer_probe"
check_for_update_on_startup = false
[tui]
status_line = ["model"]
[model_providers.footer_probe]
name = "Footer acceptance fixture"
base_url = "http://127.0.0.1:9/v1"
wire_api = "responses"
requires_openai_auth = false
'''
        config += '\n[projects.' + json.dumps(str(project)) + ']\ntrust_level = "trusted"\n'
        (codex / 'config.toml').write_text(config)
        renderer = root / 'renderer.py'
        renderer.write_text('''import json, os, sys
data = json.load(sys.stdin)
assert data["codex_version"] == "0.164.0"
assert data["terminal_columns"] == int(os.environ["COLUMNS"])
columns = int(os.environ.get("COLUMNS", "0"))
print(str(columns) * 8 if columns < 50 else "AURORA_LINE_ONE columns=" + str(columns))
print("AURORA_PROJECT_LINE")
print("AURORA_MODE_LINE")
''')
        environment = {k: v for k, v in os.environ.items()
                       if not k.startswith('_PYI_') and k not in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY')}
        environment.update(HOME=str(home), CODEX_HOME=str(codex), TERM='xterm-256color',
                           CODEX_STATEBAR_STATUS_COMMAND=json.dumps([__import__('sys').executable, str(renderer)]))
        pid, master = pty.fork()
        if pid == 0:
            os.chdir(project)
            os.execve(binary, [binary, '--no-daemon', '--no-alt-screen'], environment)
        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack('HHHH', 32, 120, 0, 0))
        captured = b''
        started = time.monotonic()
        resized = False
        embedded = False
        try:
            while time.monotonic() - started < 30:
                if (expect_native and time.monotonic() - started > 5
                        and b'gpt-ui-probe' in ANSI.sub(b'', captured)):
                    assert b'AURORA_LINE_ONE' not in captured
                    return {'native_baseline': True, 'isolated_auth': True}
                if not select.select([master], [], [], 0.2)[0]:
                    continue
                try:
                    data = os.read(master, 65536)
                except OSError:
                    break
                if not data:
                    break
                if b'\x1b[6n' in data:
                    os.write(master, b'\x1b[1;1R')
                if b'\x1b[c' in data:
                    os.write(master, b'\x1b[?1;2c')
                captured = (captured + data)[-131072:]
                plain = ANSI.sub(b'', captured)
                resized_columns = re.search(rb'(\d{2})\1{5,}', plain) if resized else None
                if not expect_native and resized_columns:
                    columns = int(resized_columns.group(1))
                    assert 0 < columns <= 40
                    return {'embedded_three_rows': True, 'resize_width_verified': True,
                            'resize_columns': columns, 'isolated_auth': True, 'prompts_sent': 0}
                if not expect_native and not resized and all(marker in plain for marker in
                         (b'AURORA_LINE_ONE', b'AURORA_PROJECT_LINE', b'AURORA_MODE_LINE')):
                    embedded = True
                    if not resized:
                        captured = b''
                        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack('HHHH', 32, 40, 0, 0))
                        os.kill(pid, signal.SIGWINCH)
                        resized = True
            plain = ANSI.sub(b'', captured).decode('utf-8', 'replace')
            raise AssertionError(f'footer acceptance failed (embedded={embedded}, resized={resized}): {plain[-4000:]}')
        finally:
            try:
                os.write(master, b'\x03\x03')
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    if os.waitpid(pid, os.WNOHANG)[0]:
                        break
                    time.sleep(0.1)
                else:
                    os.killpg(pid, signal.SIGKILL)
                    os.waitpid(pid, 0)
            except (OSError, ChildProcessError):
                pass
            os.close(master)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binary')
    parser.add_argument('--expect-native', action='store_true')
    args = parser.parse_args()
    print(json.dumps(verify(args.binary, args.expect_native), indent=2))

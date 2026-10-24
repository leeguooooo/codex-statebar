"""Standalone dashboard with bounded refresh and terminal cleanup."""
import argparse
import io
import math
import os
import sys
import time
from contextlib import redirect_stdout


def run(argv):
    from .styles import list_styles
    from .themes import list_themes

    parser = argparse.ArgumentParser(prog="cxs watch")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--session", metavar="THREAD_ID")
    parser.add_argument("--style", choices=list_styles())
    parser.add_argument("--theme", choices=[t.name for t in list_themes()])
    parser.add_argument("--no-color", action="store_true")
    args = parser.parse_args(argv)
    if not math.isfinite(args.interval) or args.interval < 0.2:
        print("--interval must be a finite number of at least 0.2 seconds", file=sys.stderr)
        return 2

    from .core import main as render_main
    terminal = sys.stdout.isatty()
    previous_stdin = sys.stdin
    previous_thread = os.environ.get("CODEX_STATEBAR_THREAD_ID")
    try:
        if args.session:
            os.environ["CODEX_STATEBAR_THREAD_ID"] = args.session
        # A dashboard discovers local rollouts on every tick. Never wait for
        # stdin EOF (e.g. when launched by a shell or process supervisor).
        sys.stdin = io.StringIO("")
        if terminal:
            print("\x1b[?1049h\x1b[?25l", end="", flush=True)
        while True:
            from .maintenance import maintain_cache
            maintain_cache()
            started = time.monotonic()
            frame = io.StringIO()
            with redirect_stdout(frame):
                render_main(use_color=terminal and not args.no_color and "NO_COLOR" not in os.environ,
                            style_override=args.style, theme_override=args.theme,
                            _suppress_side_effects=True)
            print(("\x1b[H" if terminal else "") + frame.getvalue()
                  + ("\x1b[J" if terminal else ""), end="", flush=True)
            time.sleep(max(0, args.interval - (time.monotonic() - started)))
    except KeyboardInterrupt:
        return 0
    finally:
        sys.stdin = previous_stdin
        if previous_thread is None:
            os.environ.pop("CODEX_STATEBAR_THREAD_ID", None)
        else:
            os.environ["CODEX_STATEBAR_THREAD_ID"] = previous_thread
        if terminal:
            print("\x1b[?25h\x1b[?1049l", end="", flush=True)

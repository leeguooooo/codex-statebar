"""Dashboard usability: keep data visible and terminal state recoverable."""
import io
import os
import re
import sys

import pytest

from codex_statebar import core, watch
from codex_statebar._display import clip_line, visible_width
from codex_statebar.styles import list_styles, render


@pytest.mark.parametrize('style', list_styles())
@pytest.mark.parametrize('color', [False, True])
@pytest.mark.parametrize('width', [20, 40, 60, 80])
def test_narrow_layout_preserves_segments(style, color, width):
    out = render(style, msgs_pct=42, weekly_pct=18,
                 reset_5h='2h47m', reset_7d='3d12h',
                 model='gpt-6.1-sol', ctx_pct=35,
                 bypass=True, use_color=color, max_width=width,
                 mode_show=True, mode_effort='high',
                 cwd_text='工程目录很长' * 12)
    assert all(visible_width(line) <= width for line in out.splitlines())
    plain = re.sub(r'\x1b\[[0-9;]*m', '', out)
    assert '42' in plain and '18' in plain and 'gpt-6.1-sol' in plain
    assert 'BYPASS' in plain
    if not color:
        assert '\x1b' not in out


@pytest.mark.parametrize('style', list_styles())
def test_weekly_toggle_works_for_every_style(style):
    out = render(style, msgs_pct=42, weekly_pct=18, reset_5h='2h',
                 reset_7d='3d', model='gpt-test', show_weekly=False,
                 use_color=False, max_width=40)
    assert '42' in out
    assert '18' not in out and '7d' not in out.lower()


def test_clipping_preserves_combining_text_and_closes_color():
    text = '\x1b[31m中文e\u0301abcdef'
    clipped = clip_line(text, 7)
    assert visible_width(clipped) == 7
    assert 'e\u0301' in clipped
    assert clipped.endswith('\x1b[0m')


@pytest.mark.parametrize('interval', ['nan', 'inf', '-1', '0'])
def test_watch_rejects_invalid_interval(interval, capsys):
    assert watch.run(['--interval=' + interval]) == 2
    assert '--interval' in capsys.readouterr().err


def test_watch_does_not_read_stdin_and_restores_session(monkeypatch, capsys):
    class Unreadable(io.StringIO):
        def read(self, *_args):
            raise AssertionError('dashboard must not block on stdin')
    original = Unreadable()
    monkeypatch.setattr(sys, 'stdin', original)
    monkeypatch.setenv('CODEX_STATEBAR_THREAD_ID', 'original')
    def render_frame(**kw):
        assert os.environ['CODEX_STATEBAR_THREAD_ID'] == 'chosen'
        assert sys.stdin.read() == ''
        assert not kw['use_color']
        assert kw['style_override'] == 'aurora'
        print('frame')
    monkeypatch.setattr(core, 'main', render_frame)
    def stop(_seconds):
        raise KeyboardInterrupt
    monkeypatch.setattr(watch.time, 'sleep', stop)
    assert watch.run(['--session', 'chosen', '--style', 'aurora']) == 0
    assert sys.stdin is original
    assert os.environ['CODEX_STATEBAR_THREAD_ID'] == 'original'
    assert capsys.readouterr().out == 'frame\n'


def test_watch_restores_terminal_after_render_error(monkeypatch):
    output = io.StringIO()
    output.isatty = lambda: True
    monkeypatch.setattr(sys, 'stdout', output)
    def fail(**_kw):
        raise RuntimeError('render failed')
    monkeypatch.setattr(core, 'main', fail)
    with pytest.raises(RuntimeError):
        watch.run([])
    assert output.getvalue().startswith('\x1b[?1049h\x1b[?25l')
    assert output.getvalue().endswith('\x1b[?25h\x1b[?1049l')


@pytest.mark.parametrize('style', list_styles())
def test_codex_can_hide_five_hour_window_in_every_style(style):
    out = render(style, msgs_pct=42, weekly_pct=18, reset_5h='2h',
                 reset_7d='3d', model='gpt-test', show_five_hour=False,
                 use_color=False, max_width=40)
    assert '5h' not in out.lower() and '42' not in out
    assert '18' in out and 'gpt-test' in out

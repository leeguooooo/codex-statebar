"""Status-line layout renderers (style = layout, theme = palette).

Each renderer takes the same set of fields as ``progress.format_status_line``
plus a Theme, and returns the final ANSI string.

Adding a new style:
    1. Define ``render_<name>(...) -> str``
    2. Register it in ``RENDERERS``.
"""

from typing import Optional

from .themes import Theme, get_theme

RESET = "\033[0m"
BOLD  = "\033[1m"
ITAL  = "\033[3m"
FAINT = "\033[2m"   # dim/faint attribute — makes a grey recede even further

# Installed version, resolved once and cached. importlib.metadata is ~20ms and
# banned on the per-render import graph, but a lazy call here (only when the
# version segment is on) is fine: it's not an import-time edge, and the daemon
# pays it once per process. Empty string if it can't be determined.
_VERSION_CACHE = None
def _statusbar_version() -> str:
    global _VERSION_CACHE
    if _VERSION_CACHE is None:
        try:
            from . import __version__
            _VERSION_CACHE = __version__
        except Exception:
            _VERSION_CACHE = ""
    return _VERSION_CACHE


def _version_gt(a: str, b: str) -> bool:
    """True if dotted version `a` is newer than `b`. Fail-safe (bad parts → 0)."""
    def parts(v):
        out = []
        for x in str(v).split("."):
            try:
                out.append(int(x))
            except ValueError:
                out.append(0)
        return out
    pa, pb = parts(a), parts(b)
    n = max(len(pa), len(pb))
    pa += [0] * (n - len(pa))
    pb += [0] * (n - len(pb))
    return pa > pb


def _update_hint(path=None) -> str:
    """The newer version string if the cached GitHub Release check says one is available
    (and the check is recent), else ''. Cheap file read — no network, no
    importlib on the hot path. Written by update_check."""
    try:
        import json as _json
        import time as _t
        from pathlib import Path as _Path
        p = _Path(path) if path is not None else (
            _Path.home() / ".cache" / "codex-statebar" / "latest_version.json")
        data = _json.loads(p.read_text(encoding="utf-8"))
        latest = str(data.get("version", ""))
        checked_at = float(data.get("checked_at", 0))
        if not latest or not 0 <= _t.time() - checked_at <= 7 * 86400:  # stale → no arrow
            return ""
        return latest if _version_gt(latest, _statusbar_version()) else ""
    except Exception:
        return ""

def _fg(rgb): return f"\033[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m"
def _bg(rgb): return f"\033[48;2;{rgb[0]};{rgb[1]};{rgb[2]}m"

# strip ANSI when use_color is False
import re as _re
_ANSI_RE = _re.compile(r"\033\[[0-9;]*m")
def _strip(s: str) -> str: return _ANSI_RE.sub("", s)


def _attr(obj, name: str, default=""):
    return getattr(obj, name, default)

# Density → padding string, shared by all renderers that support it.
DENSITY_PAD = {"compact": "", "regular": " ", "cozy": "  "}


def _severity_color(theme: Theme, pct: Optional[float],
                     warning: float, critical: float) -> tuple:
    if pct is None:
        return theme.mute
    if pct >= critical: return theme.s_hot
    if pct >= warning:  return theme.s_warn
    return theme.s_ok


def _cache_severity(theme: Theme, cache_text: str) -> tuple:
    """Map a countdown cache string to a severity color.

    "COLD"            → s_hot   (red, expired)
    "<1m" remaining   → s_warn  (yellow, ~1min left)
    otherwise         → s_ok    (green, comfortable)

    The "<1m" detection works because the countdown formatter only emits
    sub-minute remainders as bare "Ys" (no 'm', no 'h'). Anything with a
    minute or hour glyph is in the comfortable zone.
    """
    if cache_text == "COLD":
        return theme.s_hot
    # Comfortable: contains 'm' (minutes) or 'h' (hours).
    if "m" in cache_text or "h" in cache_text:
        return theme.s_ok
    return theme.s_warn


_AURORA_TOKENS_RE = _re.compile(r"\s*\([^()\n]*\)$")
_AURORA_SECS_RE = _re.compile(r"(\d+)m\d+s$")


def _aurora_cache_text(text: str) -> str:
    """Drop the seconds once they stop mattering: `1h00m00s` → `1h00m`,
    `59m12s` → `59m`. Under 10 minutes the seconds stay (`4m23s`)."""
    m = _AURORA_SECS_RE.search(text)
    if m and ("h" in text or int(m.group(1)) >= 10):
        return text[:m.end(1)] + "m"
    return text


def render_aurora(
    *, msgs_pct, weekly_pct, reset_5h, reset_7d, model,
    lang_body="", cost_text="", cache_age_text="", bypass=False,
    use_color=True, theme: Optional[Theme]=None,
    warning_threshold=30.0, critical_threshold=70.0,
    density: str = "regular",
    bar_glyph: str = "block",
    show_weekly: bool = True,
    show_five_hour: bool = True,
    ctx_pct: Optional[float] = None,
    projection_5h: str = "",
    projection_7d: str = "",
    forecast_5h: str = "",
    forecast_7d: str = "",
    countdown_emoji: str = "",
    no_quota: bool = False,
    balance_text: str = "",
    quota_stale: bool = False,
    _segments=None,
    **_ignored,
) -> str:
    from .progress import (window_severity_rgb, _blend, projection_pct,
                           _render_projection, _render_forecast,
                           CONTEXT_WARNING_THRESHOLD as _CW,
                           CONTEXT_CRITICAL_THRESHOLD as _CC)
    theme = theme or get_theme("graphite")
    INK  = _fg(theme.ink)
    MUTE = _fg(theme.mute)
    EDGE = _fg(theme.edge)
    cell = "━" if bar_glyph == "thin" else "■"

    def bar(p, width):
        # Each cell's color is fixed by its position along the bar, so a fuller
        # bar literally reaches further into the red end of the spectrum.
        if p is None:
            return f"{EDGE}{'·' * width}{RESET}"
        clamped = max(0.0, min(float(p), 100.0))
        filled = int(clamped / 100 * width + 0.5)
        if p > 0 and filled == 0:
            filled = 1
        out = ""
        for i in range(width):
            if i < filled:
                t = i / (width - 1) if width > 1 else 0.0
                rgb = (_blend(theme.s_ok, theme.s_warn, t * 2) if t < 0.5
                       else _blend(theme.s_warn, theme.s_hot, (t - 0.5) * 2))
                out += f"{_fg(rgb)}{cell}"
            else:
                out += f"{EDGE}{cell if use_color else '·'}"
        return out + RESET

    def pct_text(p, rgb):
        if p is None:
            return f"{MUTE}--%{RESET}"
        col = _fg(rgb) if rgb is not None else INK
        return f"{col}{BOLD}{int(round(p)):>2}%{RESET}"

    def window(label, p, chip, forecast, reset, emoji=""):
        rgb = window_severity_rgb(p, chip, theme,
                                  warning_threshold, critical_threshold)
        out = (f"{MUTE}{label}{RESET} {bar(p, 12)} {pct_text(p, rgb)} "
               f"{EDGE}⏰{MUTE}{reset or '--'}{emoji}{RESET}")
        if chip:
            out += " " + _render_projection(chip, theme, True)
        # Same rule as classic: a usable projection wins over the ⚠ forecast.
        if forecast and projection_pct(chip) is None:
            out += " " + _render_forecast(forecast, theme, True)
        return out

    pad = DENSITY_PAD.get(density, " ")
    sep = f"{pad}{EDGE}│{RESET}{pad}"
    parts = []

    if no_quota:
        rgb = _severity_color(theme, ctx_pct, _CW, _CC)
        parts.append(f"{MUTE}ctx{RESET} {bar(ctx_pct, 12)} {pct_text(ctx_pct, rgb)}")
    elif quota_stale and msgs_pct is None and weekly_pct is None:
        label = "5h/7d" if show_five_hour else "7d"
        parts.append(f"{_fg(theme.s_warn)}⟳ {label} stale·restart{RESET}")
    else:
        if show_five_hour:
            parts.append(window("5h", msgs_pct, projection_5h, forecast_5h,
                                reset_5h, countdown_emoji))
        if show_weekly:
            parts.append(window("7d", weekly_pct, projection_7d, forecast_7d,
                                reset_7d))

    # The mini bar carries context fill, so the `(70.0k/1.0M)` suffix is
    # redundant here; no_quota already shows ctx as the lead segment.
    # A rose between the hot and worktree hues — reads as "the model", never
    # as a severity, and follows each theme's palette.
    accent = _fg(_blend(theme.s_hot, theme.ink, 0.5))
    if ctx_pct is not None and not no_quota:
        name = _AURORA_TOKENS_RE.sub("", str(model))
        rgb = _severity_color(theme, ctx_pct, _CW, _CC)
        parts.append(f"{accent}{name}{RESET} {bar(ctx_pct, 6)} "
                     f"{_fg(rgb)}{int(round(ctx_pct))}%{RESET}")
    else:
        parts.append(f"{accent}{model}{RESET}")

    if balance_text:
        parts.append(f"{_fg(theme.s_ok)}{balance_text}{RESET}")
    if cost_text:
        parts.append(f"{MUTE}$ {INK}{cost_text}{RESET}")

    if lang_body:
        parts.append(f"{MUTE}{lang_body}{RESET}")

    if cache_age_text:
        col = _fg(_cache_severity(theme, cache_age_text))
        parts.append(f"{col}❄ {_aurora_cache_text(cache_age_text)}{RESET}")

    if bypass:
        parts.append(f"{_fg(theme.s_hot)}{BOLD}⚠ BYPASS{RESET}")

    line = sep.join(parts)
    if _segments is not None:
        _segments.extend(parts if use_color else [_strip(part) for part in parts])
    if not use_color:
        return _strip(line)
    return line


# ---------------------------------------------------------------------------
# Style: capsule
# ---------------------------------------------------------------------------
def render_capsule(
    *, msgs_pct, weekly_pct, reset_5h, reset_7d, model,
    lang_body="", cost_text="", cache_age_text="", bypass=False,
    use_color=True, theme: Optional[Theme]=None,
    warning_threshold=30.0, critical_threshold=70.0,
    density: str = "regular",
    show_weekly: bool = True,
    show_five_hour: bool = True,
    ctx_pct: Optional[float] = None,
    projection_5h: str = "",
    projection_7d: str = "",
    no_quota: bool = False,
    balance_text: str = "",
    _segments=None,
    **_ignored,
) -> str:
    from .progress import window_severity_rgb
    theme = theme or get_theme("graphite")
    INK    = _fg(theme.pill_ink)
    EDGE   = _fg(theme.edge)
    MUTE   = _fg(theme.mute)

    pad = DENSITY_PAD.get(density, " ")

    def pill(bg_rgb, body):
        return f"{_bg(bg_rgb)}{INK}{pad}{body}{pad}{RESET}"

    def sev_dot(p, chip=""):
        # 5h/7d follow the projection; ctx_pct (no chip) stays current-usage.
        rgb = window_severity_rgb(p, chip, theme,
                                  warning_threshold, critical_threshold)
        if rgb is None:
            return ""
        return f" {_fg(rgb)}●{RESET}"

    def pct_text(p):
        return "--%" if p is None else f"{int(round(p))}%"

    spacer = f"{EDGE} ╱{RESET} "

    parts = []

    if no_quota:
        # No official quota: a single CTX pill replaces the 5H/7D pills,
        # colored on context thresholds (claude-hud's 70/85), not the comfort band.
        from .progress import (CONTEXT_WARNING_THRESHOLD as _CW,
                               CONTEXT_CRITICAL_THRESHOLD as _CC)
        ctx_rgb = _severity_color(theme, ctx_pct, _CW, _CC)
        ctx_dot = f" {_fg(ctx_rgb)}●{RESET}" if ctx_pct is not None else ""
        ctx_body = (
            f"{BOLD}⛁ CTX{RESET}{INK}{_bg(theme.pill_5h)} {pct_text(ctx_pct)}"
            f"{ctx_dot}{INK}{_bg(theme.pill_5h)}"
        )
        parts.append(pill(theme.pill_5h, ctx_body))
    else:
        if show_five_hour:
            five_body = (
                f"{BOLD}◷ 5H{RESET}{INK}{_bg(theme.pill_5h)} {pct_text(msgs_pct)} "
                f"· {reset_5h}{sev_dot(msgs_pct, projection_5h)}{INK}{_bg(theme.pill_5h)}"
            )
            parts.append(pill(theme.pill_5h, five_body))
        if show_weekly:
            week_body = (
                f"{BOLD}☷ 7D{RESET}{INK}{_bg(theme.pill_7d)} {pct_text(weekly_pct)} "
                f"· {reset_7d or '--'}{sev_dot(weekly_pct, projection_7d)}{INK}{_bg(theme.pill_7d)}"
            )
            parts.append(pill(theme.pill_7d, week_body))

    # In no_quota mode the CTX pill already carries the context severity, so the
    # model pill stays neutral (no second ctx dot). In quota mode the model dot
    # reflects context fill and must use the context band (70/85), not the 5h/7d
    # comfort band — otherwise ~35% context shows a yellow dot here while the
    # no-quota CTX pill reads green for the identical 35%.
    if no_quota:
        model_sev = ""
    else:
        from .progress import (window_severity_rgb as _wsr,
                               CONTEXT_WARNING_THRESHOLD as _CW,
                               CONTEXT_CRITICAL_THRESHOLD as _CC)
        _ctx_rgb = _wsr(ctx_pct, "", theme, _CW, _CC)
        model_sev = f" {_fg(_ctx_rgb)}●{RESET}" if _ctx_rgb is not None else ""
    model_body = f"{BOLD}◆{RESET}{INK}{_bg(theme.pill_model)} {model}{model_sev}{INK}{_bg(theme.pill_model)}"
    parts.append(pill(theme.pill_model, model_body))

    if balance_text:
        parts.append(pill(theme.pill_cost, balance_text))
    if cost_text:
        parts.append(pill(theme.pill_cost, f"$ {cost_text}"))

    if lang_body:
        parts.append(pill(theme.pill_lang, f"📚 {lang_body}"))

    if cache_age_text:
        bg = _cache_severity(theme, cache_age_text)
        parts.append(pill(bg, f"cache {cache_age_text}"))

    if bypass:
        parts.append(f"{_fg(theme.s_hot)}{BOLD}⚠ BYPASS{RESET}")
    line = spacer.join(parts)
    if _segments is not None:
        _segments.extend(parts if use_color else [_strip(p) for p in parts])

    if not use_color:
        return _strip(line)
    return line


# ---------------------------------------------------------------------------
# Style: hairline
# ---------------------------------------------------------------------------
def render_hairline(
    *, msgs_pct, weekly_pct, reset_5h, reset_7d, model,
    lang_body="", cost_text="", cache_age_text="", bypass=False,
    use_color=True, theme: Optional[Theme]=None,
    warning_threshold=30.0, critical_threshold=70.0,
    density: str = "regular",
    show_weekly: bool = True,
    show_five_hour: bool = True,
    ctx_pct: Optional[float] = None,
    projection_5h: str = "",
    projection_7d: str = "",
    no_quota: bool = False,
    balance_text: str = "",
    _segments=None,
    **_ignored,
) -> str:
    from .progress import window_severity_rgb
    theme = theme or get_theme("graphite")
    INK  = _fg(theme.ink)
    MUTE = _fg(theme.mute)
    EDGE = _fg(theme.edge)

    def mini3(p, chip="", warn=None, crit=None):
        if p is None:
            return f"{MUTE}···{RESET}"
        cells = []
        for i in range(3):
            slot = (i + 1) * (100 / 3)
            if   p >= slot:                   cells.append("█")
            elif p >= slot - (100 / 3) * 0.66: cells.append("▆")
            elif p >= slot - (100 / 3):       cells.append("▃")
            else:                             cells.append("▁")
        # Cells still reflect current usage; only the color follows projection.
        rgb = window_severity_rgb(p, chip, theme,
                                  warn if warn is not None else warning_threshold,
                                  crit if crit is not None else critical_threshold)
        col = rgb if rgb is not None else theme.mute
        return f"{_fg(col)}{''.join(cells)}{RESET}"

    def pct_text(p):
        return "--%" if p is None else f"{int(round(p)):>2}%"

    sep_pad = DENSITY_PAD.get(density, " ")
    sep = f"{sep_pad}{EDGE}┊{RESET}{sep_pad}"
    parts = []

    if no_quota:
        # No official quota: a single ctx mini-bar replaces the 5h/7d segments,
        # colored on context thresholds (claude-hud's 70/85).
        from .progress import (CONTEXT_WARNING_THRESHOLD as _CW,
                               CONTEXT_CRITICAL_THRESHOLD as _CC)
        parts.append(
            f"{MUTE}› ctx{RESET} {mini3(ctx_pct, warn=_CW, crit=_CC)} "
            f"{INK}{pct_text(ctx_pct)}{RESET}"
        )
    else:
        if show_five_hour:
            parts.append(
                f"{MUTE}› 5h{RESET} {mini3(msgs_pct, projection_5h)} {INK}{pct_text(msgs_pct)}{RESET} "
                f"{MUTE}↺ {reset_5h}{RESET}"
            )
        if show_weekly:
            parts.append(
                f"{MUTE}› 7d{RESET} {mini3(weekly_pct, projection_7d)} {INK}{pct_text(weekly_pct)}{RESET} "
                f"{MUTE}↺ {reset_7d or '--'}{RESET}"
            )
    # Model line — colored by ctx_pct severity, neutral ink when absent. In
    # no_quota mode the ctx mini-bar already carries severity, so stay neutral.
    if ctx_pct is None or no_quota:
        model_color = INK
    else:
        # Context fill uses the context band (70/85), not the 5h/7d comfort
        # band — consistent with the no_quota ctx mini-bar above and the other
        # styles. ~35% context must read calm, not warning.
        from .progress import (CONTEXT_WARNING_THRESHOLD as _CW,
                               CONTEXT_CRITICAL_THRESHOLD as _CC)
        col = _severity_color(theme, ctx_pct, _CW, _CC)
        model_color = _fg(col)
    parts.append(f"{MUTE}›{RESET} {model_color}{model}{RESET}")

    if balance_text:
        parts.append(f"{_fg(theme.s_ok)}{balance_text}{RESET}")
    if cost_text:
        parts.append(f"{MUTE}$ {INK}{cost_text}{RESET}")

    if lang_body:
        parts.append(f"{MUTE}{lang_body}{RESET}")

    if cache_age_text:
        col = _fg(_cache_severity(theme, cache_age_text))
        parts.append(f"{col}cache {cache_age_text}{RESET}")

    if bypass:
        parts.append(f"{_fg(theme.s_hot)}{BOLD}⚠ BYPASS{RESET}")

    line = sep.join(parts)
    if _segments is not None:
        _segments.extend(parts if use_color else [_strip(p) for p in parts])
    if not use_color:
        return _strip(line)
    return line


# ---------------------------------------------------------------------------
# Style: classic — wraps the existing format_status_line for backward compat
# ---------------------------------------------------------------------------
def render_classic(
    *, msgs_pct, weekly_pct, reset_5h, reset_7d, model,
    lang_body="", cost_text="", cache_age_text="", bypass=False,
    use_color=True, theme: Optional[Theme]=None,
    warning_threshold=30.0, critical_threshold=70.0,
    countdown_emoji: str = "",
    ctx_pct: Optional[float] = None,
    shimmer_phase=None,
    projection_5h: str = "",
    projection_7d: str = "",
    forecast_5h: str = "",
    forecast_7d: str = "",
    no_quota: bool = False,
    balance_text: str = "",
    balance_pct=None,
    balance_amount: str = "",
    quota_stale: bool = False,
    _segments=None,
    **_ignored,
) -> str:
    from .progress import format_status_line, _fg, colorize, RESET
    theme = theme or get_theme("graphite")
    lang_text = (
        colorize(f"📚 {lang_body}", _fg(theme.s_ok), use_color)
        if lang_body else ""
    )
    result = format_status_line(
        msgs_pct=msgs_pct, tkns_pct=None,
        reset_time=reset_5h, model=model,
        weekly_pct=weekly_pct, reset_time_7d=reset_7d or "",
        ctx_pct=ctx_pct,
        bypass=bypass, use_color=use_color,
        countdown_emoji=countdown_emoji,
        warning_threshold=warning_threshold,
        critical_threshold=critical_threshold,
        lang_text=lang_text,
        cost_text=cost_text,
        theme=theme,
        shimmer_phase=shimmer_phase,
        projection_5h=projection_5h,
        projection_7d=projection_7d,
        forecast_5h=forecast_5h,
        forecast_7d=forecast_7d,
        no_quota=no_quota,
        balance_text=balance_text,
        balance_pct=balance_pct,
        balance_amount=balance_amount,
        quota_stale=quota_stale,
        _segments=_segments,
        show_weekly=_ignored.get("show_weekly", True),
        show_five_hour=_ignored.get("show_five_hour", True),
    )
    if cache_age_text:
        # Three-level severity: COLD red, <1m yellow, otherwise green.
        if cache_age_text == "COLD":
            col = _fg(theme.s_hot)
        elif "m" in cache_age_text or "h" in cache_age_text:
            col = _fg(theme.s_ok)
        else:
            col = _fg(theme.s_warn)
        mute = _fg(theme.mute)
        reset = RESET if use_color else ""  # don't leak a bare RESET in no-color mode
        segment = colorize(f'cache {cache_age_text}', col, use_color)
        result += f"{reset}{colorize(' | ', mute, use_color)}{segment}"
        if _segments is not None:
            _segments.append(segment)
    return result


def _ahead_behind_glyphs(ahead, behind) -> str:
    """`↑2↓1` / `↑3` / `↓1` / "" — only the nonzero directions."""
    out = ""
    if ahead:
        out += f"↑{ahead}"
    if behind:
        out += f"↓{behind}"
    return out


def _stats_segment(duration_text: str, lines_text: str, *, theme: Theme,
                   use_color: bool) -> str:
    """The ` · ⏱ <dur> · +added -removed` tail appended to the identity line.

    Returns "" when neither is present. Diff colors: +added green, -removed red.
    """
    if not (duration_text or lines_text):
        return ""
    # Lines (productivity) first, then the weaker duration signal.
    if not use_color:
        parts = []
        if lines_text:
            parts.append(lines_text)
        if duration_text:
            parts.append(f"⏱ {duration_text}")
        return " · " + " · ".join(parts)
    MUTE = _fg(theme.mute)
    INK  = _fg(theme.ink)
    OK   = _fg(theme.s_ok)
    HOT  = _fg(theme.s_hot)
    segs = []
    if lines_text:
        toks = []
        for tok in lines_text.split():
            c = OK if tok.startswith("+") else HOT if tok.startswith("-") else MUTE
            toks.append(f"{c}{tok}{RESET}")
        segs.append(" ".join(toks))
    if duration_text:
        segs.append(f"{MUTE}⏱{RESET} {INK}{duration_text}{RESET}")
    sep = f" {MUTE}·{RESET} "
    return f" {MUTE}·{RESET} " + sep.join(segs)


def _worktree_marker(info, glyph):
    count = getattr(info, "worktree_count", 0)
    if info.is_worktree:
        from .identity import strip_repo_prefix
        name = strip_repo_prefix(info.worktree_name or "worktree", info.project_name)
        if name == info.branch:
            name = "worktree"
        return f"{name} ({glyph}{count})"
    return f"worktree ({glyph}{count})" if info.in_git else ""


def _aurora_quiet(theme: Theme):
    """Palette for aurora's lines below the first: line 1 carries the color,
    these recede — grey text, with severity hues sunk halfway into mute."""
    from .progress import _blend
    return dict(
        BASE=_fg(_blend(theme.pill_ink, theme.mute, 0.5)),
        DIM=_fg(theme.mute),
        EDGE=_fg(theme.edge),
        soft=lambda rgb: _fg(_blend(rgb, theme.mute, 0.45)),
    )


# Lines below aurora's first use a faint dot, not `│`: stacked `│`s at
# unrelated offsets read as a misaligned table.
_QUIET_SEP = "  ·  "


def render_identity_line_aurora(info, *, theme: Theme, dirty,
                                ahead=None, behind=None,
                                duration_text: str = "", lines_text: str = "",
                                version_text: str = "", update_text: str = "",
                                cwd_text: str = "",
                                worktree_glyph: str = "🌳",
                                lang: str = "en",
                                use_color: bool = True) -> str:
    """Aurora's 2nd line: `⎇ main ●↓1  ·  project  ·  +244 −3  ·  ⏱ 4m`, muted.

    Same facts as `render_identity_line`, regrouped behind `│` separators with
    the branch leading. A linked worktree still leads the whole line; the trunk
    marker appears only when other worktrees exist, the version only when an
    update is waiting.
    """
    main_label = "worktree"  # same word in zh and en (leo, 2026-10-22)
    q = _aurora_quiet(theme)
    BASE, DIM, EDGE, soft = q["BASE"], q["DIM"], q["EDGE"], q["soft"]
    from ._display import hyperlink
    from .identity import branch_web_url
    repo_url = getattr(info, "web_url", None)

    def c(color, text):
        return f"{color}{text}{RESET}" if use_color else text

    def link(text, url):
        # Cmd/Ctrl+click opens it; plain-text output stays link-free.
        return hyperlink(text, url) if use_color else text

    segs = []
    if info.is_worktree:
        segs.append(c(soft(theme.wt), _worktree_marker(info, worktree_glyph)))
    if info.in_git:
        branch = info.branch or "?"
        head = c(DIM, "⎇ ") + link(c(f"{DIM}{ITAL}" if info.detached else BASE, branch),
                                   None if info.detached else branch_web_url(repo_url, branch))
        flags = ("●" if dirty else "") + _ahead_behind_glyphs(ahead, behind)
        if flags:
            head += " " + c(soft(theme.s_warn), flags)
        segs.append(head)
    segs.append(link(c(BASE, info.project_name), repo_url))
    if not info.in_git:
        segs.append(c(f"{DIM}{ITAL}", "no git"))
    if cwd_text and cwd_text != info.project_name:
        segs.append(c(BASE, cwd_text))
    if lines_text:
        toks = []
        for tok in lines_text.split():
            if tok.startswith("+"):
                toks.append(c(soft(theme.s_ok), tok))
            elif tok.startswith("-"):
                toks.append(c(soft(theme.s_hot), "−" + tok[1:]))
            else:
                toks.append(c(DIM, tok))
        segs.append(" ".join(toks))
    if duration_text:
        segs.append(c(DIM, f"⏱ {duration_text}"))
    if info.in_git and not info.is_worktree:
        segs.append(c(DIM, f"{main_label} ({worktree_glyph}{info.worktree_count})"))
    if version_text and update_text:
        notes = ("https://github.com/leeguooooo/codex-statebar/releases/tag/"
                 f"v{update_text}")
        segs.append(c(DIM, f"v{version_text}")
                    + link(c(soft(theme.s_warn), f" ↑{update_text}"), notes))
    return c(EDGE, _QUIET_SEP).join(segs)


def render_ocs_line_aurora(ocs_text: str, unread, lan, *, theme: Theme,
                           use_color: bool = True, lang: str = "en") -> str:
    """`ocs claude-b2242a10  ·  3 unread  ·  lan ● win  ● mac`, muted like line 2.
    Unread is a word, not ✉ — the envelope renders as a mismatched emoji."""
    q = _aurora_quiet(theme)
    BASE, DIM, EDGE, soft = q["BASE"], q["DIM"], q["EDGE"], q["soft"]

    def c(color, text):
        return f"{color}{text}{RESET}" if use_color else text

    name, sep, ident = ocs_text.partition(" · ")
    segs = [c(DIM, "ocs ") + c(BASE, name) + (c(DIM, f" {ident}") if sep else "")]
    if unread:
        segs.append(c(soft(theme.s_warn),
                      f"{unread} 条未读" if lang == "zh" else f"{unread} unread"))
    if lan:
        if not lan.get("running"):
            segs.append(c(DIM, "lan ") + c(soft(theme.s_warn), "off"))
        else:
            peers = [c(soft(theme.s_ok) if recent else EDGE, "●") + " "
                     + c(BASE if recent else DIM, label)
                     for label, recent in (lan.get("peers") or [])]
            body = "  ".join(peers) if peers else c(DIM, "on")
            if lan.get("more"):
                body += c(DIM, f" +{lan['more']}")
            segs.append(c(DIM, "lan ") + body)
    return c(EDGE, _QUIET_SEP).join(segs)


def render_reset_segment(reset_text: str, *, theme: Theme, quiet: bool = False,
                         use_color: bool = True) -> str:
    """`↻ 官方重置 8小时前`: labels dim, values base (aurora) or all muted."""
    body = reset_text[2:] if reset_text.startswith("↻ ") else reset_text
    pieces = body.split(" · ")
    if not use_color:
        return "↻ " + " ".join(pieces)
    q = _aurora_quiet(theme)
    BASE, DIM, EDGE = q["BASE"], q["DIM"], q["EDGE"]
    # Templates alternate `label · value`, so odd pieces are the values.
    segs = [f"{BASE if quiet and i % 2 else DIM}{piece}{RESET}"
            for i, piece in enumerate(pieces)]
    return f"{EDGE}↻{RESET} " + " ".join(segs)


def render_identity_line(info, *, theme: Theme, dirty,
                         ahead=None, behind=None,
                         duration_text: str = "", lines_text: str = "",
                         version_text: str = "", update_text: str = "",
                         cwd_text: str = "",
                         worktree_glyph: str = "🌳",
                         use_color: bool = True) -> str:
    """Render the 2nd line: `⤷ <project> ⎇ <branch>●↑2↓1 · ⏱ <dur> · +/-lines`.

    `dirty` is True / False / None — None means "unknown" (cache miss);
    in that case we omit the dot rather than asserting clean. `ahead`/`behind`
    are commits relative to upstream (None = unknown/no upstream, 0 = in sync);
    arrows render only for nonzero directions and only inside a git repo.
    `duration_text`/`lines_text` are the session stats, shown here (next to the
    project) rather than on the live-activity line. When the checkout is a
    linked git worktree (`info.is_worktree`), its name and live checkout count
    lead the identity line. The main checkout also shows the sibling count.

    `cwd_text` (show_cwd, #30) is the session's working directory; it's
    appended after the branch only when it adds information — skipped when it
    equals the project name (cwd at the repo root would just repeat the anchor).
    """
    ab = _ahead_behind_glyphs(ahead, behind) if info.in_git else ""
    stats = _stats_segment(duration_text, lines_text, theme=theme,
                           use_color=use_color)

    if not use_color:
        head = f"⤷ {info.project_name}"
        if not info.in_git:
            tail = " (no git)"
        else:
            branch = info.branch or "?"
            dot = "●" if dirty else ""
            tail = f" ⎇ {branch}{dot}"
            if ab:
                tail += f" {ab}"
        marker = _worktree_marker(info, worktree_glyph)
        if marker:
            head = marker + " " + head
        if cwd_text and cwd_text != info.project_name:
            tail += f" · {cwd_text}"
        ver = f" · v{version_text}" if version_text else ""
        if version_text and update_text:
            ver += f" ↑{update_text}"
        return head + tail + stats + ver

    MUTE = _fg(theme.mute)
    EDGE = _fg(theme.edge)
    INK = _fg(theme.pill_ink)
    HOT = _fg(theme.s_warn)

    # Project name in full ink — it's the line's identity anchor (user
    # feedback 2026-07-02: mute made it near-invisible). The ⤷ stays mute.
    from ._display import hyperlink
    from .identity import branch_web_url
    project = hyperlink(info.project_name, getattr(info, "web_url", None))
    head = f"{MUTE}⤷ {RESET}{INK}{project}{RESET}"
    if not info.in_git:
        body = f" {MUTE}{ITAL}(no git){RESET}"
    else:
        branch = info.branch or "?"
        if info.detached:
            branch_styled = f"{MUTE}{ITAL}{branch}{RESET}"
        else:
            branch_link = hyperlink(branch, branch_web_url(getattr(info, "web_url", None), branch))
            branch_styled = f"{INK}{branch_link}{RESET}"
        dot = f"{HOT}●{RESET}" if dirty else ""
        body = f" {EDGE}⎇{RESET} {branch_styled}{dot}"
        if ab:
            # Soft accent (not bare mute) — a gentle "unpushed/behind work" nudge.
            body += f" {_fg(theme.s_ok)}{ab}{RESET}"
    marker = _worktree_marker(info, worktree_glyph)
    if marker:
        color = _fg(theme.wt) if info.is_worktree else MUTE
        head = f"{color}{marker}{RESET} " + head
    if cwd_text and cwd_text != info.project_name:
        body += f" {MUTE}·{RESET} {INK}{cwd_text}{RESET}"
    # Version: the faintest thing on the line — edge (darkest grey) + dim
    # attribute, so it's there if you look for it but never competes for attention.
    ver = ""
    if version_text:
        ver = f" {FAINT}{EDGE}· v{version_text}{RESET}"
        # Update available → a soft amber `↑<newver>` nudge (a bit more visible
        # than the version, so you notice there's something to update to).
        if update_text:
            ver += f"{_fg(theme.s_warn)} ↑{update_text}{RESET}"
    return head + body + stats + ver


def render_activity_line(activity, *, theme: Theme, use_color: bool = True,
                         show_todos: bool = False, show_tools: bool = False,
                         show_tool_rollup: bool = False) -> str:
    """Render the optional 'activity' line — the in-turn signals only: the
    in-progress todo and the active tool + completed-tool rollup. Returns ""
    when nothing is enabled or present.

    Session stats (duration, lines) live on the identity line; subagents get
    their own bottom line(s) via ``render_agent_lines``.

    Style-agnostic (like ``render_identity_line``): the same line renders
    under classic / capsule / hairline so the curated main line is never
    disturbed.
    """
    MUTE = _fg(theme.mute)
    INK  = _fg(theme.ink)
    OK   = _fg(theme.s_ok)
    WARN = _fg(theme.s_warn)

    segs = []

    if show_todos and activity is not None and activity.todos:
        done, total = activity.todos_done, activity.todos_total
        ip = activity.in_progress_todo
        if ip:
            task = ip if len(ip) <= 28 else ip[:27] + "…"
            segs.append(f"{OK}▸{RESET} {INK}{task}{RESET} {MUTE}({done}/{total}){RESET}")
        else:
            segs.append(f"{OK}▸{RESET} {MUTE}todos {done}/{total}{RESET}")

    if show_tools and activity is not None and activity.active_tool:
        name, target = activity.active_tool
        tail = f" {MUTE}{target}{RESET}" if target else ""
        segs.append(f"{WARN}◐{RESET} {INK}{name}{RESET}{tail}")

    if show_tool_rollup and activity is not None and activity.completed_counts:
        # Tool name brightened (ink), ×count muted — scannable hierarchy.
        roll = " ".join(f"{INK}{n}{RESET}{MUTE}×{c}{RESET}"
                        for n, c in activity.completed_counts[:3])
        segs.append(f"{OK}✓{RESET} {roll}")

    if not segs:
        return ""
    line = f" {MUTE}·{RESET} ".join(segs)
    if not use_color:
        return _strip(line)
    return line


def _clip_width(s: str, limit: int) -> str:
    """Truncate `s` to `limit` display columns, appending … when cut.

    Wide East-Asian glyphs count as two columns, so a CJK preview clips at the
    same visual width as an ASCII one.
    """
    from unicodedata import east_asian_width

    def w(ch):
        return 2 if east_asian_width(ch) in ("W", "F") else 1

    if sum(w(ch) for ch in s) <= limit:
        return s
    out, used = [], 0
    for ch in s:
        if used + w(ch) > limit - 1:
            break
        out.append(ch)
        used += w(ch)
    return "".join(out) + "…"


def render_party_line(party, *, theme: Theme, use_color: bool = True) -> str:
    """Render the AgentParty block: a header line plus the last message.

    Header  ``#seamail · ⬡ leo-zego-voice · ◉ watching @mentions``
    Message ``   ↳ ●@ Jarvis  <preview>  28m``

    Glyphs are monochrome geometry, not emoji: they inherit the theme color (so
    the listener state reads as a signal lamp — ``◉`` live, ``⊘`` down, ``◌``
    unattached) and stay single-width. ``⬡`` is an agent, ``⬢`` a human.

    The message gets its own line so a long preview can't push the header off
    screen. Its two leading glyphs are the message's state: ``●`` unread /
    ``○`` read, then ``@`` when the preview mentions us (a space otherwise, so
    the sender column stays put).
    """
    if party is None:
        return ""
    channel = str(_attr(party, "channel", "") or "").strip()
    identity = str(_attr(party, "identity_name", "") or "").strip()
    kind = str(_attr(party, "identity_kind", "agent") or "agent").strip()
    unread = int(_attr(party, "unread", 0) or 0)
    listener_mode = str(_attr(party, "listener_mode", "") or "").strip()
    last_from = str(_attr(party, "last_from", "") or "").strip()
    last_preview = str(_attr(party, "last_preview", "") or "").strip()
    last_age = str(_attr(party, "last_age", "") or "").strip()
    fresh = bool(_attr(party, "fresh", True))
    listener_stale = bool(_attr(party, "listener_stale", False))
    listener_present = bool(_attr(party, "listener_present", bool(listener_mode)))
    mentions_only = bool(_attr(party, "listener_mentions_only", False))
    mentioned = bool(_attr(party, "mentioned", False))

    if not any((channel, identity, unread, listener_mode, last_preview)):
        return ""

    INK  = _fg(theme.ink)
    MUTE = _fg(theme.mute)
    EDGE = _fg(theme.edge)
    OK   = _fg(theme.s_ok)
    WARN = _fg(theme.s_warn)
    HOT  = _fg(theme.s_hot)
    SEP  = f"{EDGE} · {RESET}"

    # ---- header ---------------------------------------------------------
    # The `#` is ours; a channel that already carries one must not render `##`.
    chan = (channel.lstrip("#") or "AgentParty") if channel else "AgentParty"
    head = [f"{EDGE}#{RESET}{INK}{chan}{RESET}"]
    if identity:
        icon = "⬢" if kind == "human" else "⬡"
        head.append(f"{MUTE}{icon} {identity}{RESET}")

    # Listening state is the question the header must answer outright: are we
    # attached and hearing messages, or not? The glyph alone carries it.
    if not listener_present:
        head.append(f"{MUTE}◌ not listening{RESET}")
    elif listener_stale:
        head.append(f"{HOT}⊘ listener down{RESET}")
    else:
        verb = "serving" if listener_mode == "serve" else "watching"
        scope = f" {MUTE}@mentions{RESET}" if mentions_only else ""
        head.append(f"{OK}◉ {verb}{RESET}{scope}")

    if unread > 0:
        head.append(f"{WARN}{unread} unread{RESET}")
    if not fresh:
        head.append(f"{HOT}stale{RESET}")

    lines = [SEP.join(head)]

    # ---- last message, on its own line ----------------------------------
    if last_preview:
        hot_msg = unread > 0 or mentioned
        dot = f"{WARN}●{RESET}" if unread > 0 else f"{EDGE}○{RESET}"
        at = f"{WARN}@{RESET}" if mentioned else " "
        who = f"{INK}{last_from}{RESET}" if hot_msg else f"{MUTE}{last_from}{RESET}"
        who = f"{who}  " if last_from else ""
        body = f"{MUTE}{_clip_width(last_preview, 54)}{RESET}"
        age = f" {EDGE}{last_age}{RESET}" if last_age else ""
        lines.append(f"   {EDGE}↳{RESET} {dot}{at} {who}{body}{age}")

    out = "\n".join(lines)
    return out if use_color else _strip(out)


def render_agent_lines(agents, *, theme: Theme, use_color: bool = True) -> list:
    """One line per running subagent: `◐ <name>[<model>] <description> <elapsed>`.

    Each agent gets its own bottom line (multiple agents → multiple lines), so a
    long task description doesn't crowd the activity line. Returns [] when empty.
    """
    from .activity import format_elapsed_short

    if not agents:
        return []
    MUTE = _fg(theme.mute)
    INK  = _fg(theme.ink)
    WARN = _fg(theme.s_warn)

    lines = []
    for ag in agents:
        model = ag.get("model") or ""
        badge = f"{MUTE}[{model}]{RESET}" if model else ""
        desc = str(ag.get("description") or "").strip()
        if len(desc) > 40:  # roomier than the activity line — it's a line of its own
            desc = desc[:39] + "…"
        desc_part = f" {MUTE}{desc}{RESET}" if desc else ""
        el = format_elapsed_short(ag.get("elapsed_seconds", 0))
        line = (f"{WARN}◐{RESET} {INK}{ag.get('name', 'agent')}{RESET}{badge}"
                f"{desc_part} {MUTE}{el}{RESET}")
        lines.append(_strip(line) if not use_color else line)
    return lines


# Per-effort gradient palettes — a MONOTONIC grey→blue→purple ladder matching
# Claude Code's own "Faster → Smarter" effort slider (low … max, then ultracode
# = xhigh+workflows as the distinct vivid-purple top). Each tier is visibly more
# saturated/purple than the one below, so the level reads as an ordered ladder
# (not the old rainbow, where coral `max` looked hotter than `ultracode`).
_EFFORT_GRADIENTS = {
    # DESATURATED cool→purple ladder — each tier sweeps toward the next hue so it
    # reads as a gradient, but the colours are dusty/low-saturation so the line
    # doesn't shout next to the rest of the (restrained) bar. ultracode is a touch
    # brighter as the top tier.
    "low":       [(120, 172, 168), (124, 158, 196)],             # dusty teal → blue
    "auto":      [(120, 172, 168), (124, 158, 196)],             # neutral, like low
    "medium":    [(122, 156, 198), (132, 146, 202)],             # dusty azure
    "high":      [(130, 144, 204), (156, 140, 202)],             # dusty blue → indigo
    "xhigh":     [(152, 138, 204), (176, 134, 200)],             # dusty indigo → violet
    "max":       [(176, 134, 200), (196, 138, 196)],             # dusty violet → mauve
    "ultracode": [(200, 138, 202), (220, 160, 208)],             # dusty magenta → pink (top)
}
# Fallback for unknown/future levels — the showcase vivid purple.
_MODE_GRADIENT_STOPS = _EFFORT_GRADIENTS["ultracode"]


def _effort_gradient_stops(level):
    return _EFFORT_GRADIENTS.get(str(level).strip().lower(), _MODE_GRADIENT_STOPS)


def _lerp_rgb(a, b, f):
    return tuple(int(round(a[i] + (b[i] - a[i]) * f)) for i in range(3))


def _grad_sample(stops, f):
    """Sample a NON-cyclic gradient at f∈[0,1] across `stops` (clamped ends)."""
    if f <= 0:
        return stops[0]
    if f >= 1:
        return stops[-1]
    x = f * (len(stops) - 1)
    i = int(x)
    return _lerp_rgb(stops[i], stops[i + 1], x - i)


def _gradient_text(text: str, stops=None) -> str:
    """A single STATIC gradient (palette `stops`) swept once across `text`, left
    to right. Not animated: the statusLine refreshes at ≤1 Hz (and event-driven
    in some builds), so any motion can only step ~1/s and reads as a flicker —
    a clean stable sweep is the right call. The per-effort palette tells the tier."""
    stops = stops or _MODE_GRADIENT_STOPS
    n = len(text)
    out = [
        _fg(_grad_sample(stops, i / max(1, n - 1))) + ch
        for i, ch in enumerate(text)
    ]
    return "".join(out) + RESET


def _effort_display(level) -> str:
    """Display string for the effort value. `ultracode` spells out what it means
    (Claude Code: `ultracode = xhigh + workflows`); everything else verbatim."""
    if str(level).strip().lower() == "ultracode":
        return f"{level}(+workflows)"
    return str(level)


def _effort_color(level, theme):
    """Colour the effort value by intensity tier (not severity): top tiers get a
    soft amber 'cranked up' nudge, low/auto recede, the rest stay neutral. Values
    are Claude Code's: low / medium / high / xhigh / max / ultracode / auto."""
    lv = str(level).strip().lower()
    if lv in ("xhigh", "max", "ultracode"):
        return _fg(theme.s_warn)
    if lv in ("low", "auto", ""):
        return _fg(theme.mute)
    return _fg(theme.ink)   # medium / high / unknown future values


def render_mode_line(*, effort: str = "", thinking=None, fast=None,
                     style: str = "", theme: Theme, use_color: bool = True,
                     gradient: bool = True) -> str:
    """Session-mode readout: `⚙ effort:high · think:on · fast:on · style:default`.

    Each field is dropped when absent, so an older Claude Code that omits one
    just shows fewer segments; returns '' when nothing is known. The effort value
    is shown verbatim (handles any of low/medium/high/xhigh/max/ultracode/auto and
    future values) and tinted by intensity. When `gradient` is on (default), the
    WHOLE line gets a static gradient whose palette depends on the effort tier
    (cool→hot: slate/blue/cyan/amber/coral/pink-purple), so the tier is obvious at
    a glance. Static, not animated — the statusLine can't repaint faster than ~1 Hz,
    so motion only flickers; a stable per-tier sweep is the clean result."""
    segs = []  # (label, value, value_color)
    if effort:
        segs.append(("effort:", _effort_display(effort), _effort_color(effort, theme)))
    if thinking is not None:
        segs.append(("think:", "on" if thinking else "off", _fg(theme.ink)))
    if fast is not None:
        segs.append(("fast:", "on" if fast else "off", _fg(theme.ink)))
    if style:
        segs.append(("style:", str(style), _fg(theme.ink)))
    if not segs:
        return ""
    plain = "⚙ " + " · ".join(f"{l}{v}" for l, v, _ in segs)
    if not use_color:
        return plain
    if gradient:
        return _gradient_text(plain, _effort_gradient_stops(effort))
    MUTE = _fg(theme.mute)
    body = f"{MUTE} · {RESET}".join(
        f"{MUTE}{l}{RESET}{c}{v}{RESET}" for l, v, c in segs)
    return f"{MUTE}⚙{RESET} " + body


RENDERERS = {
    "aurora": render_aurora,
    "classic":  render_classic,
    "capsule":  render_capsule,
    "hairline": render_hairline,
}


def is_known_style(style: str) -> bool:
    return style in RENDERERS


def render(style: str, **kwargs) -> str:
    """Render with the named style. Unknown style names fall back to classic.

    Unknown kwargs are absorbed by each renderer's **_ignored, so callers can
    freely pass style-specific args (density, countdown_emoji, ...) to whichever
    renderer is selected.

    The optional `show_project_branch`/`identity`/`identity_dirty` kwargs
    cause a second `⤷ <project> ⎇ <branch>` line to be appended after the
    style renderer returns. The optional `activity`/`activity_opts` kwargs
    append an 'activity' line (todos / active tool / session stats) plus one
    bottom line per running subagent. All extra lines are style-agnostic.
    """
    max_width = kwargs.pop("max_width", None)
    narrow_layout = kwargs.pop("narrow_layout", "stack")
    ui_lang = kwargs.pop("ui_lang", "en")
    reset_text = kwargs.pop("reset_text", "")
    ocs_text = kwargs.pop("ocs_text", "")
    ocs_unread = kwargs.pop("ocs_unread", None)
    ocs_lan = kwargs.pop("ocs_lan", None)
    looks_hint = kwargs.pop("looks_hint", False)
    worktree_glyph = kwargs.pop("worktree_glyph", "🌳")
    show_pb = kwargs.pop("show_project_branch", False)
    info = kwargs.pop("identity", None)
    dirty = kwargs.pop("identity_dirty", None)
    ahead = kwargs.pop("identity_ahead", None)
    behind = kwargs.pop("identity_behind", None)
    duration_text = kwargs.pop("identity_duration", "")
    lines_text = kwargs.pop("identity_lines", "")
    cwd_text = kwargs.pop("cwd_text", "")
    ip_line_text = kwargs.pop("ip_line_text", "")
    ip_line_level = kwargs.pop("ip_line_level", "ok")
    fp_line_text = kwargs.pop("fp_line_text", "")
    fp_line_level = kwargs.pop("fp_line_level", "ok")
    show_version = kwargs.pop("identity_show_version", False)
    show_mode = kwargs.pop("mode_show", False)
    mode_effort = kwargs.pop("mode_effort", "")
    mode_thinking = kwargs.pop("mode_thinking", None)
    mode_fast = kwargs.pop("mode_fast", None)
    mode_style = kwargs.pop("mode_style", "")
    mode_gradient = kwargs.pop("mode_gradient", True)
    kwargs.pop("mode_phase", None)   # accepted for back-compat; gradient is static
    activity = kwargs.pop("activity", None)
    activity_opts = kwargs.pop("activity_opts", None)
    party = kwargs.pop("party", None)
    theme = kwargs.get("theme") or get_theme("graphite")
    use_color = kwargs.get("use_color", True)

    fn = RENDERERS.get(style, render_classic)
    from ._display import clip_lines, visible_width
    parts = []
    out = fn(**kwargs, _segments=parts)
    if reset_text and "\n" not in out:
        # A trailing quota segment, not a new line: the bar stays as tall as before.
        seg = render_reset_segment(reset_text, theme=theme, quiet=style == "aurora",
                                   use_color=use_color)
        pad = DENSITY_PAD.get(kwargs.get("density", "regular"), " ")
        sep = " | " if fn is render_classic else (
            f"{pad}{_fg(theme.edge)}│{RESET}{pad}" if style == "aurora" and use_color
            else f"{pad}│{pad}" if style == "aurora" else " │ ")
        out += sep + seg
        parts.append(seg)
    if max_width is not None and max_width > 0 and visible_width(out) > max_width:
        # Pack whole segments; only an individually oversized segment is clipped.
        sep = " | " if fn is render_classic else " │ "
        if narrow_layout == "drop":
            while len(parts) > 1 and visible_width(sep.join(parts)) > max_width:
                parts.pop()
            out = sep.join(parts) if parts else out
        rows, row = [], ""
        for part in parts or [out]:
            if row and visible_width(row + sep + part) > max_width:
                rows.append(row)
                row = part
            else:
                row = row + sep + part if row else part
        if row:
            rows.append(row)
        out = "\n".join(rows)

    if show_pb and info is not None:
        version_text = _statusbar_version() if show_version else ""
        update_text = _update_hint() if show_version else ""
        identity_fn = render_identity_line_aurora if style == "aurora" else render_identity_line
        out = out + "\n" + identity_fn(
            info, theme=theme, dirty=dirty, ahead=ahead, behind=behind,
            duration_text=duration_text, lines_text=lines_text,
            version_text=version_text, update_text=update_text,
            cwd_text=cwd_text, worktree_glyph=worktree_glyph,
            use_color=use_color,
        )
    elif cwd_text:
        # show_cwd is on but the identity line is off — give the directory its
        # own minimal line, styled like the identity anchor.
        if use_color:
            out = (out + "\n" + f"{_fg(theme.mute)}⤷ {RESET}"
                   f"{_fg(theme.pill_ink)}{cwd_text}{RESET}")
        else:
            out = out + "\n" + f"⤷ {cwd_text}"

    if looks_hint:
        from .picker import hint_text
        out += "\n" + hint_text(ui_lang)
    if ocs_text and style == "aurora":
        out += "\n" + render_ocs_line_aurora(ocs_text, ocs_unread, ocs_lan,
                                              theme=theme, use_color=use_color, lang=ui_lang)
    elif ocs_text:
        line = f"ocs {ocs_text}"
        if ocs_unread:
            line += f" · ✉{ocs_unread}"
        if ocs_lan:
            peers = ocs_lan.get("peers") or []
            labels = "  ".join(("● " if recent else "○ ") + label for label, recent in peers)
            if ocs_lan.get("more"):
                labels += f" +{ocs_lan['more']}"
            line += " · lan " + (labels or ("on" if ocs_lan.get("running") else "off"))
        out += "\n" + (f"{_fg(theme.ink)}{line}{RESET}" if use_color else line)
    party_line = render_party_line(party, theme=theme, use_color=use_color)
    if party_line:
        out = out + "\n" + party_line

    # Dedicated egress-IP risk warning — appears only above the risk threshold
    # (ip_risk.SHOW_THRESHOLD), amber for suspicious, red for bad. May be
    # multi-line (summary + action); each sub-line is colored separately so a
    # long warning wraps cleanly instead of being truncated. Not part of the
    # git identity line: network state isn't repo identity.
    def _emit_risk(text, level):
        nonlocal out
        if not text:
            return
        color = theme.s_hot if level == "crit" else theme.s_warn
        for seg in text.split("\n"):
            out = out + "\n" + (f"{_fg(color)}{seg}{RESET}" if use_color else seg)

    _emit_risk(ip_line_text, ip_line_level)
    # Relay fingerprint-risk warning line (local env inference; see fp_risk.py).
    _emit_risk(fp_line_text, fp_line_level)

    if show_mode:
        mode_line = render_mode_line(
            effort=mode_effort, thinking=mode_thinking, fast=mode_fast,
            style=mode_style, theme=theme, use_color=use_color,
            gradient=mode_gradient)
        if mode_line:
            out = out + "\n" + mode_line

    if activity_opts:
        opts = dict(activity_opts)
        show_agents = opts.pop("show_agents", False)
        act_line = render_activity_line(
            activity, theme=theme, use_color=use_color, **opts)
        if act_line:
            out = out + "\n" + act_line
        # Subagents get their own bottom line(s), one per running agent.
        if show_agents and activity is not None and activity.agents:
            for agline in render_agent_lines(
                    activity.agents, theme=theme, use_color=use_color):
                out = out + "\n" + agline
    return clip_lines(out, max_width)


def list_styles() -> list[str]:
    return list(RENDERERS.keys())

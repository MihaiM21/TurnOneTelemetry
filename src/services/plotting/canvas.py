"""
Social-format canvases for plots.

A chart that is posted to X, Instagram, TikTok or a Short is *recomposed* for
each aspect ratio, not cropped: a 16:9 layout squeezed into 9:16 puts the data
under the platform's buttons and shrinks every label below readability. This
module owns the parts every such layout shares so a feature only decides where
its panels go:

* :data:`FORMATS` -- target pixel sizes, the safe area each platform leaves
  clear of its own UI, and the base font size that keeps text readable on the
  device the format is watched on.
* :func:`new_canvas` / :func:`safe_gridspec` -- a figure at exactly the target
  pixel size and a GridSpec confined to the safe area (below a header band,
  above a footer band).
* :func:`add_header`, :func:`add_footer`, :func:`add_watermark`,
  :func:`add_driver_plate` -- the chrome, identical across features.
* :func:`fit_to_safe_width` -- run by :func:`save_png`: stretches and centres the
  content horizontally so what is actually drawn (tick labels included) spans the
  safe area edge to edge, whatever padding a layout reserved.
* :func:`save_png` -- writes at the format's dpi so the file is the target size.

Only features that accept a ``format`` argument use this. A legacy render (no
``format``) keeps its own figure so the website's images do not change; those
only share :func:`add_legacy_watermark`.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional, Tuple

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.figure import Figure
from matplotlib.gridspec import GridSpec

from src.services.plotting import theme

_ASSETS = Path(__file__).resolve().parents[3] / "assets"
_LOGO_MARK = _ASSETS / "images" / "logo mic.png"
_LOGO_FULL = _ASSETS / "images" / "logo.png"
_DRIVER_DIR = _ASSETS / "drivers"

# Every canvas is laid out at this dpi; the saved file is width_px x height_px.
DESIGN_DPI = 150

TEXT = "#E9E9E9"
MUTED = "#8A8A8A"
FACE = "#0d0d0d"

DEFAULT_SOURCE = "Data: F1 live timing"
DEFAULT_HANDLE = "turnonehub.com"


@dataclass(frozen=True)
class CanvasFormat:
    """One output format.

    ``safe`` is ``(left, bottom, right, top)`` in figure fractions: the region
    that stays clear of platform UI (the TikTok/Reels/Shorts action rail on the
    right and caption block at the bottom, mostly). Content outside it may be
    covered. ``base_fontsize`` is in points at :data:`DESIGN_DPI`.
    """

    name: str
    width_px: int
    height_px: int
    safe: Tuple[float, float, float, float]
    base_fontsize: float

    @property
    def figsize(self) -> Tuple[float, float]:
        return self.width_px / DESIGN_DPI, self.height_px / DESIGN_DPI

    @property
    def is_vertical(self) -> bool:
        return self.height_px > self.width_px

    @property
    def aspect(self) -> float:
        return self.width_px / self.height_px

    def px_to_pt(self, px: float) -> float:
        """Convert a pixel height at the target size to points at DESIGN_DPI."""
        return px * 72.0 / DESIGN_DPI


# Minimum readable text: 26 px on a 1080p landscape frame, 34 px on 9:16
# (the phone is further from the eye than the screen is). base_fontsize is the
# *body* size; tick labels use ~0.85x of it and must still clear the minimum.
FORMATS: Dict[str, CanvasFormat] = {
    "landscape": CanvasFormat("landscape", 1920, 1080, (0.03, 0.05, 0.97, 0.955), 14.0),
    "square": CanvasFormat("square", 1080, 1080, (0.05, 0.05, 0.95, 0.955), 14.0),
    "portrait": CanvasFormat("portrait", 1080, 1350, (0.05, 0.05, 0.95, 0.96), 14.0),
    # 9:16: keep clear of the caption block at the bottom (~17%) and the
    # status/progress bar at the top (~8%). The side margins are symmetric so
    # the chart is centred on the phone; 10% keeps all but the last ~1% clear of
    # the TikTok/Reels action rail (x >= ~89%).
    "story": CanvasFormat("story", 1080, 1920, (0.10, 0.17, 0.90, 0.92), 17.0),
}

FORMAT_NAMES = tuple(FORMATS)


def get_format(name: str) -> CanvasFormat:
    """Look up a format by name; raises ``ValueError`` listing valid names."""
    key = (name or "").strip().lower()
    if key not in FORMATS:
        raise ValueError(f"format must be one of {', '.join(FORMAT_NAMES)} (got {name!r})")
    return FORMATS[key]


def new_canvas(fmt: CanvasFormat) -> Figure:
    """A themed figure at exactly the format's pixel size."""
    theme.setup_turnone_theme()
    fig = plt.figure(figsize=fmt.figsize, dpi=DESIGN_DPI, facecolor=FACE)
    return fig


def pin(artist):
    """Mark a figure-level artist as frame chrome: :func:`fit_to_safe_width` leaves it where it is.

    Header, footer and watermark are pinned already. Pin any other ``fig.text``
    anchored to a safe-area edge (a footnote, a badge); unpinned figure texts
    move with the content they label.
    """
    artist._t1_pinned = True
    return artist


def header_height(fmt: CanvasFormat, subtitle: bool = True) -> float:
    """Figure fraction :func:`add_header` occupies below the safe top."""
    font_px = DESIGN_DPI / 72.0
    title_px = _title_size(fmt) * font_px
    need = 1.25 * title_px
    if subtitle:
        need += 1.55 * fmt.base_fontsize * 0.95 * font_px
    return (need + 0.9 * fmt.base_fontsize * font_px) / fmt.height_px


def safe_gridspec(
    fig: Figure, fmt: CanvasFormat, nrows: int, ncols: int = 1,
    header: Optional[float] = None, footer: float = 0.04, top_labels: bool = False, **kwargs,
) -> GridSpec:
    """GridSpec filling the safe area minus a header band and a footer band.

    ``header`` is a figure fraction reserved for :func:`add_header` (default:
    measured from the format's font sizes). ``footer`` is a fraction of the
    safe area's height reserved for :func:`add_footer`. ``top_labels`` leaves a
    line for labels drawn above the first axis (corner numbers). Extra kwargs
    (``hspace``, ``height_ratios`` ...) pass through to ``fig.add_gridspec``.
    """
    left, bottom, right, top = fmt.safe
    height = top - bottom
    # Tick labels and axis labels need a fixed amount of *pixels*, not a
    # fraction of the frame: a 5% margin is plenty on 1920 px and clips the
    # y label on 1080 px.
    font_px = fmt.base_fontsize * DESIGN_DPI / 72.0
    y_label_pad = 6.2 * font_px / fmt.width_px
    x_label_pad = 2.8 * font_px / fmt.height_px
    top_pad = (1.6 * font_px / fmt.height_px) if top_labels else 0.0
    header = header_height(fmt) if header is None else header
    return fig.add_gridspec(
        nrows, ncols,
        left=left + y_label_pad,
        right=right,
        bottom=bottom + footer * height + x_label_pad,
        top=top - header - top_pad,
        **kwargs,
    )


def _title_size(fmt: CanvasFormat) -> float:
    return fmt.base_fontsize * (1.55 if fmt.is_vertical else 1.45)


def _shrink_to_width(fig: Figure, text, max_px: float, min_scale: float = 0.7) -> None:
    """Reduce a text's font size until it is at most ``max_px`` wide (not below ``min_scale``)."""
    renderer = fig.canvas.get_renderer()
    width = text.get_window_extent(renderer).width
    if width <= max_px or width <= 0:
        return
    # 2 % under: text width does not scale exactly linearly with the font size (hinting, kerning).
    text.set_fontsize(text.get_fontsize() * max(min_scale, 0.98 * max_px / width))


def _watermark_px(fmt: CanvasFormat, size_frac: float = 0.042) -> float:
    logo = _light_logo()
    aspect = (logo.shape[1] / logo.shape[0]) if logo is not None else 1.0
    return size_frac * min(fmt.width_px, fmt.height_px) * aspect


def add_header(fig: Figure, fmt: CanvasFormat, title: str, subtitle: Optional[str] = None) -> None:
    """Title (bold) and optional subtitle, top-left of the safe area.

    Either line is shrunk (to at most 70 %) when it would run past the safe
    area -- the title stops short of the watermark in the top-right corner.
    """
    left, _, right, top = fmt.safe
    width_px = (right - left) * fmt.width_px
    size = _title_size(fmt)
    head = pin(fig.text(left, top, title, ha="left", va="top", fontsize=size, fontweight="bold", color=TEXT))
    _shrink_to_width(fig, head, width_px - _watermark_px(fmt) - 24)
    if subtitle:
        title_px = size * DESIGN_DPI / 72.0
        gap = 1.45 * title_px / fmt.height_px
        sub = pin(fig.text(left, top - gap, subtitle, ha="left", va="top",
                           fontsize=fmt.base_fontsize * 0.95, color=MUTED))
        _shrink_to_width(fig, sub, width_px)


def add_footer(
    fig: Figure, fmt: CanvasFormat,
    source: Optional[str] = DEFAULT_SOURCE, handle: Optional[str] = DEFAULT_HANDLE,
) -> None:
    """Source line bottom-left and handle bottom-right, inside the safe area.

    The source line doubles as the data disclosure Reddit's ``[OC]`` rule asks for.
    """
    left, bottom, right, _ = fmt.safe
    size = fmt.base_fontsize * 0.8
    if source:
        pin(fig.text(left, bottom, source, ha="left", va="bottom", fontsize=size, color=MUTED))
    if handle:
        pin(fig.text(right, bottom, handle, ha="right", va="bottom", fontsize=size,
                     color=MUTED, fontweight="semibold"))


@lru_cache(maxsize=1)
def _light_logo() -> Optional[np.ndarray]:
    """The full T1 mark, cropped, with its black strokes lifted to the text colour.

    The source mark is black + red, and black vanishes on the dark theme.
    """
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - Pillow ships with matplotlib
        return None
    if not _LOGO_FULL.exists():
        return None
    img = Image.open(_LOGO_FULL).convert("RGBA")
    bbox = img.getbbox()
    if bbox:
        img = img.crop(bbox)
    arr = np.asarray(img).astype(np.float32) / 255.0
    rgb, alpha = arr[..., :3], arr[..., 3]
    dark = (rgb.max(axis=-1) < 0.35) & (alpha > 0)
    rgb[dark] = np.array([0xE9, 0xE9, 0xE9], dtype=np.float32) / 255.0
    return np.dstack([rgb, alpha])


def add_watermark(fig: Figure, fmt: CanvasFormat, alpha: float = 0.9, size_frac: float = 0.042) -> None:
    """Small T1 mark in the top-right corner of the safe area.

    Sized off the frame's *short* side so it is the same physical size on
    every format.
    """
    logo = _light_logo()
    if logo is None:
        return
    _, _, right, top = fmt.safe
    height_px = size_frac * min(fmt.width_px, fmt.height_px)
    aspect = logo.shape[1] / logo.shape[0]
    h = height_px / fmt.height_px
    w = height_px * aspect / fmt.width_px
    ax = fig.add_axes((right - w, top - h, w, h), zorder=10)
    ax.imshow(logo, alpha=alpha)
    ax.set_axis_off()
    pin(ax)


def add_legacy_watermark(fig: Figure, xo: int = 575, yo: int = 575, alpha: float = 0.6, zorder: int = 3) -> None:
    """The original mid-figure 150 px mark, at fixed pixel offsets.

    Exists so the legacy (non-``format``) renders keep their exact look while
    no longer depending on the process's working directory.
    """
    try:
        import matplotlib.image as mpimg
        logo = mpimg.imread(str(_LOGO_MARK))
        fig.figimage(logo, xo, yo, zorder=zorder, alpha=alpha)
    except Exception:
        pass


def driver_headshot_path(code: str) -> Optional[Path]:
    """``assets/drivers/{CODE}.png`` if it exists (no path components allowed)."""
    code = (code or "").strip().upper()
    if not code.isalnum() or len(code) > 4:
        return None
    path = _DRIVER_DIR / f"{code}.png"
    return path if path.exists() else None


def add_driver_plate(
    fig: Figure, fmt: CanvasFormat, rect: Tuple[float, float, float, float],
    code: str, color: str, lap_time: Optional[str] = None,
    detail: Optional[str] = None, hero: bool = False,
) -> None:
    """A driver identity block: team-colour bar, code, lap time, optional headshot.

    ``rect`` is ``(left, bottom, width, height)`` in figure fractions. Headshots
    are opt-in (``hero=True``) because the bundled images are rights-managed
    photography; without it the plate is text and colour only.
    """
    ax = fig.add_axes(rect, zorder=5)
    ax.set_axis_off()
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    text_x = 0.06
    if hero:
        path = driver_headshot_path(code)
        if path is not None:
            try:
                import matplotlib.image as mpimg
                img = mpimg.imread(str(path))
                # Square headshot to the left of the text, height-filling.
                w_frac = (rect[3] * fmt.height_px) / (rect[2] * fmt.width_px)
                ax.imshow(img, extent=(0.04, 0.04 + w_frac, 0.0, 1.0), aspect="auto", zorder=1)
                text_x = 0.06 + w_frac
            except Exception:
                pass

    ax.add_patch(plt.Rectangle((0.0, 0.08), 0.018, 0.84, color=color, transform=ax.transAxes, zorder=2))
    sub = " · ".join(p for p in (lap_time, detail) if p)
    # Code over sub-line, as one block centred in the plate; both shrink together
    # when the plate is too short for them (it used to spill over its top edge).
    big, small = fmt.base_fontsize * 1.45, fmt.base_fontsize * 0.9
    pt_px = DESIGN_DPI / 72.0
    big_px, small_px = big * 1.12 * pt_px, (small * 1.3 * pt_px if sub else 0.0)
    rect_px = rect[3] * fmt.height_px
    k = min(1.0, 0.96 * rect_px / (big_px + small_px))
    big, small, big_px, small_px = big * k, small * k, big_px * k, small_px * k
    top = 0.5 + (big_px + small_px) / (2.0 * rect_px)
    ax.text(text_x, top, code.upper(), ha="left", va="top", fontsize=big,
            fontweight="bold", color=color, transform=ax.transAxes)
    if sub:
        ax.text(text_x, top - big_px / rect_px, sub, ha="left", va="top", fontsize=small,
                color=TEXT, transform=ax.transAxes)


def style_axis(ax, fmt: CanvasFormat) -> None:
    """Tick/label sizes for the format (rcParams are sized for legacy plots)."""
    tick = fmt.base_fontsize * 0.85
    ax.tick_params(labelsize=tick)
    ax.xaxis.label.set_size(fmt.base_fontsize)
    ax.yaxis.label.set_size(fmt.base_fontsize)
    ax.grid(True, color="#2a2a2a", linestyle="--", alpha=0.5)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)


def _content_extent(renderer, axes, texts) -> Optional[Tuple[float, float]]:
    """Pixel x-extent of what the axes and texts actually draw."""
    xs0, xs1 = [], []
    for ax in axes:
        bb = ax.get_tightbbox(renderer)
        if bb is not None and bb.width > 0:
            xs0.append(bb.x0)
            xs1.append(bb.x1)
    for t in texts:
        bb = t.get_window_extent(renderer)
        if bb.width > 0:
            xs0.append(bb.x0)
            xs1.append(bb.x1)
    return (min(xs0), max(xs1)) if xs0 else None


def _position_span(fig: Figure, axes, texts) -> Tuple[float, float]:
    """Pixel range of the axes boxes and text anchors: the part a stretch scales."""
    width = fig.bbox.width
    xs = []
    for ax in axes:
        pos = ax.get_position(original=True)
        xs += [pos.x0 * width, pos.x1 * width]
    for t in texts:
        xs.append(t.get_position()[0] * width)
    return (min(xs), max(xs)) if xs else (0.0, 0.0)


def _move_x(fig: Figure, axes, texts, scale: float, shift_px: float) -> None:
    """Apply ``x -> scale * x + shift_px`` (figure pixels) to axes boxes and text anchors."""
    width = fig.bbox.width
    for ax in axes:
        pos = ax.get_position(original=True)
        x0 = (scale * pos.x0 * width + shift_px) / width
        x1 = (scale * pos.x1 * width + shift_px) / width
        ax.set_position((x0, pos.y0, x1 - x0, pos.height), which="both")
    for t in texts:
        x, y = t.get_position()
        t.set_position(((scale * x * width + shift_px) / width, y))


def fit_to_safe_width(fig: Figure, fmt: CanvasFormat, iterations: int = 3) -> None:
    """Make the drawn content span the safe area's width, centred on the frame.

    A layout reserves room for tick and axis labels up front (it cannot know how
    wide ``"320"`` or ``"10  COL"`` will render), so charts ended up with more
    empty space on one side than the other. This measures the real extent of
    every unpinned axes (tick labels, axis labels and out-of-axes annotations
    included) and every unpinned figure text, then stretches and shifts them all
    by one affine map so the extent is ``safe.left .. safe.right``. Relative
    placement is kept; label sizes (pixels) are not scaled. Pinned chrome
    (:func:`pin`: header, footer, watermark) does not move.
    """
    axes = [ax for ax in fig.axes if not getattr(ax, "_t1_pinned", False)]
    texts = [t for t in fig.texts if not getattr(t, "_t1_pinned", False) and t.get_visible()
             and t.get_transform() == fig.transFigure]
    lone_maps = _lone_maps(axes)
    axes = [ax for ax in axes if ax not in lone_maps]
    width = fig.bbox.width
    left, right = fmt.safe[0], fmt.safe[2]
    # A map alone in its row takes the whole safe width; being aspect-locked it
    # then sits centred on the frame. (Its grid column started after the y-label
    # padding of the panels below, which put it right of centre.)
    for ax in lone_maps:
        pos = ax.get_position(original=True)
        ax.set_position((left, pos.y0, right - left, pos.height), which="both")
    if not axes and not texts:
        return
    target0, target1 = left * width, right * width
    renderer = fig.canvas.get_renderer()
    for _ in range(iterations):
        fig.canvas.draw()
        extent = _content_extent(renderer, axes, texts)
        if extent is None:
            return
        x0, x1 = extent
        if abs(x0 - target0) < 1.0 and abs(x1 - target1) < 1.0:
            return
        p0, p1 = _position_span(fig, axes, texts)
        span = p1 - p0
        overhang = (x1 - x0) - span
        scale = (target1 - target0 - overhang) / span if span > 1.0 else 1.0
        if not 0.5 <= scale <= 2.0:
            scale = 1.0
        # The left label overhang (p0 - x0) is fixed in pixels; land x0 on target0.
        _move_x(fig, axes, texts, scale, target0 + (p0 - x0) - scale * p0)
    # Aspect-locked axes (track maps beside a panel) may refuse to widen: at least centre.
    fig.canvas.draw()
    extent = _content_extent(renderer, axes, texts)
    if extent is not None:
        _move_x(fig, axes, texts, 1.0, (target0 + target1 - extent[0] - extent[1]) / 2.0)


def _lone_maps(axes) -> list:
    """Aspect-locked, axis-less axes (track maps) that do not share their row with another axes."""
    lone = []
    for ax in axes:
        if ax.axison or ax.get_aspect() == "auto":
            continue
        pos = ax.get_position(original=True)
        if not any(other is not ax and _rows_overlap(pos, other.get_position(original=True)) for other in axes):
            lone.append(ax)
    return lone


def _rows_overlap(a, b) -> bool:
    return min(a.y1, b.y1) - max(a.y0, b.y0) > 0.25 * min(a.height, b.height)


def save_png(fig: Figure, path: str, fmt: CanvasFormat, scale: float = 1.0, fit: bool = True) -> str:
    """Fit the content to the safe width, save at the format's pixel size (times ``scale``) and close.

    Never ``bbox_inches='tight'``: that changes the pixel size, and the whole
    point of a format is that the file is exactly the size the platform wants.
    """
    if fit:
        fit_to_safe_width(fig, fmt)
    fig.savefig(path, dpi=DESIGN_DPI * scale, facecolor=fig.get_facecolor())
    plt.close(fig)
    return path


def format_suffix(fmt_name: Optional[str]) -> str:
    """Filename suffix for a formatted render (``''`` for legacy renders)."""
    return f"_{fmt_name}" if fmt_name else ""

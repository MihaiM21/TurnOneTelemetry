"""Offline tests for the canvas width-fitting, header shrinking and driver-plate sizing."""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import pytest  # noqa: E402

from src.services.plotting import canvas  # noqa: E402

FORMATS = canvas.FORMAT_NAMES


@pytest.fixture(autouse=True)
def _close_figures():
    yield
    plt.close("all")


def _chrome(fmt):
    fig = canvas.new_canvas(fmt)
    canvas.add_header(fig, fmt, "Title", "Sub")
    canvas.add_footer(fig, fmt)
    canvas.add_watermark(fig, fmt)
    return fig


def _panel(fig, fmt, spec=None):
    ax = fig.add_subplot(spec if spec is not None else canvas.safe_gridspec(fig, fmt, 1)[0])
    ax.plot([0, 1, 2], [10, 20, 15])
    ax.set_ylabel("Position")
    ax.set_yticks([10, 15, 20])
    ax.set_yticklabels(["10  COL", "15  COL", "20  COL"])
    return ax


def test_story_safe_area_is_symmetric():
    safe = canvas.FORMATS["story"].safe
    assert abs(safe[0] - (1 - safe[2])) < 1e-9


@pytest.mark.parametrize("fmt_name", FORMATS)
def test_fit_spans_the_safe_width(fmt_name):
    fmt = canvas.get_format(fmt_name)
    fig = _chrome(fmt)
    ax = _panel(fig, fmt)
    header = [t for t in fig.texts if t.get_text() == "Title"][0]
    before = header.get_position()

    canvas.fit_to_safe_width(fig, fmt)
    fig.canvas.draw()

    bb = ax.get_tightbbox(fig.canvas.get_renderer())
    assert bb.x0 == pytest.approx(fmt.safe[0] * fmt.width_px, abs=2)
    assert bb.x1 == pytest.approx(fmt.safe[2] * fmt.width_px, abs=2)
    assert header.get_position() == before


def _map_axes(fig, spec):
    ax = fig.add_subplot(spec)
    ax.plot([0, 1, 1, 0, 0], [0, 0, 1, 1, 0])
    ax.set_axis_off()
    ax.set_aspect("equal")
    return ax


@pytest.mark.parametrize("fmt_name", FORMATS)
def test_lone_map_takes_the_full_safe_width_and_is_centred(fmt_name):
    fmt = canvas.get_format(fmt_name)
    fig = _chrome(fmt)
    gs = canvas.safe_gridspec(fig, fmt, 2, height_ratios=[1, 1])
    track = _map_axes(fig, gs[0])
    _panel(fig, fmt, gs[1])

    canvas.fit_to_safe_width(fig, fmt)
    fig.canvas.draw()

    pos = track.get_position(original=True)
    assert pos.x0 == pytest.approx(fmt.safe[0], abs=1e-6)
    assert pos.x1 == pytest.approx(fmt.safe[2], abs=1e-6)
    win = track.get_window_extent(fig.canvas.get_renderer())
    # Centred in the safe area (landscape's is 0.035..0.975, so not exactly on the frame's midline).
    assert (win.x0 + win.x1) / 2 == pytest.approx((fmt.safe[0] + fmt.safe[2]) / 2 * fmt.width_px, abs=3)
    if fmt.name != "landscape":
        assert (win.x0 + win.x1) / 2 == pytest.approx(fmt.width_px / 2, abs=3)


def test_map_sharing_a_row_is_not_widened():
    fmt = canvas.get_format("landscape")
    fig = _chrome(fmt)
    gs = canvas.safe_gridspec(fig, fmt, 1, 2)
    _panel(fig, fmt, gs[0, 0])
    track = _map_axes(fig, gs[0, 1])

    canvas.fit_to_safe_width(fig, fmt)

    pos = track.get_position(original=True)
    assert pos.width < 0.9 * (fmt.safe[2] - fmt.safe[0])


def test_unpinned_text_moves_but_pinned_text_does_not():
    fmt = canvas.get_format("portrait")
    fig = _chrome(fmt)
    _panel(fig, fmt)
    loose = fig.text(0.5, 0.5, "loose", ha="center")
    fixed = canvas.pin(fig.text(0.5, 0.45, "fixed", ha="center"))
    loose_before, fixed_before = loose.get_position(), fixed.get_position()

    canvas.fit_to_safe_width(fig, fmt)

    assert loose.get_position() != loose_before
    assert fixed.get_position() == fixed_before


@pytest.mark.parametrize("fmt_name", FORMATS)
def test_long_subtitle_is_shrunk_to_the_safe_width(fmt_name):
    fmt = canvas.get_format(fmt_name)
    fig = canvas.new_canvas(fmt)
    # Sized from a probe so it overflows by 25 % at full size, which the 70 % shrink floor can absorb.
    safe_px = (fmt.safe[2] - fmt.safe[0]) * fmt.width_px
    probe = fig.text(0, 0, "Azerbaijan GP ", fontsize=fmt.base_fontsize * 0.95)
    per_repeat = probe.get_window_extent(fig.canvas.get_renderer()).width
    probe.remove()
    long_sub = "Azerbaijan GP " * int(1.25 * safe_px / per_repeat)
    canvas.add_header(fig, fmt, "Title", long_sub)
    fig.canvas.draw()
    sub = [t for t in fig.texts if t.get_text() == long_sub][0]
    assert sub.get_window_extent(fig.canvas.get_renderer()).width <= safe_px + 1
    assert sub.get_fontsize() < fmt.base_fontsize * 0.95      # it really had to shrink


@pytest.mark.parametrize("fmt_name", FORMATS)
def test_driver_plate_text_fits_a_short_rect(fmt_name):
    fmt = canvas.get_format(fmt_name)
    fig = canvas.new_canvas(fmt)
    rect = (0.1, 0.5, 0.4, 0.03)
    canvas.add_driver_plate(fig, fmt, rect, "VER", "#3671C6", lap_time="1:30.512", detail="Q3 lap 12")
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    plate = fig.axes[-1]
    box = plate.get_window_extent(renderer)
    assert len(plate.texts) == 2
    for t in plate.texts:
        bb = t.get_window_extent(renderer)
        assert bb.y0 >= box.y0 - 2
        assert bb.y1 <= box.y1 + 2


def test_save_png_without_fit_keeps_positions(tmp_path):
    fmt = canvas.get_format("square")
    fig = _chrome(fmt)
    ax = _panel(fig, fmt)
    before = ax.get_position().bounds
    out = tmp_path / "x.png"

    assert canvas.save_png(fig, str(out), fmt, fit=False) == str(out)

    assert ax.get_position().bounds == before
    from PIL import Image
    with Image.open(out) as img:
        assert img.size == (fmt.width_px, fmt.height_px)


@pytest.mark.parametrize("fmt_name", FORMATS)
def test_save_png_size_is_exact_with_fit(tmp_path, fmt_name):
    from PIL import Image

    fmt = canvas.get_format(fmt_name)
    fig = _chrome(fmt)
    _panel(fig, fmt)
    out = tmp_path / f"{fmt_name}.png"
    canvas.save_png(fig, str(out), fmt)
    with Image.open(out) as img:
        assert img.size == (fmt.width_px, fmt.height_px)

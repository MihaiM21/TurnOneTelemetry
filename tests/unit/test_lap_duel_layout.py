"""Layout checks on the rendered Lap Duel figure (text containment, overlap, canvas bounds).

The figure is captured after ``fit_to_safe_width`` and a draw, by replacing
``canvas.save_png`` inside the renderer, so assertions run on real glyph extents.
"""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

import copy  # noqa: E402

import matplotlib.pyplot as plt  # noqa: E402
import pytest  # noqa: E402

from src.services.analysis.v2 import _lap_duel_render as render_mod  # noqa: E402
from src.services.analysis.v2 import lap_duel  # noqa: E402
from src.services.plotting import canvas  # noqa: E402
from tests.unit.test_lap_duel_payload import _side  # noqa: E402

TOL = 2.0          # px, containment slack
OVERLAP_TOL = 3.0  # px, overlap allowed in both axes before two texts count as colliding


@pytest.fixture(scope="module")
def raw_payload():
    a = _side("VER", 90.512, team="Red Bull", color="#3671C6", number="1")
    b = _side("NOR", 90.889, team="McLaren", color="#FF8000", number="4", speed_scale=0.99)
    return lap_duel.build_payload(a, b, circuit_info=None)


@pytest.fixture(scope="module")
def payload(raw_payload):
    """Synthetic duel with both top speeds mid-lap (see the edge-case test for the start/finish line)."""
    return _with_top_speed_at(raw_payload, 0.45, 0.5)


def _with_top_speed_at(payload, frac_a, frac_b):
    out = copy.deepcopy(payload)
    span = out["distance"][-1] - out["distance"][0]
    a, b = out["a"]["driverCode"], out["b"]["driverCode"]
    out["highlights"]["top_speed_at_m"] = {a: frac_a * span, b: frac_b * span}
    return out


@pytest.fixture()
def rendered(payload, tmp_path, monkeypatch):
    """Callable ``rendered(fmt_name, pl=None) -> (fig, fmt)`` of the fitted, drawn figure."""
    captured = {}

    def fake_save_png(fig, path, fmt, scale=1.0, fit=True):
        canvas.fit_to_safe_width(fig, fmt)
        fig.canvas.draw()
        captured["fig"] = fig
        return str(path)

    monkeypatch.setattr(render_mod.cv, "save_png", fake_save_png)
    monkeypatch.setattr(render_mod, "output_path", lambda *a, **k: str(tmp_path / "duel.png"))
    monkeypatch.chdir(tmp_path)

    def _render(fmt_name, pl=None):
        render_mod.render(payload if pl is None else pl, fmt_name)
        return captured["fig"], canvas.get_format(fmt_name)

    yield _render
    plt.close("all")


def _texts(artists):
    return [t for t in artists if t.get_text().strip() and t.get_visible()]


def _axes_by_ylabel(fig, label):
    return [ax for ax in fig.axes if ax.get_ylabel() == label]


@pytest.mark.parametrize("fmt_name", canvas.FORMAT_NAMES)
def test_in_axes_labels_stay_inside_their_axes(rendered, fmt_name):
    fig, _ = rendered(fmt_name)
    renderer = fig.canvas.get_renderer()
    checked = 0
    for label in ("Speed (km/h)", "Gap (s)"):
        axes = _axes_by_ylabel(fig, label)
        assert axes, label
        for ax in axes:
            box = ax.get_window_extent(renderer)
            inverse = ax.transAxes.inverted()
            for t in _texts(ax.texts):
                bb = t.get_window_extent(renderer)
                _, y = inverse.transform(((bb.x0 + bb.x1) / 2, (bb.y0 + bb.y1) / 2))
                if y > 1.0:
                    continue      # corner numbers sit deliberately above the panel
                checked += 1
                assert bb.x0 >= box.x0 - TOL and bb.x1 <= box.x1 + TOL, (label, t.get_text())
                assert bb.y0 >= box.y0 - TOL and bb.y1 <= box.y1 + TOL, (label, t.get_text())
    assert checked > 0


@pytest.mark.parametrize("fmt_name", canvas.FORMAT_NAMES)
def test_texts_in_one_axes_do_not_collide(rendered, fmt_name):
    fig, _ = rendered(fmt_name)
    renderer = fig.canvas.get_renderer()
    for ax in fig.axes:
        items = [(t.get_text(), t.get_window_extent(renderer)) for t in _texts(ax.texts)]
        for i, (ta, a) in enumerate(items):
            for tb, b in items[i + 1:]:
                ox = min(a.x1, b.x1) - max(a.x0, b.x0)
                oy = min(a.y1, b.y1) - max(a.y0, b.y0)
                assert not (ox > OVERLAP_TOL and oy > OVERLAP_TOL), (ax.get_ylabel(), ta, tb)


@pytest.mark.parametrize("fmt_name", canvas.FORMAT_NAMES)
def test_all_texts_are_inside_the_canvas(rendered, fmt_name):
    fig, fmt = rendered(fmt_name)
    renderer = fig.canvas.get_renderer()
    artists = list(fig.texts)
    for ax in fig.axes:
        artists += list(ax.texts)
    artists = _texts(artists)
    assert artists
    for t in artists:
        bb = t.get_window_extent(renderer)
        assert bb.x0 >= -TOL and bb.x1 <= fmt.width_px + TOL, t.get_text()
        assert bb.y0 >= -TOL and bb.y1 <= fmt.height_px + TOL, t.get_text()


@pytest.mark.parametrize("fmt_name", canvas.FORMAT_NAMES)
@pytest.mark.parametrize("frac", [0.0, 1.0])
def test_top_speed_labels_stay_in_frame_when_the_peak_is_at_the_line(rendered, raw_payload, fmt_name, frac):
    pl = _with_top_speed_at(raw_payload, frac, frac)
    fig, _ = rendered(fmt_name, pl)
    renderer = fig.canvas.get_renderer()
    ax = _axes_by_ylabel(fig, "Speed (km/h)")[0]
    box = ax.get_window_extent(renderer)
    top = {f"{pl['highlights']['top_speed_kmh'][k]:.0f}" for k in ("VER", "NOR")}
    labels = [t for t in ax.texts if t.get_text() in top]
    assert len(labels) == 2
    for t in labels:
        bb = t.get_window_extent(renderer)
        assert bb.x0 >= box.x0 - TOL and bb.x1 <= box.x1 + TOL, t.get_text()

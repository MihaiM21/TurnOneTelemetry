import matplotlib as mpl
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe

def setup_turnone_theme():
    plt.rcParams.update({
        # Background
        "figure.facecolor": "#0d0d0d",
        "axes.facecolor": "#141414",

        # Text & font
        "font.family": "sans-serif",
        "font.sans-serif": ["Montserrat", "DejaVu Sans"],
        "text.color": "#E9E9E9",
        "axes.labelcolor": "#E9E9E9",
        "xtick.color": "#E9E9E9",
        "ytick.color": "#E9E9E9",

        # Axes
        "axes.edgecolor": "#2a2a2a",
        "axes.linewidth": 1.2,
        "axes.grid": True,
        "grid.color": "#2a2a2a",
        "grid.linestyle": "--",
        "grid.alpha": 0.4,

        # Lines
        "lines.linewidth": 2.5,
        "lines.solid_capstyle": "round",

        # Legend
        "legend.facecolor": "#141414",
        "legend.edgecolor": "#2a2a2a",
        "legend.fontsize": 11,
        "legend.title_fontsize": 12,

        # Titles
        "figure.titlesize": 18,
        "axes.titlesize": 14,
        "axes.titleweight": "bold",
    })


# Track-status shading colors tuned for the dark theme.
_TRACK_STATUS_SHADING = {
    "SC": ("#FF8C00", 0.15),    # orange
    "VSC": ("#FFD700", 0.12),   # yellow
    "RED": ("#FF3B3B", 0.18),   # red
    "YELLOW": ("#FFD700", 0.08),
}


def add_track_status_shading(ax, periods):
    """Shade lap ranges for Safety Car / VSC / Red-flag periods.

    ``periods`` is the list returned by
    ``src.services.analysis.v2._race_helpers.get_track_status_periods`` — dicts
    with ``status``, ``start_lap`` and ``end_lap`` (``end_lap`` may be ``None``
    when the status was active at the session end). GREEN periods are skipped.
    """
    if not periods:
        return

    x_max = ax.get_xlim()[1]
    seen_labels = set()
    for period in periods:
        status = period.get("status")
        style = _TRACK_STATUS_SHADING.get(status)
        if style is None:
            continue
        color, alpha = style
        start = period.get("start_lap")
        if start is None:
            continue
        end = period.get("end_lap")
        if end is None:
            end = x_max
        if end <= start:
            end = start + 1
        label = status if status not in seen_labels else None
        seen_labels.add(status)
        ax.axvspan(start, end, color=color, alpha=alpha, zorder=0, label=label)


def add_glow(ax, linewidth=6, alpha=0.25, passes=4):
    """
    Adaugă efect de glow soft neon la toate liniile din ax.
    linewidth -> cât de groasă e aura
    alpha -> transparență
    passes -> câte straturi pentru glow
    """
    lines = ax.get_lines()
    for line in lines:
        path_effects = []
        for n in range(passes):
            path_effects.append(pe.withStroke(
                linewidth=linewidth + (n * 1.5),
                alpha=alpha / (n + 1),
                foreground=line.get_color()))
        line.set_path_effects(path_effects)

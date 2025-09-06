import matplotlib as mpl
import matplotlib.pyplot as plt

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
            path_effects.append(mpl.patheffects.withStroke(
                linewidth=linewidth + (n * 1.5),
                alpha=alpha / (n + 1),
                foreground=line.get_color()))
        line.set_path_effects(path_effects)
"""
Driver-radar polar rendering (V2).

Draws a scaled radar payload (from :mod:`driver_radar`) as a themed polar chart:
one filled, glowing polygon per driver in team/driver colours, on the dark
TurnOne theme, sized for either an on-site square figure or a portrait
social-media crop. This module owns *only* presentation — it never touches raw
data or normalization.

The single-driver "hero" flag on the payload is honoured with a bolder, more
saturated fill; the driver-headshot/portrait polish (``assets/drivers/{TLA}.png``)
is a planned fast-follow and is loaded best-effort in a try/except so its
absence never breaks a render.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import matplotlib.image as mpimg
import matplotlib.pyplot as plt
import numpy as np

from src.services.plotting import output as dirOrg
from src.services.plotting import theme as setup_theme

# Portrait crop tuned for 1080x1350 (4:5, the Instagram feed sweet spot).
_SQUARE_SIZE = (10.0, 10.0)
_PORTRAIT_SIZE = (10.8, 13.5)


def _resolve_output(
    scope: str, year: int, event: Optional[str], session: Optional[str],
    span: Optional[str], tlas: List[str],
) -> str:
    """Folder + filename per scope, via the shared ``checkForFolder`` convention."""
    tag = "_".join(tlas) if tlas else "auto"
    if scope == "session":
        event_folder = (event or "Event").replace(" ", "")
        sub = f"{year}/{event_folder}/{session}"
        name = f"Driver radar {tag} {year} {event} {session}.png"
    elif scope == "career":
        sub = f"{year}/Career/Radar"
        name = f"Career radar {tag} {span or year}.png"
    else:  # season
        sub = f"{year}/Season/Radar"
        name = f"Season radar {tag} {year}.png"
    dirOrg.checkForFolder(sub)
    return f"outputs/plots/{sub}/{name}"


def _closed(seq: List[float]) -> List[float]:
    """Repeat the first element at the end to close the polygon loop."""
    return list(seq) + [seq[0]] if seq else seq


def render_radar(
    payload: Dict[str, Any],
    title: str,
    subtitle: str,
    scope: str,
    year: int,
    event: Optional[str] = None,
    session: Optional[str] = None,
    span: Optional[str] = None,
    portrait: bool = False,
) -> str:
    """Render a scaled radar ``payload`` to a themed PNG and return its path."""
    setup_theme.setup_turnone_theme()

    axes_names: List[str] = payload["axes"]
    drivers: List[Dict[str, Any]] = payload["drivers"]
    hero = payload.get("hero", False)
    n = len(axes_names)

    angles = np.linspace(0, 2 * np.pi, n, endpoint=False).tolist()
    angles_closed = _closed(angles)

    figsize = _PORTRAIT_SIZE if portrait else _SQUARE_SIZE
    fig = plt.figure(figsize=figsize)
    ax = fig.add_subplot(111, polar=True)
    ax.set_theta_offset(np.pi / 2)   # first spoke at the top
    ax.set_theta_direction(-1)       # clockwise, like a compass

    for d in drivers:
        # Missing spokes (None) collapse to the centre rather than lying.
        vals = [v if v is not None else 0.0 for v in d["values"]]
        vals_closed = _closed(vals)
        color = d.get("color", "#777777")
        fill_alpha = 0.35 if hero else 0.18
        ax.plot(angles_closed, vals_closed, color=color, linewidth=2.6,
                label=d["tla"], zorder=3, solid_capstyle="round")
        ax.fill(angles_closed, vals_closed, color=color, alpha=fill_alpha, zorder=2)

    setup_theme.add_glow(ax)

    ax.set_xticks(angles)
    ax.set_xticklabels(axes_names, fontsize=12, fontweight="bold")
    ax.set_ylim(0, 100)
    ax.set_yticks([20, 40, 60, 80])
    ax.set_yticklabels(["20", "40", "60", "80"], fontsize=8, color="#888888")
    ax.set_rlabel_position(180 / n)
    ax.grid(color="#2a2a2a", linestyle="--", alpha=0.5)
    ax.spines["polar"].set_color("#2a2a2a")

    if not hero:
        ax.legend(loc="upper right", bbox_to_anchor=(1.15, 1.10), fontsize=11)

    tlas = [d["tla"] for d in drivers]
    fig.suptitle(f"{title}\n{subtitle}", fontsize=16, fontweight="bold", y=0.99)

    # Hero fast-follow: driver headshot behind the chart. Best-effort only.
    if hero and tlas:
        try:
            img = mpimg.imread(f"assets/drivers/{tlas[0]}.png")
            fig.figimage(img, 20, 20, zorder=0, alpha=0.35)
        except Exception:
            pass

    try:
        logo = mpimg.imread("assets/images/logo mic.png")
        fig.figimage(logo, 20, figsize[1] * fig.dpi - 90, zorder=5, alpha=0.5)
    except Exception:
        pass

    out_path = _resolve_output(scope, year, event, session, span, tlas)
    fig.savefig(out_path, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return out_path

import json
from functools import lru_cache
from pathlib import Path

teams = [
    "Alpine", "Aston Martin", "Ferrari", "Haas", "Kick Sauber",
    "McLaren", "Mercedes", "Racing Bulls", "Red Bull Racing", "Williams"
]

team_colors = {
    "Alpine": "#0093CC",
    "Aston Martin": "#229971",
    "Ferrari": "#E80020",
    "Haas": "#B6BABD",
    "Kick Sauber": "#52E252",
    "McLaren": "#FF8000",
    "Mercedes": "#27F4D2",
    "Racing Bulls": "#6692FF",
    "Red Bull Racing": "#3671C6",
    "Williams": "#64C4FF",
}

# Path to the year-keyed team/driver reference data, resolved relative to the repo root
# (src/services/plotting/colors.py -> parents[3] == repo root).
_DATA_DIR = Path(__file__).resolve().parents[3] / "src" / "domain" / "data"

# Some entries in teams.json ship without a real livery colour on file (or with a
# placeholder white). Known offenders get a sane fallback instead of rendering invisible
# white lines/markers on plots.
_TEAM_COLOR_FALLBACKS = {
    "Audi": "#BB0A30",
    "Cadillac": "#0B2C5A",
    "Haas": "#B6BABD",
}


@lru_cache(maxsize=1)
def _load_teams_data():
    path = _DATA_DIR / "teams.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _latest_teams_year():
    teams_data = _load_teams_data()
    if not teams_data:
        return None
    return max(int(year) for year in teams_data.keys())


def _resolve_team_color(entry):
    """Return the usable colour for a teams.json team entry, guarding against a
    missing or placeholder-white colour on file."""
    color = entry.get("color")
    if not color or color.strip().upper() == "#FFFFFF":
        return _TEAM_COLOR_FALLBACKS.get(entry.get("name"), "#FFFFFF")
    return color


def _lighten(hex_color, amount=0.2):
    """Mix a hex colour toward white by `amount` (0-1), used for the second-listed
    driver of a team so teammates are visually distinguishable."""
    hex_color = hex_color.lstrip("#")
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    r = round(r + (255 - r) * amount)
    g = round(g + (255 - g) * amount)
    b = round(b + (255 - b) * amount)
    return "#{:02X}{:02X}{:02X}".format(r, g, b)


def _team_color_for_year(team, year):
    teams_data = _load_teams_data()
    entries = teams_data.get(str(year))
    if not entries:
        return None

    team_norm = team.strip().lower()
    for entry in entries:
        candidates = [entry.get("name"), entry.get("alt_name"), entry.get("short_name")]
        candidates = [c.lower() for c in candidates if c]
        if team_norm in candidates:
            return _resolve_team_color(entry)
    return None


def _legacy_team_color(team):
    """The original hardcoded alias lookup, returning None (instead of white) when
    nothing matches so callers can decide what to try next."""
    team_aliases = {
        "Alpine": ["alpine", "alp"],
        "Aston Martin": ["aston martin", "am", "aston"],
        "Ferrari": ["ferrari", "fer"],
        "Haas": ["haas", "has"],
        "Kick Sauber": ["kick sauber", "sauber", "kick"],
        "McLaren": ["mclaren", "mcl"],
        "Mercedes": ["mercedes", "merc", "mer"],
        "Racing Bulls": ["racing bulls", "rb", "racingbulls", "visa cash app rb", "vcarb"],
        "Red Bull Racing": ["red bull racing", "redbull", "rbr"],
        "Williams": ["williams", "wil"]
    }

    team_norm = team.lower().strip()

    for official_name, aliases in team_aliases.items():
        if team_norm in aliases:
            return team_colors[official_name]

    return None


def get_team_color(team, year=None):
    if year is not None:
        color = _team_color_for_year(team, year)
        return legible_on_dark(color) if color is not None else "#FFFFFF"

    color = _legacy_team_color(team)
    if color is not None:
        return color

    latest_year = _latest_teams_year()
    if latest_year is not None:
        color = _team_color_for_year(team, latest_year)
        if color is not None:
            return color

    return "#FFFFFF"


_driver_aliases = {
    "Hamilton": ["HAM", "Hamilton"],
    "Leclerc": ["LEC", "Leclerc"],
    "Verstappen": ["VER", "Verstappen"],
    "Lawson": ["LAW", "Lawson"],
    "Russell": ["RUS", "Russell"],
    "Antonelli": ["ANT", "Antonelli"],
    "Norris": ["NOR", "Norris"],
    "Piastri": ["PIA", "Piastri"],
    "Stroll": ["STR", "Stroll"],
    "Alonso": ["ALO", "Alonso"],
    "Hulkenberg": ["HUL", "Hulkenberg"],
    "Bortoleto": ["BOR", "Bortoleto"],
    "Tsunoda": ["TSU", "Tsunoda"],
    "Hadjar": ["HAD", "Hadjar"],
    "Ocon": ["OCO", "Ocon"],
    "Bearman": ["BEA", "Bearman"],
    "Gasly": ["GAS", "Gasly"],
    "Doohan": ["DOO", "Doohan"],
    "Albon": ["ALB", "Albon"],
    "Sainz": ["SAI", "Sainz"],
    "Colapinto": ["COL", "Colapinto"]
}

# Updated driver colors to match their 2025 teams correctly
_driver_colors = {
    "Hamilton": "#E80020",        # Ferrari - Red
    "Leclerc": "#DC143C",         # Ferrari - Darker Red
    "Verstappen": "#3671C6",      # Red Bull - Blue
    "Tsunoda": "#4A79CC",          # Red Bull - Lighter Blue
    "Russell": "#27F4D2",         # Mercedes - Teal
    "Antonelli": "#00D2BE",       # Mercedes - Darker Teal
    "Norris": "#FF8000",          # McLaren - Orange
    "Piastri": "#FF9500",         # McLaren - Lighter Orange
    "Stroll": "#229971",          # Aston Martin - Green
    "Alonso": "#2BB885",          # Aston Martin - Lighter Green
    "Hulkenberg": "#52E252",      # Kick Sauber - Green
    "Bortoleto": "#6BE66B",       # Kick Sauber - Lighter Green
    "Lawson": "#6692FF",         # Racing Bulls - Blue
    "Hadjar": "#8AA8FF",          # Racing Bulls - Lighter Blue
    "Colapinto": "#0093CC",            # Alpine - Blue
    "Gasly": "#33A3D1",           # Alpine - Lighter Blue
    "Bearman": "#B6BABD",         # Haas - Silver/Grey
    "Ocon": "#C5C9CC",          # Haas - Lighter Grey
    "Albon": "#64C4FF",           # Williams - Light Blue
    "Sainz": "#7AC8FF",           # Williams - Lighter Blue
    "Doohan": "#B6BABD"          # Haas - Silver/Grey
}


def _legacy_driver_color(driver):
    """The original hardcoded alias lookup, returning None (instead of white) when
    nothing matches so callers can decide what to try next."""
    driver_norm = driver.lower().strip()

    for official_name, aliases in _driver_aliases.items():
        if driver_norm in [alias.lower() for alias in aliases]:
            return _driver_colors.get(official_name)

    return None


def _normalize_to_tla(driver):
    """Resolve a driver argument (a 3-letter TLA or a known full surname) to its TLA."""
    driver_stripped = driver.strip()
    if len(driver_stripped) == 3 and driver_stripped.isalpha():
        return driver_stripped.upper()

    driver_norm = driver_stripped.lower()
    for aliases in _driver_aliases.values():
        if driver_norm in [alias.lower() for alias in aliases]:
            return aliases[0]

    return None


def _driver_color_for_year(driver, year):
    tla = _normalize_to_tla(driver)
    if tla is None:
        return None

    teams_data = _load_teams_data()
    entries = teams_data.get(str(year))
    if not entries:
        return None

    for entry in entries:
        drivers_list = entry.get("drivers", [])
        if tla in drivers_list:
            base_color = _resolve_team_color(entry)
            if drivers_list.index(tla) >= 1:
                return _lighten(base_color)
            return base_color

    return None


def get_driver_color(driver, year=None):
    if year is not None:
        color = _driver_color_for_year(driver, year)
        return legible_on_dark(color) if color is not None else "#FFFFFF"

    color = _legacy_driver_color(driver)
    if color is not None:
        return color

    latest_year = _latest_teams_year()
    if latest_year is not None:
        color = _driver_color_for_year(driver, latest_year)
        if color is not None:
            return color

    # If the driver is not found the color will be white
    return "#FFFFFF"


# ---------------------------------------------------------------------------
# Contrast helpers (social formats). The year-aware lookups above lift colours
# that vanish on the dark canvas; the legacy (year=None) lookups are untouched
# so the website's renders do not change.
# ---------------------------------------------------------------------------
# Relative luminance below which a colour disappears on the #0d0d0d canvas;
# only Cadillac's #42423e (~0.05) is under it -- Ferrari red and Red Bull blue sit just above.
MIN_LUMINANCE = 0.16

# CIE76 distance under which two traces read as "the same colour" on a phone.
# A team colour lightened 20-55 % lands at 7-25, which is the teammate problem.
MIN_PAIR_DELTA_E = 40.0

# The second colour of each livery, for the second driver of a head-to-head
# between teammates: chosen to contrast with the primary on a dark canvas
# *and* for red/green colour blindness (hue and lightness both differ).
TEAMMATE_ALT_COLORS = {
    "mercedes": "#E8ECEF",          # silver
    "ferrari": "#FFD500",           # yellow
    "mclaren": "#47C7FC",           # blue
    "red bull racing": "#FFC906",   # yellow
    "racing bulls": "#F2F2F2",      # white
    "aston martin": "#CEDC00",      # lime
    "alpine": "#FF87BC",            # pink
    "williams": "#F2F2F2",          # white
    "haas": "#E6002B",              # red
    "audi": "#C7CCD1",              # titanium
    "kick sauber": "#F2F2F2",       # white
    "cadillac": "#D4AF37",          # gold
}

# Used when a team has no entry above, or its alt is still too close.
CONTRAST_FALLBACKS = ("#F2F2F2", "#FFD500", "#FF87BC", "#47C7FC", "#B388FF")


def _to_rgb01(color):
    h = (color or "").strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return None
    try:
        return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        return None


def _to_hex(rgb):
    return "#{:02X}{:02X}{:02X}".format(*(max(0, min(255, round(c * 255))) for c in rgb))


def _linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(color):
    """WCAG relative luminance (0 black .. 1 white); ``None`` if unparsable."""
    rgb = _to_rgb01(color)
    if rgb is None:
        return None
    r, g, b = (_linear(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def legible_on_dark(color, floor=MIN_LUMINANCE):
    """Mix a too-dark colour toward white until it reads on the dark canvas.

    Anything already bright enough (or unparsable) is returned unchanged.
    """
    rgb = _to_rgb01(color)
    if rgb is None or relative_luminance(color) >= floor:
        return color
    lo, hi = 0.0, 1.0
    for _ in range(20):
        mid = (lo + hi) / 2
        mixed = _to_hex(tuple(c + (1.0 - c) * mid for c in rgb))
        if relative_luminance(mixed) >= floor:
            hi = mid
        else:
            lo = mid
    return _to_hex(tuple(c + (1.0 - c) * hi for c in rgb))


def _lab(color):
    rgb = _to_rgb01(color)
    if rgb is None:
        return None
    r, g, b = (_linear(c) for c in rgb)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t):
        return t ** (1.0 / 3.0) if t > 0.008856 else 7.787 * t + 16.0 / 116.0

    fx, fy, fz = f(x), f(y), f(z)
    return 116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz)


def color_distance(a, b):
    """Perceptual (CIE76) distance between two hex colours; ``inf`` if either does not parse."""
    la, lb = _lab(a), _lab(b)
    if la is None or lb is None:
        return float("inf")
    return sum((p - q) ** 2 for p, q in zip(la, lb)) ** 0.5


def _team_key(team):
    """Canonical lower-case team name via teams.json (any year) or the legacy aliases."""
    norm = (team or "").strip().lower()
    if not norm:
        return ""
    for entries in _load_teams_data().values():
        for entry in entries:
            names = [entry.get("name"), entry.get("alt_name"), entry.get("short_name")]
            if norm in [n.lower() for n in names if n]:
                return (entry.get("name") or norm).lower()
    for official in ("Red Bull Racing", "Racing Bulls", "Kick Sauber", "Aston Martin"):
        if _legacy_team_color(norm) == team_colors[official]:
            return official.lower()
    return norm


def teammate_alt_color(team):
    """The livery's second colour for ``team`` (``None`` if unknown)."""
    return TEAMMATE_ALT_COLORS.get(_team_key(team))


def contrast_color(reference, candidate, team=None, min_delta_e=MIN_PAIR_DELTA_E):
    """``candidate`` if it is clearly distinct from ``reference``, otherwise a replacement.

    The replacement is ``team``'s second livery colour when that is distinct
    enough, else the most distant of a small fixed palette. Used for the second
    line of a two-driver chart so teammates (or two red teams) stay apart.
    """
    if color_distance(reference, candidate) >= min_delta_e:
        return candidate
    alt = teammate_alt_color(team) if team else None
    if alt and color_distance(reference, alt) >= min_delta_e:
        return alt
    return max(CONTRAST_FALLBACKS, key=lambda c: color_distance(reference, c))


def get_driver_team(driver, year):
    """Team name for a driver TLA in ``year`` from teams.json (``None`` if unknown)."""
    tla = _normalize_to_tla(driver or "")
    for entry in _load_teams_data().get(str(year), []) if tla else []:
        if tla in entry.get("drivers", []):
            return entry.get("name")
    return None


def pair_colors(color_a, color_b, team_b=None):
    """Colours for a two-driver chart: A keeps its colour, B is made distinct."""
    a = legible_on_dark(color_a)
    return a, contrast_color(a, legible_on_dark(color_b), team_b)


compound_colors = {
    "SOFT": "#da291c",
    "MEDIUM": "#ffd12e",
    "HARD": "#f0f0ec",
    "INTERMEDIATE": "#43b02a",
    "WET": "#0067ad",
    "UNKNOWN": "#777777",
}


def get_compound_color(compound: str) -> str:
    if not compound:
        return compound_colors["UNKNOWN"]
    return compound_colors.get(compound.upper(), compound_colors["UNKNOWN"])

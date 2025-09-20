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

def get_team_color(team):

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



    team = team.lower().strip()

    for official_name, aliases in team_aliases.items():
        if team in aliases:
            return team_colors[official_name]

    return "#FFFFFF"


def get_driver_color(driver):
    driver_aliases = {
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
    driver_colors = {
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
        "Albon": "#64C4FF"        # Williams backup - Light Blue
    }

    driver = driver.lower().strip()

    for official_name, aliases in driver_aliases.items():
        if driver in [alias.lower() for alias in aliases]:
            return driver_colors.get(official_name, "#FFFFFF")

    # If the driver is not found the color will be white
    return "#FFFFFF"

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


    driver_colors = {
        "Hamilton": "#E80020",
        "Leclerc": "#b0041d",
        "Verstappen": "#3671C6",
        "Lawson": "#1e63c7",
        "Russell": "#27F4D2",
        "Antonelli": "#0dbda0",
        "Norris": "#FF8000",
        "Piastri": "#cc6804",
        "Stroll": "#229971",
        "Alonso": "#165c44",
        "Hulkenberg": "#52E252",
        "Bortoleto": "#2fb52f",
        "Tsunoda": "#6692FF",
        "Hadjar": "#4161b0",
        "Ocon": "#B6BABD",
        "Bearman": "#898c8f",
        "Gasly": "#0093CC",
        "Colapinto": "#026d96",
        "Albon": "#64C4FF",
        "Sainz": "#387ca6"
    }

    driver = driver.lower().strip()
    # print(f"Driver search: '{driver}'")

    for official_name, aliases in driver_aliases.items():
        # print(f"Checking {official_name}: {aliases}")
        if driver in [alias.lower() for alias in aliases]:
            # print(f"Found match: {official_name}, returning {driver_colors[official_name]}")
            return driver_colors[official_name]

    # If the driver is not found the color will be white
    # print("No match found, returning #FFFFFF")
    return "#FFFFFF"

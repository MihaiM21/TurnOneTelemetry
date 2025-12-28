"""
Populate seasons_data collection with F1 season information
This script extracts data from teamColorPicker.py and stores it in MongoDB
"""

import traceback
from src.utils.database.seasons_manager import SeasonsDataManager
from src.utils.database.mongo import MongoDBManager
from src.static_data.f1_2025_data import f1_2025_races_data
from src.static_data.f1_2026_data import f1_2026_races_data

def populate_2026_season():
    """Populate 2025 season data"""

    # 2026 Teams
    teams_2026 = [
        {
            "name": "Red Bull Racing",
            "color": "#3671C6",
            "drivers": ["VER", "HAD"]
        },
        {
            "name": "Ferrari",
            "color": "#E80020",
            "drivers": ["HAM", "LEC"]
        },
        {
            "name": "Mercedes",
            "color": "#27F4D2",
            "drivers": ["RUS", "ANT"]
        },
        {
            "name": "McLaren",
            "color": "#FF8000",
            "drivers": ["NOR", "PIA"]
        },
        {
            "name": "Aston Martin",
            "color": "#229971",
            "drivers": ["ALO", "STR"]
        },
        {
            "name": "Alpine",
            "color": "#0093CC",
            "drivers": ["GAS", "COL"]
        },
        {
            "name": "Williams",
            "color": "#64C4FF",
            "drivers": ["ALB", "SAI"]
        },
        {
            "name": "Racing Bulls",
            "color": "#6692FF",
            "drivers": ["LAW", "LIN"]
        },
        {
            "name": "Audi",
            "color": "#ff5226",
            "drivers": ["HUL", "BOR"]
        },
        {
            "name": "Haas",
            "color": "#B6BABD",
            "drivers": ["BEA", "OCO"]
        },
        {
            "name": "Cadillac",
            "color": "#42423e",
            "drivers": ["BOT", "PER"]
        }
    ]

    # 2026 Drivers
    drivers_2026 = [
        # Red Bull Racing
        {
            "code": "VER",
            "name": "Verstappen",
            "full_name": "Max Verstappen",
            "team": "Red Bull Racing",
            "color": "#3671C6",
            "number": 3
        },
        {
            "code": "HAD",
            "name": "Hadjar",
            "full_name": "Isack Hadjar",
            "team": "Red Bull Racing",
            "color": "#4A79CC",
            "number": 48
        },
        # Ferrari
        {
            "code": "HAM",
            "name": "Hamilton",
            "full_name": "Lewis Hamilton",
            "team": "Ferrari",
            "color": "#E80020",
            "number": 44
        },
        {
            "code": "LEC",
            "name": "Leclerc",
            "full_name": "Charles Leclerc",
            "team": "Ferrari",
            "color": "#DC143C",
            "number": 16
        },
        # Mercedes
        {
            "code": "RUS",
            "name": "Russell",
            "full_name": "George Russell",
            "team": "Mercedes",
            "color": "#27F4D2",
            "number": 63
        },
        {
            "code": "ANT",
            "name": "Antonelli",
            "full_name": "Andrea Kimi Antonelli",
            "team": "Mercedes",
            "color": "#00D2BE",
            "number": 12
        },
        # McLaren
        {
            "code": "NOR",
            "name": "Norris",
            "full_name": "Lando Norris",
            "team": "McLaren",
            "color": "#FF8000",
            "number": 4
        },
        {
            "code": "PIA",
            "name": "Piastri",
            "full_name": "Oscar Piastri",
            "team": "McLaren",
            "color": "#FF9500",
            "number": 81
        },
        # Aston Martin
        {
            "code": "ALO",
            "name": "Alonso",
            "full_name": "Fernando Alonso",
            "team": "Aston Martin",
            "color": "#2BB885",
            "number": 14
        },
        {
            "code": "STR",
            "name": "Stroll",
            "full_name": "Lance Stroll",
            "team": "Aston Martin",
            "color": "#229971",
            "number": 18
        },
        # Alpine
        {
            "code": "GAS",
            "name": "Gasly",
            "full_name": "Pierre Gasly",
            "team": "Alpine",
            "color": "#33A3D1",
            "number": 10
        },
        {
            "code": "COL",
            "name": "Colapinto",
            "full_name": "Franco Colapinto",
            "team": "Alpine",
            "color": "#0093CC",
            "number": 43
        },
        # Williams
        {
            "code": "ALB",
            "name": "Albon",
            "full_name": "Alexander Albon",
            "team": "Williams",
            "color": "#64C4FF",
            "number": 23
        },
        {
            "code": "SAI",
            "name": "Sainz",
            "full_name": "Carlos Sainz",
            "team": "Williams",
            "color": "#7AC8FF",
            "number": 55
        },
        # Racing Bulls
        {
            "code": "LAW",
            "name": "Lawson",
            "full_name": "Liam Lawson",
            "team": "Racing Bulls",
            "color": "#6692FF",
            "number": 30
        },
        {
            "code": "LIN",
            "name": "Lindblad",
            "full_name": "Arvin Lindblad",
            "team": "Racing Bulls",
            "color": "#8AA8FF",
            "number": 41
        },
        # Audi
        {
            "code": "HUL",
            "name": "Hulkenberg",
            "full_name": "Nico Hulkenberg",
            "team": "Audi",
            "color": "#de3f16",
            "number": 27
        },
        {
            "code": "BOR",
            "name": "Bortoleto",
            "full_name": "Gabriel Bortoleto",
            "team": "Audi",
            "color": "#ff5226",
            "number": 5
        },
        # Haas
        {
            "code": "BEA",
            "name": "Bearman",
            "full_name": "Oliver Bearman",
            "team": "Haas",
            "color": "#B6BABD",
            "number": 87
        },
        {
            "code": "OCO",
            "name": "Ocon",
            "full_name": "Esteban Ocon",
            "team": "Haas",
            "color": "#C5C9CC",
            "number": 31
        },
        {
            "code": "BOT",
            "name": "Bottas",
            "full_name": "Valtteri Bottas",
            "team": "Cadillac",
            "color": "#42423e",
            "number": 77
        },
        {
            "code": "PER",
            "name": "Perez",
            "full_name": "Sergio Perez",
            "team": "Cadillac",
            "color": "#5c5b57",
            "number": 11
        }
    ]

    return drivers_2026, teams_2026

def populate_2025_season():
    """Populate 2025 season data"""

    # 2025 Teams
    teams_2025 = [
        {
            "name": "Red Bull Racing",
            "color": "#3671C6",
            "drivers": ["VER", "TSU"]
        },
        {
            "name": "Ferrari",
            "color": "#E80020",
            "drivers": ["HAM", "LEC"]
        },
        {
            "name": "Mercedes",
            "color": "#27F4D2",
            "drivers": ["RUS", "ANT"]
        },
        {
            "name": "McLaren",
            "color": "#FF8000",
            "drivers": ["NOR", "PIA"]
        },
        {
            "name": "Aston Martin",
            "color": "#229971",
            "drivers": ["ALO", "STR"]
        },
        {
            "name": "Alpine",
            "color": "#0093CC",
            "drivers": ["GAS", "COL"]
        },
        {
            "name": "Williams",
            "color": "#64C4FF",
            "drivers": ["ALB", "SAI"]
        },
        {
            "name": "Racing Bulls",
            "color": "#6692FF",
            "drivers": ["LAW", "HAD"]
        },
        {
            "name": "Kick Sauber",
            "color": "#52E252",
            "drivers": ["HUL", "BOR"]
        },
        {
            "name": "Haas",
            "color": "#B6BABD",
            "drivers": ["BEA", "OCO"]
        }
    ]

    # 2025 Drivers
    drivers_2025 = [
        # Red Bull Racing
        {
            "code": "VER",
            "name": "Verstappen",
            "full_name": "Max Verstappen",
            "team": "Red Bull Racing",
            "color": "#3671C6",
            "number": 1
        },
        {
            "code": "TSU",
            "name": "Tsunoda",
            "full_name": "Yuki Tsunoda",
            "team": "Red Bull Racing",
            "color": "#4A79CC",
            "number": 22
        },
        # Ferrari
        {
            "code": "HAM",
            "name": "Hamilton",
            "full_name": "Lewis Hamilton",
            "team": "Ferrari",
            "color": "#E80020",
            "number": 44
        },
        {
            "code": "LEC",
            "name": "Leclerc",
            "full_name": "Charles Leclerc",
            "team": "Ferrari",
            "color": "#DC143C",
            "number": 16
        },
        # Mercedes
        {
            "code": "RUS",
            "name": "Russell",
            "full_name": "George Russell",
            "team": "Mercedes",
            "color": "#27F4D2",
            "number": 63
        },
        {
            "code": "ANT",
            "name": "Antonelli",
            "full_name": "Andrea Kimi Antonelli",
            "team": "Mercedes",
            "color": "#00D2BE",
            "number": 12
        },
        # McLaren
        {
            "code": "NOR",
            "name": "Norris",
            "full_name": "Lando Norris",
            "team": "McLaren",
            "color": "#FF8000",
            "number": 4
        },
        {
            "code": "PIA",
            "name": "Piastri",
            "full_name": "Oscar Piastri",
            "team": "McLaren",
            "color": "#FF9500",
            "number": 81
        },
        # Aston Martin
        {
            "code": "ALO",
            "name": "Alonso",
            "full_name": "Fernando Alonso",
            "team": "Aston Martin",
            "color": "#2BB885",
            "number": 14
        },
        {
            "code": "STR",
            "name": "Stroll",
            "full_name": "Lance Stroll",
            "team": "Aston Martin",
            "color": "#229971",
            "number": 18
        },
        # Alpine
        {
            "code": "GAS",
            "name": "Gasly",
            "full_name": "Pierre Gasly",
            "team": "Alpine",
            "color": "#33A3D1",
            "number": 10
        },
        {
            "code": "COL",
            "name": "Colapinto",
            "full_name": "Franco Colapinto",
            "team": "Alpine",
            "color": "#0093CC",
            "number": 43
        },
        # Williams
        {
            "code": "ALB",
            "name": "Albon",
            "full_name": "Alexander Albon",
            "team": "Williams",
            "color": "#64C4FF",
            "number": 23
        },
        {
            "code": "SAI",
            "name": "Sainz",
            "full_name": "Carlos Sainz",
            "team": "Williams",
            "color": "#7AC8FF",
            "number": 55
        },
        # Racing Bulls
        {
            "code": "LAW",
            "name": "Lawson",
            "full_name": "Liam Lawson",
            "team": "Racing Bulls",
            "color": "#6692FF",
            "number": 30
        },
        {
            "code": "HAD",
            "name": "Hadjar",
            "full_name": "Isack Hadjar",
            "team": "Racing Bulls",
            "color": "#8AA8FF",
            "number": 48
        },
        # Kick Sauber
        {
            "code": "HUL",
            "name": "Hulkenberg",
            "full_name": "Nico Hulkenberg",
            "team": "Kick Sauber",
            "color": "#52E252",
            "number": 27
        },
        {
            "code": "BOR",
            "name": "Bortoleto",
            "full_name": "Gabriel Bortoleto",
            "team": "Kick Sauber",
            "color": "#6BE66B",
            "number": 5
        },
        # Haas
        {
            "code": "BEA",
            "name": "Bearman",
            "full_name": "Oliver Bearman",
            "team": "Haas",
            "color": "#B6BABD",
            "number": 87
        },
        {
            "code": "OCO",
            "name": "Ocon",
            "full_name": "Esteban Ocon",
            "team": "Haas",
            "color": "#C5C9CC",
            "number": 31
        },
        {
            "code": "DOO",
            "name": "Doohan",
            "full_name": "Jack Doohan",
            "team": "Alpine",
            "color": "#B6BABD",
            "number": 7
        }
    ]

    return drivers_2025, teams_2025

def populate_2024_season():
    """Populate 2024 season data"""

    # 2024 Teams
    teams_2024 = [
        {
            "name": "Red Bull Racing",
            "color": "#3671C6",
            "drivers": ["VER", "PER"]
        },
        {
            "name": "Ferrari",
            "color": "#E80020",
            "drivers": ["LEC", "SAI"]
        },
        {
            "name": "Mercedes",
            "color": "#27F4D2",
            "drivers": ["HAM", "RUS"]
        },
        {
            "name": "McLaren",
            "color": "#FF8000",
            "drivers": ["NOR", "PIA"]
        },
        {
            "name": "Aston Martin",
            "color": "#229971",
            "drivers": ["ALO", "STR"]
        },
        {
            "name": "Alpine",
            "color": "#0093CC",
            "drivers": ["GAS", "OCO"]
        },
        {
            "name": "Williams",
            "color": "#64C4FF",
            "drivers": ["ALB", "SAR"]
        },
        {
            "name": "Racing Bulls",
            "color": "#6692FF",
            "drivers": ["RIC", "TSU"]
        },
        {
            "name": "Kick Sauber",
            "color": "#52E252",
            "drivers": ["BOT", "ZHO"]
        },
        {
            "name": "Haas",
            "color": "#B6BABD",
            "drivers": ["MAG", "HUL"]
        }
    ]

    # 2024 Drivers
    drivers_2024 = [
        {
            "code": "VER",
            "name": "Verstappen",
            "full_name": "Max Verstappen",
            "team": "Red Bull Racing",
            "color": "#3671C6",
            "number": 1
        },
        {
            "code": "PER",
            "name": "Perez",
            "full_name": "Sergio Perez",
            "team": "Red Bull Racing",
            "color": "#4A79CC",
            "number": 11
        },
        {
            "code": "LEC",
            "name": "Leclerc",
            "full_name": "Charles Leclerc",
            "team": "Ferrari",
            "color": "#E80020",
            "number": 16
        },
        {
            "code": "SAI",
            "name": "Sainz",
            "full_name": "Carlos Sainz",
            "team": "Ferrari",
            "color": "#DC143C",
            "number": 55
        },
        {
            "code": "HAM",
            "name": "Hamilton",
            "full_name": "Lewis Hamilton",
            "team": "Mercedes",
            "color": "#27F4D2",
            "number": 44
        },
        {
            "code": "RUS",
            "name": "Russell",
            "full_name": "George Russell",
            "team": "Mercedes",
            "color": "#00D2BE",
            "number": 63
        },
        {
            "code": "NOR",
            "name": "Norris",
            "full_name": "Lando Norris",
            "team": "McLaren",
            "color": "#FF8000",
            "number": 4
        },
        {
            "code": "PIA",
            "name": "Piastri",
            "full_name": "Oscar Piastri",
            "team": "McLaren",
            "color": "#FF9500",
            "number": 81
        },
        {
            "code": "ALO",
            "name": "Alonso",
            "full_name": "Fernando Alonso",
            "team": "Aston Martin",
            "color": "#229971",
            "number": 14
        },
        {
            "code": "STR",
            "name": "Stroll",
            "full_name": "Lance Stroll",
            "team": "Aston Martin",
            "color": "#2BB885",
            "number": 18
        },
        {
            "code": "GAS",
            "name": "Gasly",
            "full_name": "Pierre Gasly",
            "team": "Alpine",
            "color": "#0093CC",
            "number": 10
        },
        {
            "code": "OCO",
            "name": "Ocon",
            "full_name": "Esteban Ocon",
            "team": "Alpine",
            "color": "#33A3D1",
            "number": 31
        },
        {
            "code": "ALB",
            "name": "Albon",
            "full_name": "Alexander Albon",
            "team": "Williams",
            "color": "#64C4FF",
            "number": 23
        },
        {
            "code": "SAR",
            "name": "Sargeant",
            "full_name": "Logan Sargeant",
            "team": "Williams",
            "color": "#7AC8FF",
            "number": 2
        },
        {
            "code": "RIC",
            "name": "Ricciardo",
            "full_name": "Daniel Ricciardo",
            "team": "Racing Bulls",
            "color": "#6692FF",
            "number": 3
        },
        {
            "code": "TSU",
            "name": "Tsunoda",
            "full_name": "Yuki Tsunoda",
            "team": "Racing Bulls",
            "color": "#8AA8FF",
            "number": 22
        },
        {
            "code": "BOT",
            "name": "Bottas",
            "full_name": "Valtteri Bottas",
            "team": "Kick Sauber",
            "color": "#52E252",
            "number": 77
        },
        {
            "code": "ZHO",
            "name": "Zhou",
            "full_name": "Zhou Guanyu",
            "team": "Kick Sauber",
            "color": "#6BE66B",
            "number": 24
        },
        {
            "code": "MAG",
            "name": "Magnussen",
            "full_name": "Kevin Magnussen",
            "team": "Haas",
            "color": "#B6BABD",
            "number": 20
        },
        {
            "code": "HUL",
            "name": "Hulkenberg",
            "full_name": "Nico Hulkenberg",
            "team": "Haas",
            "color": "#C5C9CC",
            "number": 27
        }
    ]

    return drivers_2024, teams_2024


def main():
    """Main function to populate seasons data"""

    print("=" * 60)
    print("Populating Seasons Data Collection")
    print("=" * 60)

    # Initialize manager
    seasons_manager = SeasonsDataManager()

    # Populate 2026 season
    print("\n1. Populating 2026 Season Data...")
    drivers_2026, teams_2026 = populate_2026_season()
    success_2026 = seasons_manager.create_season_document(2026, drivers_2026, teams_2026, f1_2026_races_data)

    if success_2026:
        print(f"   ✓ Added {len(drivers_2026)} drivers")
        print(f"   ✓ Added {len(teams_2026)} teams")
        print(f"   ✓ Added {len(f1_2026_races_data)} races")

    # Populate 2025 season
    print("\n2. Populating 2025 Season Data...")
    drivers_2025, teams_2025 = populate_2025_season()
    success_2025 = seasons_manager.create_season_document(2025, drivers_2025, teams_2025, f1_2025_races_data)

    if success_2025:
        print(f"   ✓ Added {len(drivers_2025)} drivers")
        print(f"   ✓ Added {len(teams_2025)} teams")
        print(f"   ✓ Added {len(f1_2025_races_data)} races")

    # Populate 2024 season
    print("\n3. Populating 2024 Season Data...")
    drivers_2024, teams_2024 = populate_2024_season()
    success_2024 = seasons_manager.create_season_document(2024, drivers_2024, teams_2024)

    if success_2024:
        print(f"   ✓ Added {len(drivers_2024)} drivers")
        print(f"   ✓ Added {len(teams_2024)} teams")
        print(f"   ✓ No race data available for 2024")

    # Verify data
    print("\n4. Verifying Data...")
    available_seasons = seasons_manager.list_all_seasons()
    print(f"   Available seasons: {available_seasons}")

    # Test retrieval
    print("\n5. Testing Data Retrieval...")
    for year in available_seasons:
        drivers = seasons_manager.get_drivers(year)
        teams = seasons_manager.get_teams(year)
        print(f"   Year {year}: {len(drivers)} drivers, {len(teams)} teams")

        # Show sample driver
        if drivers:
            sample_driver = drivers[0]
            print(f"      Sample driver: {sample_driver['full_name']} ({sample_driver['code']}) - {sample_driver['team']}")

        # Show sample team
        if teams:
            sample_team = teams[0]
            print(f"      Sample team: {sample_team['name']} - Drivers: {', '.join(sample_team['drivers'])}")

    print("\n" + "=" * 60)
    print("Seasons Data Population Complete!")
    print("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n✗ Population failed with error: {e}")
        import traceback
        traceback.print_exc()


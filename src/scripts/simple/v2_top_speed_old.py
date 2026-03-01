import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
import json
from typing import Dict, List, Tuple, Optional
from collections import defaultdict

from src.utils import dirOrg
from src.utils import setup_theme
from src.utils.teamColorPicker import team_colors, teams
from src.utils.database.mongo_helper import store_plot_data_to_mongo, get_plot_data_from_mongo
from src.data_loader.f1_static_client import F1StaticClient


def _init(y: int, event_name: str, session_name: str) -> Tuple[str, str, str]:
    """
    Initialize paths and filenames for output files.
    """
    event_folder = event_name.replace(' ', '')
    dirOrg.checkForFolder(f"{y}/{event_folder}/{session_name}")
    location = f"outputs/plots/{y}/{event_folder}/{session_name}"
    name = f'Top speed comparison {y} {event_name} {session_name}.png'
    name_json = name.replace("png", "json")
    return location, name, name_json


def get_driver_team_mapping(base_url: str, client: F1StaticClient) -> Dict[str, str]:
    """
    Get mapping of driver numbers to team names from DriverList.json
    """
    try:
        driver_list_url = base_url + "DriverList.json"
        response = client.session.get(driver_list_url)
        response.raise_for_status()
        
        drivers = json.loads(response.content.decode('utf-8-sig'))
        
        # Build driver number -> team mapping
        driver_to_team = {}
        for driver_num, driver_info in drivers.items():
            team_name = driver_info.get('TeamName', 'Unknown')
            driver_to_team[driver_num] = team_name
        
        return driver_to_team
        
    except Exception as e:
        print(f"Warning: Could not fetch driver list: {e}")
        return {}


# ============================================================================
# VARIANTA 1: TELEMETRIE (CarData) - Codul tau original
# ============================================================================
def extract_top_speeds_from_telemetry(
    base_url: str, 
    client: F1StaticClient,
    driver_to_team: Dict[str, str]
) -> Dict[str, float]:
    """
    Extrage viteza maximă absolută atinsă oriunde pe circuit din CarData.z.jsonStream
    """
    car_data_url = base_url + "CarData.z.jsonStream"
    
    print(f"\n[SURSA 1] Extragere date Telemetrie (CarData): {car_data_url}")
    print("... procesare stream comprimat (poate dura 10-20 secunde) ...")
    
    try:
        telemetry_entries = client.parse_compressed_stream(car_data_url)
        
        team_max_speeds = defaultdict(lambda: 0)
        
        for entry in telemetry_entries:
            entries_list = entry.get('Entries', [])
            
            # Normalizare: uneori Entries e listă, alteori e un singur obiect
            if not isinstance(entries_list, list):
                entries_list = [entries_list]

            for item in entries_list:
                cars = item.get('Cars', {})
                for driver_num, driver_data in cars.items():
                    channels = driver_data.get('Channels', {})
                    speed_kmh = channels.get('2', 0)  # Canalul 2 = Viteză
                    
                    if speed_kmh and speed_kmh > 0:
                        if driver_num in driver_to_team:
                            team = driver_to_team[driver_num]
                            if speed_kmh > team_max_speeds[team]:
                                team_max_speeds[team] = speed_kmh
        
        return dict(team_max_speeds)
        
    except Exception as e:
        print(f"Eroare la extragerea telemetriei: {e}")
        return {}


# ============================================================================
# VARIANTA 2: SPEED TRAP (TimingData) - Varianta Noua
# ============================================================================
def extract_top_speeds_from_speed_trap(
    base_url: str,
    client: F1StaticClient,
    driver_to_team: Dict[str, str]
) -> Dict[str, float]:
    """
    Extrage viteza oficială de la Speed Trap (ST) din TimingData.jsonStream
    Aceasta este valoarea afisată de obicei pe grafica TV.
    """
    timing_url = base_url + "TimingData.jsonStream" # MERGE SI json si jsonStream
    print(f"\n[SURSA 2] Extragere date Speed Trap (TimingData): {timing_url}")
    
    try:
        # Folosim parser-ul simplu linie-cu-linie
        entries = client.parse_jsonstream_simple(timing_url)
        
        team_st_speeds = defaultdict(lambda: 0.0)
        
        for entry in entries:
            if 'Lines' not in entry:
                continue
                
            lines = entry['Lines']
            
            for car_number, data in lines.items():
                # Cautam structura: Lines -> Car -> Speeds -> ST (Speed Trap)
                if 'Speeds' in data and 'ST' in data['Speeds']:
                    st_data = data['Speeds']['ST']
                    
                    # Verificam daca exista valoarea
                    if 'Value' in st_data:
                        val = st_data['Value']
                        if val and val != "":
                            try:
                                speed = float(val)
                                if car_number in driver_to_team:
                                    team = driver_to_team[car_number]
                                    # Pastram maximul per echipa
                                    if speed > team_st_speeds[team]:
                                        team_st_speeds[team] = speed
                            except ValueError:
                                continue

        return dict(team_st_speeds)

    except Exception as e:
        print(f"Eroare la extragerea Speed Trap: {e}")
        return {}


# ============================================================================
# MAIN LOGIC
# ============================================================================

def TopSpeedPlot_V2(y: int, event_name: str, session_name: str, use_cache: bool = True) -> str:
    """
    Genereaza grafic si afiseaza comparatia intre cele doua surse.
    """
    client = F1StaticClient()
    
    print(f"\nFetching {y} {event_name} {session_name}...")
    base_url = client.get_event_session_url(y, event_name, session_name)
    
    if not base_url:
        raise ValueError(f"Could not find session: {y} {event_name} {session_name}")
    
    # Setup paths
    setup_theme.setup_turnone_theme()
    location, name, name_json = _init(y, event_name, session_name)
    
    # Step 1: Get mapping
    driver_to_team = get_driver_team_mapping(base_url, client)
    
    # Step 2: Extragem AMBELE variante
    
    # Varianta 1: Telemetrie (Max absolut pe circuit)
    telemetry_speeds = extract_top_speeds_from_telemetry(base_url, client, driver_to_team)
    
    # Varianta 2: Speed Trap (Oficial FIA)
    st_speeds = extract_top_speeds_from_speed_trap(base_url, client, driver_to_team)
    
    # Step 3: Afisam Comparatia (Cele doua topuri diferite)
    print("\n" + "="*80)
    print(f"COMPARATIE VITEZE MAXIME - {y} {event_name}")
    print("="*80)
    print(f"{'ECHIPA':<25} | {'TELEMETRIE (CarData)':<20} | {'SPEED TRAP (Oficial)':<20} | {'DIFERENTA':<10}")
    print("-" * 80)
    
    all_teams = set(list(telemetry_speeds.keys()) + list(st_speeds.keys()))
    
    # Pregatim datele pentru grafic (folosim Telemetria ca sursa principala pentru plot, 
    # dar salvam ambele in JSON)
    plot_data = []
    
    for team in sorted(all_teams):
        t_speed = telemetry_speeds.get(team, 0)
        st_speed = st_speeds.get(team, 0)
        diff = t_speed - st_speed
        
        print(f"{team:<25} | {int(t_speed):<20} | {int(st_speed):<20} | {diff:+.1f}")
        
        # Daca lipseste telemetria, folosim ST pentru grafic, altfel Telemetria
        final_speed_for_plot = t_speed if t_speed > 0 else st_speed
        color = team_colors.get(team, "#FFFFFF")
        
        plot_data.append({
            'Team': team,
            'Speed': final_speed_for_plot,
            'Color': color,
            'SpeedTrap_Value': st_speed,
            'Telemetry_Value': t_speed
        })
    
    print("="*80)
    print("NOTA: Telemetria (CarData) este de obicei mai mare pentru ca masoara viteza")
    print("      in orice punct, nu doar la punctul fix de radar (Speed Trap).")
    print("="*80)

    # Sortam pentru grafic (descrescator dupa viteza folosita la plot)
    plot_data.sort(key=lambda x: x['Speed'], reverse=True)
    
    teams_list = [d['Team'] for d in plot_data]
    speeds_list = [d['Speed'] for d in plot_data]
    colors_list = [d['Color'] for d in plot_data]
    
    # Step 4: Generare Plot
    print("\nGenerare grafic (folosind datele din coloana Telemetrie)...")
    _generate_plot(
        teams_list, speeds_list, colors_list,
        y, event_name, session_name,
        location, name
    )
    
    # Step 5: Salvare date extinse in JSON
    df = pd.DataFrame(plot_data)
    json_path = location + "/" + name_json
    df.to_json(json_path, orient='records')
    
    print(f"\n✓ Plot saved to: {location}/{name}")
    print(f"✓ Data (both sources) saved to: {location}/{name_json}")
    
    return location + "/" + name


def _generate_plot(
    teams_list: List[str],
    speeds_list: List[float],
    colors_list: List[str],
    year: int,
    event_name: str,
    session_name: str,
    location: str,
    name: str
):
    """
    Functia de plotare ramane neschimbata vizual
    """
    fig, ax = plt.subplots(figsize=(13, 13), layout='constrained')
    ax.bar(teams_list, speeds_list, color=colors_list)
    
    # Set Y-axis limits (dynamic)
    if speeds_list:
        y_min = min(speeds_list) - 5
        y_max = max(speeds_list) + 5
        ax.set_ylim(y_min, y_max)
    
    # Add speed labels on bars
    for i, (team, speed) in enumerate(zip(teams_list, speeds_list)):
        ax.text(team, int(speed) + 0.5, f"{int(speed)}",
                verticalalignment='bottom',
                horizontalalignment='center',
                color='white', fontsize=16, fontweight="bold")
    
    # Add watermark
    try:
        logo = mpimg.imread('lib/logo mic.png')
        fig.figimage(logo, 575, 575, zorder=3, alpha=.6)
    except Exception:
        pass
    
    # Set title
    plt.suptitle(f'Top speed comparison\n{year} {event_name} {session_name}')
    plt.tight_layout()
    
    setup_theme.add_glow(ax)
    plt.savefig(location + "/" + name)
    plt.close()


if __name__ == "__main__":
    # Test cu ambele variante
    print("Testare Top Speed cu surse duale...")
    try:
        TopSpeedPlot_V2(2023, "Italian Grand Prix", "Race")
    except Exception as e:
        print(f"Eroare: {e}")
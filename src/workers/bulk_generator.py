import tkinter as tk
import customtkinter
import fastf1 as ff1
from PIL import Image
from itertools import combinations

# Import your custom modules
from src.services.analysis.v1.top_speed import TopSpeedPlot, TopSpeedData
from src.services.analysis.v1.throttle_comparison import ThrottleComp, ThrottleCompData
from src.services.analysis.v1.qualifying_results import QualiResults, QualiResultsData
from src.services.analysis.v1.track_comparison import TrackComparisonPlot, TrackComparisonData
from src.services.analysis.v1.throttle_brake_comparison import throttle_graph, throttle_graph_data
from src.services.analysis.v1.laptimes_distribution import LatimesDistribution
from src.core.observability.analytics import SessionTracker
from src.services.orchestrator_helpers import get_latest_finished_session
from src.services.orchestrator import latest_session_analised


# MODIFY HERE
year = 2025
round = 12
event = "R"

customtkinter.set_appearance_mode("dark")
# Creating window
root = customtkinter.CTk(fg_color="#262525")
root.title("Formula One Telemetry Analysis - FOTA")
root.geometry("1280x720")


driver_aliases = {
    "Hamilton": ["HAM", "Hamilton"],
    "Leclerc": ["LEC", "Leclerc"],
    "Verstappen": ["VER", "Verstappen"],
    "Tsunoda": ["TSU", "Tsunoda"],
    "Lawson": ["LAW", "Lawson"],
    "Russell": ["RUS", "Russell"],
    "Antonelli": ["ANT", "Antonelli"],
    "Norris": ["NOR", "Norris"],
    "Piastri": ["PIA", "Piastri"],
    "Stroll": ["STR", "Stroll"],
    "Alonso": ["ALO", "Alonso"],
    "Hulkenberg": ["HUL", "Hulkenberg"],
    "Bortoleto": ["BOR", "Bortoleto"],
    "Hadjar": ["HAD", "Hadjar"],
    "Ocon": ["OCO", "Ocon"],
    "Bearman": ["BEA", "Bearman"],
    "Gasly": ["GAS", "Gasly"],
    "Doohan": ["DOO", "Doohan"],
    "Albon": ["ALB", "Albon"],
    "Sainz": ["SAI", "Sainz"],
    "Colapinto": ["COL", "Colapinto"]
}

top_driver_aliases = {
    "Hamilton": ["HAM", "Hamilton"],
    "Leclerc": ["LEC", "Leclerc"],
    "Verstappen": ["VER", "Verstappen"],
    "Tsunoda": ["TSU", "Tsunoda"],
    "Russell": ["RUS", "Russell"],
    "Antonelli": ["ANT", "Antonelli"],
    "Norris": ["NOR", "Norris"],
    "Piastri": ["PIA", "Piastri"]
}

driver_list = list(top_driver_aliases.items())
def comparisonPlots(y, r, e):
    for i in range(len(top_driver_aliases)):
        for j in range(i + 1, len(top_driver_aliases)):
            driver1 = top_driver_aliases[i]
            driver2 = top_driver_aliases[j]
            print(f"Compar {driver1} cu {driver2}")

def driverAnalysisFunction(y, r, e):
    for name, aliases in driver_aliases.items():
        driver_code = aliases[0]  # Primul element din listă (codul de 3 litere)
        try:
            driver_analysis(y, r, e, driver_code)
        except Exception as ex:
            print(f"Error processing driver {name} ({driver_code}): {ex}")
            continue


def Practice_Generator(y, r, e):
    QualiResults(y, r, e)
    stint_laptimes_simple(year, round, event)
    TopSpeedFunc(y, r, e)
    LaptimesDistributionFunc(y, r, e)
    TeamPaceRankingFunc(y, r, e)
    ThrottleComp(y, r, e)
    driverAnalysisFunction(y,r,e)
    #adaugat comparatii intre cei mai buni soferi


def Quali_Generator(y, r, e):
    QualiResults(y, r, e)
    TopSpeedFunc(y, r, e)
    LaptimesDistributionFunc(y, r, e)
    TeamPaceRankingFunc(y, r, e)
    ThrottleComp(y, r, e)
    driverAnalysisFunction(y, r, e)
    for i in range(len(driver_list)):
        name1, aliases1 = driver_list[i]
        driver_code1 = aliases1[0]

        for j in range(i + 1, len(driver_list)):
            name2, aliases2 = driver_list[j]
            driver_code2 = aliases2[0]

            TrackCompFunc(y, r, e, driver_code1, driver_code2, "test", "test")

    for i in range(len(driver_list)):
        name1, aliases1 = driver_list[i]
        driver_code1 = aliases1[0]

        for j in range(i + 1, len(driver_list)):
            name2, aliases2 = driver_list[j]
            driver_code2 = aliases2[0]

            throttle_graph(y, r, e, driver_code1, driver_code2)

    for i in range(len(driver_list)):
        name1, aliases1 = driver_list[i]
        driver_code1 = aliases1[0]

        for j in range(i + 1, len(driver_list)):
            name2, aliases2 = driver_list[j]
            driver_code2 = aliases2[0]

            SpeedTraceFunc(y, r, e, driver_code1, driver_code2)

def Race_Generator(y, r, e):
    #TopSpeedFunc(y, r, e)
    LaptimesDistributionFunc(y, r, e)
    #stint_laptimes_simple(year, round, event)
    TeamPaceRankingFunc(y, r, e)
    StrategyFunc(y, r, e)
    position_changes(y ,r ,e)
    for i in range(len(driver_list)):
        name1, aliases1 = driver_list[i]
        driver_code1 = aliases1[0]

        DriverLaptimesFunc(y, r, e, driver_code1)
    #



if __name__ == '__main__':
    if event == "FP1" or event == "FP2" or event == "FP3":
        Practice_Generator(year, round, event)
        #DriverRacePace(year, round, event)
    if event == "Q" or event == "SQ":
        Quali_Generator(year, round, event)
    if event == "R" or event == "S":
        Race_Generator(year, round, event)
import tkinter as tk
import customtkinter
import fastf1 as ff1
from PIL import Image
from itertools import combinations
from Scripts.Quali.Throttle_comparison import ThrottleComp
from Scripts.Quali.plot_qualifying_results import QualiResults
from Scripts.Quali.top_speed_plot import TopSpeedFunc
from Scripts.Race.plot_strategy import StrategyFunc
from Scripts.Quali.Track_comparison import TrackCompFunc
from Scripts.Quali.plot_speed_traces import SpeedTraceFunc
from Scripts.Race.plot_team_pace_ranking import TeamPaceRankingFunc
from Scripts.Race.plot_driver_laptimes import DriverLaptimesFunc
from Scripts.Race.plot_laptimes_distribution import LaptimesDistributionFunc
from Scripts.Throttle_graph import throttle_graph
from Scripts.Race.plot_position_changes import position_changes
from Scripts.Complex.driver_analysis import driver_analysis
from Scripts.Complex.stint_laptimes_simple import stint_laptimes_simple
from Scripts.Complex.team_race_pace import TeamRacePace
from Scripts.Complex.drivers_race_pace import DriverRacePace

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
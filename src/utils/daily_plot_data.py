import random
import json

import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.image as mpimg

from src.utils import dirOrg
from src.data_loader import data_aqcuisition
from src.utils import setup_theme
from src.utils.teamColorPicker import team_colors, teams
from src.scripts.simple.top_speed import TopSpeedData
from src.scripts.simple.throttle_comparison import ThrottleCompData


class DailyPlotData:
    def __init__(self):
        self.year = 2025
        self.round = None
        self.event = None

    def getRandomSession(self):
        # Generate a random number from 1 to 15
        self.year = 2025
        self.round = random.choice(list(range(1, 11)))
        self.event = random.choice("FP1 Q R".split())

    def generate_daily_plot(self):
        self.getRandomSession()
        # Load session using data_aqcuisition module
        topSpeedDataPath = TopSpeedData(self.year, self.round, self.event)
        throttleCompDataPath = ThrottleCompData(self.year, self.round, self.event)

        # Load JSON data from both files
        with open(topSpeedDataPath, 'r') as f1:
            topSpeedData_json = json.load(f1)
        with open(throttleCompDataPath, 'r') as f2:
            throttleCompData_json = json.load(f2)

        # Return as a dict with two keys
        merged_json = {
            'top_speed': topSpeedData_json,
            'throttle_comparison': throttleCompData_json
        }

        return merged_json


    def get_current_date(self):
        return self.date

    def get_event_name(self):
        return self.event_name
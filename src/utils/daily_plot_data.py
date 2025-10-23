import random
import json
import datetime

from src.scripts.simple.top_speed import TopSpeedData
from src.scripts.simple.throttle_comparison import ThrottleCompData


class DailyPlotData:
    STATE_PATH = 'outputs/daily_state.json'

    def __init__(self):
        self.year = 2025
        self.round = None
        self.event = None
        self.date = None
        self._load_or_generate_daily_state()

    def _load_or_generate_daily_state(self):
        today = datetime.date.today().isoformat()
        try:
            with open(self.STATE_PATH, 'r') as f:
                state = json.load(f)
            if state.get('date') == today:
                self.round = state['round']
                self.event = state['event']
                self.date = state['date']
                return
        except Exception:
            pass
        # If not today, generate new
        self.round = random.choice(list(range(1, 11)))
        self.event = random.choice("FP1 Q R".split())
        self.date = today
        state = {'date': today, 'round': self.round, 'event': self.event}
        with open(self.STATE_PATH, 'w') as f:
            json.dump(state, f)

    def getRandomSession(self):
        # No longer needed, handled by _load_or_generate_daily_state
        pass

    def generate_daily_plot(self):
        # Use the round/event already set for today
        topSpeedDataPath = TopSpeedData(self.year, self.round, self.event)
        throttleCompDataPath = ThrottleCompData(self.year, self.round, self.event)
        with open(topSpeedDataPath, 'r') as f1:
            topSpeedData_json = json.load(f1)
        with open(throttleCompDataPath, 'r') as f2:
            throttleCompData_json = json.load(f2)
        merged_json = {
            'top_speed': topSpeedData_json,
            'throttle_comparison': throttleCompData_json,
            'date': self.date,
            'round': self.round,
            'event': self.event
        }
        return merged_json

    def get_current_date(self):
        return self.date

    def get_event_name(self):
        return self.event

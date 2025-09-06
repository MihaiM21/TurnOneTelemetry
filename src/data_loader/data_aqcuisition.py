import fastf1
from fastf1 import plotting


class SessionLoader:
    def __init__(self, year, round, event):
        self.year = year
        self.round = round
        self.event = event
        plotting.setup_mpl(misc_mpl_mods=False)
        fastf1.Cache.enable_cache('./cache')
        self.session = fastf1.get_session(self.year, self.round, self.event)
        self.session.load()

    def get_session(self):
        return self.session



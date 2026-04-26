from __future__ import annotations

from pathlib import Path

from worldsim.debug import DebugLogger
from worldsim.engine import WorldEngine
from worldsim.memory import CampaignStore
from worldsim.tui import WorldSimApp


class Game:
    def __init__(self) -> None:
        data_dir = Path(__file__).resolve().parent.parent / "data"
        save_path = data_dir / "campaign.json"
        self.debug_logger = DebugLogger.create(data_dir)
        self.store = CampaignStore(save_path)
        self.engine = WorldEngine()

    def run(self) -> None:
        WorldSimApp(store=self.store, engine=self.engine, debug_logger=self.debug_logger).run()

from __future__ import annotations

import asyncio

from worldsim.engine import WorldEngine
from worldsim.memory import CampaignStore
from worldsim.tui import WorldSimApp


def test_textual_app_mounts_with_empty_store(tmp_path) -> None:
    app = WorldSimApp(
        store=CampaignStore(tmp_path / "campaign.json"),
        engine=WorldEngine(seed=7),
    )

    async def mount() -> None:
        async with app.run_test(size=(120, 40)):
            assert app.query_one("#switcher") is not None

    asyncio.run(mount())

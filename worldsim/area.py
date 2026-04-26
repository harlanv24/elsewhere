from __future__ import annotations

from worldsim.models import Biome, Location


AREA_STEPS = ["approach", "interior", "deep point"]


def area_choices(location: Location | None, biome: Biome) -> list[str]:
    if location is not None:
        templates = {
            Biome.FOREST: ["Forest Edge", "Watchfire Camp", "Old Shrine"],
            Biome.PLAIN: ["Main Road", "Commons", "Storehouse Yard"],
            Biome.HILL: ["Ridge Path", "Watchpoint", "Stone Steps"],
            Biome.MOUNTAIN: ["Cliff Gate", "Mine Mouth", "Wind Shelf"],
            Biome.SWAMP: ["Bog Walk", "Stilt Camp", "Reed Hollow"],
            Biome.WATER: ["Shoreline", "Landing", "Flooded Track"],
        }
        suffixes = templates.get(location.biome, ["Outer Ring", "Crossing", "Underworks"])
        return [f"{location.name} {suffix}" for suffix in suffixes[:3]]
    return wilderness_areas(biome)


def wilderness_areas(biome: Biome) -> list[str]:
    options = {
        Biome.FOREST: ["Game Trail", "Camp Ring", "Shadow Thicket"],
        Biome.PLAIN: ["Open Trail", "Camp Ring", "Stone Marker"],
        Biome.HILL: ["Slope Path", "Camp Ring", "Lookout Shelf"],
        Biome.MOUNTAIN: ["Rock Shelf", "Camp Ring", "Narrow Pass"],
        Biome.SWAMP: ["Reed Bank", "Camp Ring", "Mud Spur"],
        Biome.WATER: ["Shoreline", "Camp Ring", "Drift Landing"],
    }
    return options.get(biome, ["Camp Ring", "Survey Point", "Trail Break"])


def display_area(area: str | None) -> str:
    return area if area is not None else "None"


def zone_name(step: int) -> str:
    index = max(0, min(step, len(AREA_STEPS) - 1))
    return AREA_STEPS[index]


def initial_tension(location: Location | None) -> int:
    if location is None:
        return 2
    return max(1, min(6, location.danger))


def area_theme(biome: Biome) -> str:
    themes = {
        Biome.FOREST: "hidden paths and old growth pressure",
        Biome.PLAIN: "open exposure and travel-worn structures",
        Biome.HILL: "elevation, sightlines, and old stonework",
        Biome.MOUNTAIN: "thin paths, rockfall, and harsh wind",
        Biome.SWAMP: "soft footing, stagnant water, and muffled sounds",
        Biome.WATER: "slippery edges, current, and exposed crossings",
    }
    return themes[biome]


def area_hazard(biome: Biome, area_hash: int) -> str:
    hazards = {
        Biome.FOREST: ["snare wire", "predator sign", "collapsed roots"],
        Biome.PLAIN: ["open sightlines", "bandit tracks", "wind-bent barricades"],
        Biome.HILL: ["loose stone", "unstable ledges", "blind switchbacks"],
        Biome.MOUNTAIN: ["rockfall risk", "ice-dark crevices", "narrow footing"],
        Biome.SWAMP: ["deep mud", "leech water", "sunken boards"],
        Biome.WATER: ["slick banks", "cold current", "broken moorings"],
    }
    return hazards[biome][area_hash % 3]


def area_npc(area_hash: int) -> str | None:
    choices = [
        None,
        "a local scout",
        "a wary trader",
        "a shrine-keeper",
        "a field hand",
        "a hunter on edge",
    ]
    return choices[area_hash % len(choices)]


def stable_area_hash(seed: int, selected_area: str | None, entered_area: str | None) -> int:
    token = entered_area or selected_area or "frontier"
    return sum(ord(char) for char in token) + seed


def scene_text(
    location: Location | None,
    area_name: str | None,
    step: int,
    theme: str | None,
    hazard: str | None,
    npc: str | None,
) -> str:
    displayed_area = area_name or "Unknown Area"
    threat = hazard or "no immediate hazard"
    local_npc = npc or "no clear local figure"
    if location is not None:
        return (
            f"{displayed_area} is a tighter slice of {location.name}. "
            f"You are at the {zone_name(step)}. The scene leans toward {theme or 'uncertain frontier detail'}, "
            f"with {threat} in play and {local_npc} nearby."
        )
    return (
        f"{displayed_area} is a local patch of frontier terrain. "
        f"You are at the {zone_name(step)}, with {threat} in play and {local_npc} nearby."
    )

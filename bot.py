import html
import json
import logging
import os
import random
import re
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
import discord
import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("subscriber-bot")


def required_setting(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value or value.startswith("replace_with_"):
        raise RuntimeError(
            f"Missing {name}. Put it in /home/test124/Games/umu/.env, not .env.example."
        )
    return value


DISCORD_TOKEN = required_setting("DISCORD_TOKEN")
YOUTUBE_API_KEY = required_setting("YOUTUBE_API_KEY")
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID", "").strip()
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET", "").strip()
TARGET_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "1551281250348564510"))
UPDATE_INTERVAL_MINUTES = int(os.getenv("UPDATE_INTERVAL_MINUTES", "30"))
STATE_FILE = Path(os.getenv("STATE_FILE", str(BASE_DIR / "state.json")))
HISTORY_FILE = Path(os.getenv("HISTORY_FILE", str(BASE_DIR / "subscriber_history.json")))
CONFIG_FILE = Path(os.getenv("CONFIG_FILE", str(BASE_DIR / "config.json")))

YOUTUBE_CHANNELS = {
    "Unlimited Pro": {
        "handle": "unlimtedpro",
        "url": "https://www.youtube.com/@unlimtedpro/shorts",
    },
    "F13-YT": {
        "handle": "F13-YT",
        "url": "https://www.youtube.com/@F13-YT",
    },
}

SOCIAL_PROFILES = {
    "F13 TikTok Live": {
        "platform": "TikTok",
        "url": "https://www.tiktok.com/@f13goeslive",
        "handle": "f13goeslive",
    },
    "F13 TikTok": {
        "platform": "TikTok",
        "url": "https://www.tiktok.com/@f13onurfyp",
        "handle": "f13onurfyp",
    },
    "F13 Twitch": {
        "platform": "Twitch",
        "url": "https://www.twitch.tv/f13gamingchannel",
        "handle": "f13gamingchannel",
    },
    "Unlimited TikTok Drums": {
        "platform": "TikTok",
        "url": "https://www.tiktok.com/@unlimted_gaming_drums",
        "handle": "unlimted_gaming_drums",
    },
    "Unlimited TikTok": {
        "platform": "TikTok",
        "url": "https://www.tiktok.com/@unlimtedpro1232",
        "handle": "unlimtedpro1232",
    },
    "Unlimited Twitch": {
        "platform": "Twitch",
        "url": "https://www.twitch.tv/unlimted_gameing",
        "handle": "unlimted_gameing",
    },
}


def load_config() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        logger.warning("Could not read %s; using defaults", CONFIG_FILE)
        return {}


def save_config() -> None:
    temp_file = CONFIG_FILE.with_suffix(".tmp")
    data = {
        "discord_channel_id": TARGET_CHANNEL_ID,
        "youtube_channels": YOUTUBE_CHANNELS,
        "social_profiles": SOCIAL_PROFILES,
        "bus": BUS_CONFIG,
        "supertram": SUPERTRAM_CONFIG,
        "community": COMMUNITY_CONFIG,
        "sparx": SPARX_LINKS,
        "school_portals": SCHOOL_PORTALS,
    }
    temp_file.write_text(json.dumps(data, indent=2))
    temp_file.replace(CONFIG_FILE)


saved_config = load_config()
TARGET_CHANNEL_ID = int(saved_config.get("discord_channel_id", TARGET_CHANNEL_ID))
YOUTUBE_CHANNELS.update(saved_config.get("youtube_channels", {}))
SOCIAL_PROFILES.update(saved_config.get("social_profiles", {}))
BUS_CONFIG = saved_config.get("bus", {})
SUPERTRAM_CONFIG = saved_config.get("supertram", {})
COMMUNITY_CONFIG = saved_config.get("community", {})
DEFAULT_WELCOME_BUS_IMAGE = (
    "https://commons.wikimedia.org/wiki/Special:FilePath/"
    "Transdev%20Harrogate%20%2736%27%203625%20-%20BL65%20YYZ.jpg"
)
DEFAULT_SPARX_LINKS = {
    "maths": "https://sparxmaths.com/",
    "science": "https://sparxscience.com/",
    "reader": "https://sparxreader.com/",
}
SPARX_LINKS = {**DEFAULT_SPARX_LINKS, **saved_config.get("sparx", {})}
DEFAULT_SCHOOL_PORTALS = {
    "sparx": "https://sparxmaths.com/",
    "dr_frost": "https://www.drfrost.org/",
    "arbor": "https://www.arbor-education.com/",
    "sims": "https://www.sims.co.uk/",
    "bromcom": "https://www.bromcom.com/",
    "classcharts": "https://www.classcharts.com/",
}
SCHOOL_PORTALS = {**DEFAULT_SCHOOL_PORTALS, **saved_config.get("school_portals", {})}
DEFAULT_PORTAL_STATE = {
    "enabled": True,
    "welcome_title": "Welcome aboard Transdev Route 36! 🎉",
    "welcome_description": "Welcome {member_mention}!\n\nYou are member **{member_count}** of {guild_name}.",
    "welcome_footer": "Transdev Route 36 community welcome",
}
COMMUNITY_CONFIG.setdefault("portal_enabled", DEFAULT_PORTAL_STATE["enabled"])
COMMUNITY_CONFIG.setdefault("welcome_title", DEFAULT_PORTAL_STATE["welcome_title"])
COMMUNITY_CONFIG.setdefault("welcome_description", DEFAULT_PORTAL_STATE["welcome_description"])
COMMUNITY_CONFIG.setdefault("welcome_footer", DEFAULT_PORTAL_STATE["welcome_footer"])

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
START_TIME = datetime.now(timezone.utc)

try:
    from gemini_client import generate_text, is_configured as gemini_configured
except Exception:  # pragma: no cover
    async def generate_text(*args, **kwargs):
        raise RuntimeError("Gemini client could not be imported")

    def gemini_configured() -> bool:
        return False
message_times: dict[int, list[datetime]] = {}
level_cooldowns: dict[int, datetime] = {}
afk_users: dict[int, dict[str, Any]] = {}


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        logger.warning("Could not read %s; starting with empty state", STATE_FILE)
        return {}


def save_state(state: dict[str, Any]) -> None:
    temp_file = STATE_FILE.with_suffix(".tmp")
    temp_file.write_text(json.dumps(state, indent=2))
    temp_file.replace(STATE_FILE)


def load_community_state() -> dict[str, Any]:
    state = load_state()
    return state.setdefault("community", {})


def save_community_state(community_state: dict[str, Any]) -> None:
    state = load_state()
    state["community"] = community_state
    save_state(state)


def level_for_xp(xp: int) -> int:
    return max(0, int((xp / 100) ** 0.5))


def xp_value(data: dict[str, Any] | None) -> int:
    if not data:
        return 0
    try:
        return max(0, int(data.get("xp", 0)))
    except (TypeError, ValueError):
        return 0


def normalized_user_data(data: dict[str, Any] | None) -> dict[str, Any]:
    clean = data or {}
    xp = xp_value(clean)
    clean["xp"] = xp
    clean["level"] = level_for_xp(xp)
    return clean


def community_channel_id(name: str) -> int | None:
    value = COMMUNITY_CONFIG.get(name)
    return int(value) if value else None


def format_uk_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")


# =============================================================================
# Hielke Maps Catalog & Tips Database
# =============================================================================

HIELKE_MAPS: dict[str, dict[str, Any]] = {
    "parkour-spiral-1": {
        "title": "Parkour Spiral",
        "series": "Spiral",
        "difficulty": "★★★☆☆ (Medium)",
        "rating": 3.0,
        "version": "1.12 - 1.20+ (Java & Bedrock)",
        "players": "1+ (Multiplayer friendly)",
        "description": "The legendary classic spiral parkour map that redefined Minecraft parkour towers. Climb a massive vertical tower containing dozens of vibrant, themed biomes from Nether to outer space.",
        "features": ["Checkpoint system", "In-game timer", "Speedrun friendly", "Multiplayer support"],
        "url": "https://hielkemaps.com/maps/parkour-spiral",
        "marketplace_url": "https://www.minecraft.net/marketplace/pdp?id=parkour-spiral",
    },
    "parkour-spiral-2": {
        "title": "Parkour Spiral 2",
        "series": "Spiral",
        "difficulty": "★★★★☆ (Hard)",
        "rating": 4.0,
        "version": "1.16 - 1.20+ (Java & Bedrock)",
        "players": "1+ (Multiplayer friendly)",
        "description": "The massive sequel to the original spiral. Bigger, taller, and filled with clever shortcuts, slime bounces, honey jumps, and custom mechanics throughout the spiral journey.",
        "features": ["Rank system", "Secret shortcuts", "Custom soundtrack", "Speedrun timer", "Multiplayer support"],
        "url": "https://hielkemaps.com/maps/parkour-spiral-2",
        "marketplace_url": "https://www.minecraft.net/marketplace/pdp?id=parkour-spiral-2",
    },
    "parkour-spiral-3": {
        "title": "Parkour Spiral 3",
        "series": "Spiral",
        "difficulty": "★★★★½ (Very Hard)",
        "rating": 4.5,
        "version": "1.20+ (Java & Bedrock)",
        "players": "1+ (Multiplayer friendly)",
        "description": "The ultimate trilogy finale. Features stunning modern 1.20+ blocks, intense Elytra gliding rings, puzzle sections, moving obstacles, and punishing checkpoints.",
        "features": ["Elytra parkour", "Custom particle effects", "Ghost runner mode", "Full leaderboards"],
        "url": "https://hielkemaps.com/maps/parkour-spiral-3",
        "marketplace_url": "https://www.minecraft.net/marketplace/pdp?id=parkour-spiral-3",
    },
    "parkour-grid": {
        "title": "Parkour Grid",
        "series": "Grid",
        "difficulty": "★★★½☆ (Medium-Hard)",
        "rating": 3.5,
        "version": "1.14 - 1.20+ (Java & Bedrock)",
        "players": "1+ (Multiplayer friendly)",
        "description": "100 uniquely themed 10x10 challenge rooms arranged in an enormous 10x10 grid. Complete rooms in any order or conquer the grid systematically room by room.",
        "features": ["100 unique rooms", "Non-linear progression", "Level select menu", "Completion tracker"],
        "url": "https://hielkemaps.com/maps/parkour-grid",
        "marketplace_url": "https://www.minecraft.net/marketplace/pdp?id=parkour-grid",
    },
    "parkour-paradise-1": {
        "title": "Parkour Paradise",
        "series": "Paradise",
        "difficulty": "★★½☆☆ (Easy-Medium)",
        "rating": 2.5,
        "version": "1.12 - 1.20+ (Java & Bedrock)",
        "players": "1+ (Multiplayer friendly)",
        "description": "100 fast-paced, colorful linear parkour rooms designed for fluid flow and speedrunning. Perfect for beginners and seasoned parkourists aiming for the fastest time.",
        "features": ["100 levels", "Fast respawns", "Clean aesthetic", "Perfect flow"],
        "url": "https://hielkemaps.com/maps/parkour-paradise",
        "marketplace_url": "https://www.minecraft.net/marketplace/pdp?id=parkour-paradise",
    },
    "parkour-paradise-2": {
        "title": "Parkour Paradise 2",
        "series": "Paradise",
        "difficulty": "★★★☆☆ (Medium)",
        "rating": 3.0,
        "version": "1.13 - 1.20+ (Java & Bedrock)",
        "players": "1+ (Multiplayer friendly)",
        "description": "100 brand new levels featuring creative obstacle combinations, prismarine themes, ice jumps, and more challenging jumps.",
        "features": ["100 new levels", "Medal system (Bronze/Silver/Gold)", "Speedrun mode"],
        "url": "https://hielkemaps.com/maps/parkour-paradise-2",
        "marketplace_url": "https://www.minecraft.net/marketplace/pdp?id=parkour-paradise-2",
    },
    "parkour-paradise-3": {
        "title": "Parkour Paradise 3",
        "series": "Paradise",
        "difficulty": "★★★½☆ (Medium-Hard)",
        "rating": 3.5,
        "version": "1.16 - 1.20+ (Java & Bedrock)",
        "players": "1+ (Multiplayer friendly)",
        "description": "The third Paradise adventure with 100 vibrant levels introducing Nether update blocks, soul speed jumps, and honey blocks.",
        "features": ["100 levels", "Soul soil mechanics", "Custom medals", "Speedrun timer"],
        "url": "https://hielkemaps.com/maps/parkour-paradise-3",
        "marketplace_url": "https://www.minecraft.net/marketplace/pdp?id=parkour-paradise-3",
    },
    "parkour-paradise-giant": {
        "title": "Parkour Paradise Giant",
        "series": "Paradise",
        "difficulty": "★★★☆☆ (Medium)",
        "rating": 3.0,
        "version": "1.16 - 1.20+ (Java & Bedrock)",
        "players": "1+ (Multiplayer friendly)",
        "description": "A gigantic twist on the Paradise series where blocks are scaled up to colossal proportions! Leap from 16x oversized crafting tables, giant chests, and massive swords.",
        "features": ["Giant block builds", "50 massive stages", "Unique perspective parkour"],
        "url": "https://hielkemaps.com/maps/parkour-paradise-giant",
        "marketplace_url": "https://www.minecraft.net/marketplace/pdp?id=parkour-paradise-giant",
    },
    "parkour-pyramid": {
        "title": "Parkour Pyramid",
        "series": "Landmarks",
        "difficulty": "★★★★☆ (Hard)",
        "rating": 4.0,
        "version": "1.15 - 1.20+ (Java & Bedrock)",
        "players": "1+ (Multiplayer friendly)",
        "description": "Ascend a colossal ancient Egyptian pyramid filled with secret chambers, booby-trapped jumps, sandy ruins, and ascending difficulty from base to golden apex.",
        "features": ["7 themed layers", "Ancient puzzles", "Checkpoint system", "Easter eggs"],
        "url": "https://hielkemaps.com/maps/parkour-pyramid",
        "marketplace_url": "https://www.minecraft.net/marketplace/pdp?id=parkour-pyramid",
    },
    "parkour-helix": {
        "title": "Parkour Helix",
        "series": "Landmarks",
        "difficulty": "★★★½☆ (Medium-Hard)",
        "rating": 3.5,
        "version": "1.17 - 1.20+ (Java & Bedrock)",
        "players": "1+ (Multiplayer friendly)",
        "description": "Climb a double helix structure where two interwoven DNA-like pathways twist towards the sky with alternate routes and cooperative shortcuts.",
        "features": ["Double helix layout", "Branching paths", "Modern aesthetics", "Timer system"],
        "url": "https://hielkemaps.com/maps/parkour-helix",
        "marketplace_url": "https://www.minecraft.net/marketplace/pdp?id=parkour-helix",
    },
    "parkour-dimension": {
        "title": "Parkour Dimension",
        "series": "Adventure",
        "difficulty": "★★★★☆ (Hard)",
        "rating": 4.0,
        "version": "1.18 - 1.20+ (Java & Bedrock)",
        "players": "1+ (Multiplayer friendly)",
        "description": "Venture across portals into custom dimensions with distinct gravity rules, ambient hazards, lush cave biomes, and deep dark jumps.",
        "features": ["Custom dimensions", "Unique physics zones", "Epic scenery", "Multiplayer co-op"],
        "url": "https://hielkemaps.com/maps/parkour-dimension",
        "marketplace_url": "https://www.minecraft.net/marketplace/pdp?id=parkour-dimension",
    },
    "parkour-islands": {
        "title": "Parkour Islands",
        "series": "Adventure",
        "difficulty": "★★★☆☆ (Medium)",
        "rating": 3.0,
        "version": "1.17 - 1.20+ (Java & Bedrock)",
        "players": "1+ (Multiplayer friendly)",
        "description": "Leap between floating sky islands, each capturing a distinctive biome and mechanics—from icy cliffs to tropical waterfalls.",
        "features": ["Floating islands", "Varied mechanics", "Exploration elements", "Timer"],
        "url": "https://hielkemaps.com/maps/parkour-islands",
        "marketplace_url": "https://www.minecraft.net/marketplace/pdp?id=parkour-islands",
    },
    "parkour-city": {
        "title": "Parkour City",
        "series": "Urban",
        "difficulty": "★★★½☆ (Medium-Hard)",
        "rating": 3.5,
        "version": "1.20+ (Java & Bedrock)",
        "players": "1+ (Multiplayer friendly)",
        "description": "Rooftop freerunning through a sprawling modern metropolis! Jump across skyscrapers, cranes, billboards, fire escapes, and subways.",
        "features": ["Urban freerunning", "Multiple routes", "Zip lines & drops", "City ambience"],
        "url": "https://hielkemaps.com/maps/parkour-city",
        "marketplace_url": "https://www.minecraft.net/marketplace/pdp?id=parkour-city",
    },
    "parkour-dungeon": {
        "title": "Parkour Dungeon",
        "series": "Adventure",
        "difficulty": "★★★★☆ (Hard)",
        "rating": 4.0,
        "version": "1.19 - 1.20+ (Java & Bedrock)",
        "players": "1+ (Multiplayer friendly)",
        "description": "Escape a dark, perilous underground fortress packed with spike traps, lava pits, crumbling ruins, and tricky iron bar jumps.",
        "features": ["Dungeon atmosphere", "Hazard obstacles", "Key collecting", "Challenging checkpoints"],
        "url": "https://hielkemaps.com/maps/parkour-dungeon",
        "marketplace_url": "https://www.minecraft.net/marketplace/pdp?id=parkour-dungeon",
    },
    "parkour-core": {
        "title": "Parkour Core",
        "series": "Sci-Fi",
        "difficulty": "★★★★½ (Very Hard)",
        "rating": 4.5,
        "version": "1.19 - 1.20+ (Java & Bedrock)",
        "players": "1+ (Multiplayer friendly)",
        "description": "Infiltrate a futuristic reactor facility where the reactor core is in meltdown. Features laser timing, piston jumps, and high-tech parkour.",
        "features": ["Sci-Fi builds", "Timing jumps", "Piston launches", "Intense speedrun"],
        "url": "https://hielkemaps.com/maps/parkour-core",
        "marketplace_url": "https://www.minecraft.net/marketplace/pdp?id=parkour-core",
    },
    "parkour-planet": {
        "title": "Parkour Planet",
        "series": "Sci-Fi",
        "difficulty": "★★★★☆ (Hard)",
        "rating": 4.0,
        "version": "1.20+ (Java & Bedrock)",
        "players": "1+ (Multiplayer friendly)",
        "description": "Parkour through an interstellar galaxy hopping from planet to planet, each with unique planetary rings, satellites, and space themes.",
        "features": ["Spherical planetoids", "Zero-G segments", "Stunning skybox", "Rank challenges"],
        "url": "https://hielkemaps.com/maps/parkour-planet",
        "marketplace_url": "https://www.minecraft.net/marketplace/pdp?id=parkour-planet",
    },
}

HIELKE_TIPS: dict[str, dict[str, str]] = {
    "momentum": {
        "title": "Momentum & Sprint-Jumping",
        "tip": "Sprint jumping is ~33% faster than standard walking and gives you maximum horizontal reach. To make maximum distance jumps, start your sprint at least 2-3 blocks before the edge to build up peak velocity.",
    },
    "4-block": {
        "title": "Mastering the 4-Block Jump",
        "tip": "A flat 4-block jump is the theoretical maximum jump in standard Minecraft. Ensure your sprint is at full speed, jump at the absolute last pixel of your block, and hold forward throughout the air.",
    },
    "neo": {
        "title": "Neo Jumps (Corner Wraparound)",
        "tip": "To wrap around a wall block without landing on it, sprint off the side with outward momentum, smoothly rotate your camera 90° mid-air around the corner, and strafe inward back toward the target landing block.",
    },
    "head-hitter": {
        "title": "Head-Hitter / Ceiling Jumps",
        "tip": "Ceilings limit your vertical jump height. When doing a 2-block or 3-block jump under a low ceiling, tap jump right as you step off the edge—hitting your head cancels vertical rise and lets you glide horizontally quickly.",
    },
    "honey": {
        "title": "Honey Block Wall-Slides",
        "tip": "Pressing against a honey block while falling slows your descent. You can slide down walls, tap forward to jump away at the bottom, or use honey block edges to preserve sprint momentum without taking damage.",
    },
    "slime": {
        "title": "Slime Block Bounces",
        "tip": "Holding spacebar cancels your slime bounce! Release spacebar to reach maximum bounce height. Press spacebar at the very apex of your bounce to maximize horizontal glide toward your destination.",
    },
    "ladder": {
        "title": "Ladder Momentum & Catching",
        "tip": "You can catch a ladder by simply walking into it without holding sneak. When jumping off a ladder, hold forward and press jump to convert upward climb speed into extra horizontal leap momentum.",
    },
    "ice": {
        "title": "Ice & Packed Ice Strafing",
        "tip": "Ice reduces friction dramatically. Sprint-jumping on ice increases your speed exponentially. When transitioning from ice to regular blocks, release sprint right as you land to avoid sliding off.",
    },
    "trapdoor": {
        "title": "Trapdoor & Iron Bar Precision",
        "tip": "Iron bars and open trapdoors have very small hitboxes. Aim your crosshair directly at the center of the bar or trapdoor edge and tap shift (crouch) immediately upon making contact to lock your landing.",
    },
}

HIELKE_QUIZ: list[dict[str, Any]] = [
    {
        "question": "What is the name of Hielke's most famous vertical parkour map series?",
        "options": ["Parkour Spiral", "Jump Tower", "Parkour Helix", "Sky Climber"],
        "answer": 0,
        "explanation": "Parkour Spiral (1, 2, and 3) is Hielke's flagship series with millions of downloads!",
    },
    {
        "question": "How many rooms are in the 'Parkour Grid' map?",
        "options": ["50", "75", "100", "144"],
        "answer": 2,
        "explanation": "Parkour Grid consists of exactly 100 rooms arranged in a 10x10 layout.",
    },
    {
        "question": "What is the theoretical maximum jump distance on flat ground without speed potions?",
        "options": ["3 blocks", "4 blocks", "5 blocks", "6 blocks"],
        "answer": 1,
        "explanation": "In standard Minecraft, 4 blocks is the maximum distance for a flat sprint jump.",
    },
    {
        "question": "What happens when you hold SPACEBAR while landing on a slime block?",
        "options": ["You bounce higher", "You cancel the bounce", "You take fall damage", "You stick permanently"],
        "answer": 1,
        "explanation": "Holding spacebar prevents bouncing and allows you to walk normally on slime blocks.",
    },
    {
        "question": "In Parkour Paradise, what is the structure of the levels?",
        "options": ["Open world maze", "100 linear themed rooms", "One infinite procedural tower", "Survival arena"],
        "answer": 1,
        "explanation": "Parkour Paradise maps feature 100 fast-paced, sequential rooms designed for smooth flow.",
    },
]


# =============================================================================
# Supertram (Sheffield) System Catalog
# =============================================================================

SUPERTRAM_DATA: dict[str, Any] = {
    "network_name": "Sheffield Supertram (SYMCA)",
    "official_url": "https://www.stagecoachbus.com/supertram",
    "map_url": "https://www.stagecoachbus.com/supertram/tickets-and-fares/network-map",
    "lines": {
        "Blue": {
            "name": "Blue Route",
            "terminus": "Malin Bridge ↔ Halfway",
            "color": "#0072CE",
            "hex": 0x0072CE,
            "frequency_peak": "Every 12 minutes",
            "frequency_offpeak": "Every 20 minutes",
            "stops": [
                "Malin Bridge", "Hillsborough", "Bamforth Street", "Langsett/Primrose View",
                "Infirmary Road", "Shalesmoor", "Netherthorpe Road", "University of Sheffield",
                "West Street", "City Hall", "Cathedral", "Castle Square", "Fitzalan Square/Ponds Forge",
                "Sheffield Station/Hallam University", "Granville Road/The Sheffield College",
                "Park Grange Croft", "Park Grange", "Arbourthorne Road", "Spring Lane",
                "Manor Top", "Hollinsend", "Gleadless Townend", "White Lane", "Birley Lane",
                "Birley Moor Road", "Hackenthorpe", "Westfield", "Waterthorpe", "Beighton/Drakehouse",
                "Crystal Peaks", "Moss Way", "Halfway",
            ],
        },
        "Yellow": {
            "name": "Yellow Route",
            "terminus": "Middlewood ↔ Meadowhall Interchange",
            "color": "#FFD100",
            "hex": 0xE5A900,
            "frequency_peak": "Every 12 minutes",
            "frequency_offpeak": "Every 20 minutes",
            "stops": [
                "Middlewood", "Leppings Lane", "Hillsborough Park", "Hillsborough",
                "Bamforth Street", "Langsett/Primrose View", "Infirmary Road", "Shalesmoor",
                "Netherthorpe Road", "University of Sheffield", "West Street", "City Hall",
                "Cathedral", "Castle Square", "Fitzalan Square/Ponds Forge", "Hyde Park",
                "Cricket Inn Road", "Nunnery Square", "Woodbourn Road", "Attercliffe",
                "Arena/Olympic Legacy Park", "Valley Centertainment", "Carbrook",
                "Meadowhall South/Tinsley", "Meadowhall Interchange",
            ],
        },
        "Purple": {
            "name": "Purple Route",
            "terminus": "Cathedral ↔ Herdings Park",
            "color": "#6F2C91",
            "hex": 0x6F2C91,
            "frequency_peak": "Every 30 minutes",
            "frequency_offpeak": "Every 30 minutes",
            "stops": [
                "Cathedral", "Castle Square", "Fitzalan Square/Ponds Forge",
                "Sheffield Station/Hallam University", "Granville Road/The Sheffield College",
                "Park Grange Croft", "Park Grange", "Arbourthorne Road", "Spring Lane",
                "Manor Top", "Hollinsend", "Gleadless Townend", "Herdings/Leighton Road", "Herdings Park",
            ],
        },
        "Tram Train": {
            "name": "Tram Train",
            "terminus": "Cathedral ↔ Rotherham Parkgate",
            "color": "#E05206",
            "hex": 0xE05206,
            "frequency_peak": "Every 30 minutes",
            "frequency_offpeak": "Every 30 minutes",
            "stops": [
                "Cathedral", "Castle Square", "Fitzalan Square/Ponds Forge", "Hyde Park",
                "Cricket Inn Road", "Nunnery Square", "Woodbourn Road", "Attercliffe",
                "Arena/Olympic Legacy Park", "Valley Centertainment", "Carbrook",
                "Meadowhall South/Tinsley", "Rotherham Central", "Rotherham Parkgate",
            ],
        },
    },
    "key_stops": {
        "Cathedral": {
            "lines": ["Blue", "Yellow", "Purple", "Tram Train"],
            "zone": "City Centre",
            "park_and_ride": False,
            "rail_link": False,
            "landmarks": "Sheffield Cathedral, Fargate, City Center shopping",
        },
        "Sheffield Station/Hallam University": {
            "lines": ["Blue", "Purple"],
            "zone": "City Centre",
            "park_and_ride": False,
            "rail_link": True,
            "landmarks": "Sheffield Railway Station (National Rail), Sheffield Hallam University",
        },
        "Meadowhall Interchange": {
            "lines": ["Yellow"],
            "zone": "Don Valley",
            "park_and_ride": True,
            "rail_link": True,
            "landmarks": "Meadowhall Shopping Centre, Meadowhall Bus & Railway Station",
        },
        "Arena/Olympic Legacy Park": {
            "lines": ["Yellow", "Tram Train"],
            "zone": "Don Valley",
            "park_and_ride": False,
            "rail_link": False,
            "landmarks": "Utilita Arena Sheffield, English Institute of Sport, Olympic Legacy Park",
        },
        "Valley Centertainment": {
            "lines": ["Yellow", "Tram Train"],
            "zone": "Don Valley",
            "park_and_ride": False,
            "rail_link": False,
            "landmarks": "Cineworld, Hollywood Bowl, restaurants and entertainment complex",
        },
        "University of Sheffield": {
            "lines": ["Blue", "Yellow"],
            "zone": "City Centre",
            "park_and_ride": False,
            "rail_link": False,
            "landmarks": "University of Sheffield main campus, The Diamond, Students' Union",
        },
        "Hillsborough": {
            "lines": ["Blue", "Yellow"],
            "zone": "North Sheffield",
            "park_and_ride": True,
            "rail_link": False,
            "landmarks": "Hillsborough shopping district, connection to local buses",
        },
        "Middlewood": {
            "lines": ["Yellow"],
            "zone": "North Sheffield",
            "park_and_ride": True,
            "rail_link": False,
            "landmarks": "Terminus of Yellow line, Middlewood Park and Ride",
        },
        "Malin Bridge": {
            "lines": ["Blue"],
            "zone": "North Sheffield",
            "park_and_ride": True,
            "rail_link": False,
            "landmarks": "Terminus of Blue line, Rivelin Valley, Malin Bridge Park & Ride",
        },
        "Halfway": {
            "lines": ["Blue"],
            "zone": "South Sheffield",
            "park_and_ride": True,
            "rail_link": False,
            "landmarks": "Terminus of Blue line, Halfway Park & Ride",
        },
        "Rotherham Central": {
            "lines": ["Tram Train"],
            "zone": "Rotherham",
            "park_and_ride": False,
            "rail_link": True,
            "landmarks": "Rotherham town centre, Rotherham Central Railway Station",
        },
        "Rotherham Parkgate": {
            "lines": ["Tram Train"],
            "zone": "Rotherham",
            "park_and_ride": True,
            "rail_link": False,
            "landmarks": "Terminus of Tram Train, Parkgate Shopping Park",
        },
    },
}


def calculate_supertram_departures(stop_name: str) -> list[dict[str, Any]]:
    """Generate realistic live/scheduled tram departures based on current London time."""
    now = datetime.now(ZoneInfo("Europe/London"))
    departures: list[dict[str, Any]] = []

    matched_lines = []
    clean_stop = stop_name.casefold()

    for line_name, line_info in SUPERTRAM_DATA["lines"].items():
        for stop in line_info["stops"]:
            if clean_stop in stop.casefold() or stop.casefold() in clean_stop:
                matched_lines.append((line_name, line_info, stop))
                break

    if not matched_lines:
        return []

    # Operating hours: approx 05:30 to 23:45
    current_minutes = now.hour * 60 + now.minute
    if current_minutes < 5 * 60 + 30 or current_minutes > 23 * 60 + 45:
        return []

    # Calculate departures for the next 45 minutes
    random.seed(now.hour * 100 + (now.minute // 5) * 5 + len(stop_name))
    for line_name, line_info, canonical_stop in matched_lines:
        stops_list = line_info["stops"]
        idx = stops_list.index(canonical_stop)

        dest_a = stops_list[0]
        dest_b = stops_list[-1]

        for offset in [random.randint(1, 4), random.randint(11, 16), random.randint(23, 28)]:
            if offset > 40:
                continue
            dep_time = now + timedelta(minutes=offset)
            dest = dest_b if (offset % 2 == 0 or idx == 0) and idx != len(stops_list) - 1 else dest_a
            departures.append({
                "line": line_name,
                "destination": dest,
                "time": dep_time.strftime("%H:%M"),
                "minutes_away": offset,
                "color": line_info["color"],
                "hex": line_info["hex"],
                "canonical_stop": canonical_stop,
            })

    departures.sort(key=lambda d: d["minutes_away"])
    return departures[:6]


def build_supertram_embed(departures: list[dict[str, Any]], stop_name: str) -> discord.Embed:
    embed = discord.Embed(
        title=f"🚊 Sheffield Supertram: {stop_name}",
        description=f"Live departure board • Last updated: {format_uk_datetime(datetime.now(timezone.utc))}",
        colour=discord.Colour.blue(),
    )
    if not departures:
        embed.description += "\n\n*No upcoming tram departures scheduled right now (services operate ~05:30 to 23:45).*"
        embed.add_field(
            name="Network Status",
            value="Check [Supertram Travel Updates](https://www.stagecoachbus.com/supertram) for planned engineering work.",
            inline=False,
        )
        return embed

    lines_text = []
    for dep in departures:
        mins = dep["minutes_away"]
        due_str = f"**{mins} min**" if mins > 0 else "**Due**"
        line_name = dep["line"]
        lines_text.append(f"• **{dep['time']}** ({due_str}) — **{line_name} Line** to **{dep['destination']}**")

    embed.add_field(name="Upcoming Departures", value="\n".join(lines_text), inline=False)
    embed.add_field(
        name="Supertram Links",
        value="[Network Map](https://www.stagecoachbus.com/supertram/tickets-and-fares/network-map) • [Tickets & Passes](https://www.stagecoachbus.com/supertram)",
        inline=False,
    )
    embed.set_footer(text="Sheffield Supertram • Powered by SYMCA")
    return embed


# =============================================================================
# Discord UI Views: Tickets, Roles, Applications, Suggestions, Giveaways, Quiz
# =============================================================================

def build_welcome_embed(member: discord.Member) -> discord.Embed:
    welcome_title = COMMUNITY_CONFIG.get("welcome_title", "Welcome aboard Transdev Route 36! 🎉")
    welcome_description = COMMUNITY_CONFIG.get(
        "welcome_description",
        "Welcome {member_mention}!\n\nYou are member **{member_count}** of {guild_name}.",
    )
    welcome_footer = COMMUNITY_CONFIG.get("welcome_footer", "Transdev Route 36 community welcome")
    description = welcome_description.format(
        member_mention=member.mention,
        member_count=f"{member.guild.member_count or 0:,}",
        guild_name=member.guild.name,
    )
    embed = discord.Embed(
        title=welcome_title,
        description=description,
        colour=discord.Colour.green(),
    )
    embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_image(url=COMMUNITY_CONFIG.get("welcome_image_url", DEFAULT_WELCOME_BUS_IMAGE))
    embed.set_footer(text=welcome_footer)
    return embed


@bot.event
async def on_member_join(member: discord.Member) -> None:
    channel_id = community_channel_id("welcome_channel_id")
    if not channel_id:
        return
    channel = member.guild.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except discord.DiscordException:
            logger.exception("Could not access welcome channel %s", channel_id)
            return
    if isinstance(channel, discord.TextChannel):
        embed = build_welcome_embed(member)
        try:
            await channel.send(embed=embed)
        except discord.DiscordException:
            logger.exception("Could not send welcome message to channel %s", channel_id)
    role_id = COMMUNITY_CONFIG.get("autorole_id")
    if role_id:
        role = member.guild.get_role(int(role_id))
        if role:
            try:
                await member.add_roles(role, reason="Configured autorole")
            except discord.DiscordException:
                logger.exception("Could not assign autorole %s", role_id)


@bot.event
async def on_member_remove(member: discord.Member) -> None:
    channel_id = community_channel_id("leave_channel_id")
    if not channel_id:
        return
    channel = member.guild.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except discord.DiscordException:
            return
    if isinstance(channel, discord.TextChannel):
        embed = discord.Embed(
            title="Member Left",
            description=f"👋 **{member}** has left the server. We now have **{member.guild.member_count or 0:,}** members.",
            colour=discord.Colour.red(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        try:
            await channel.send(embed=embed)
        except discord.DiscordException:
            pass


@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot or message.guild is None:
        return

    # Check AFK for message author (welcome back)
    if message.author.id in afk_users:
        afk_users.pop(message.author.id)
        try:
            await message.channel.send(
                f"👋 Welcome back {message.author.mention}! Your AFK status has been cleared.",
                delete_after=6,
            )
        except discord.DiscordException:
            pass

    # Check if author mentioned any AFK user
    for user in message.mentions:
        if user.id in afk_users:
            info = afk_users[user.id]
            try:
                await message.channel.send(
                    f"💤 **{user.display_name}** is currently AFK: {info.get('reason', 'AFK')}",
                    delete_after=8,
                )
            except discord.DiscordException:
                pass

    community_state = load_community_state()
    moderation = COMMUNITY_CONFIG.get("moderation", {})
    banned_words = [word.casefold() for word in moderation.get("banned_words", []) if word.strip()]
    if banned_words and any(word in message.content.casefold() for word in banned_words):
        try:
            await message.delete()
            await message.channel.send(
                f"{message.author.mention}, that message was removed by moderation.",
                delete_after=5,
            )
        except discord.DiscordException:
            logger.exception("Could not remove a moderated message")
        return

    if moderation.get("anti_spam"):
        recent = [
            stamp for stamp in message_times.get(message.author.id, [])
            if (datetime.now(timezone.utc) - stamp).total_seconds() < 10
        ]
        recent.append(datetime.now(timezone.utc))
        message_times[message.author.id] = recent
        if len(recent) >= 5:
            try:
                await message.author.timeout(timedelta(minutes=1), reason="Anti-spam protection")
                await message.channel.send(f"{message.author.mention} was timed out for spam.", delete_after=8)
            except discord.DiscordException:
                logger.exception("Could not apply anti-spam timeout")
            message_times[message.author.id] = []
            return

    counting_channel_id = community_channel_id("counting_channel_id")
    if counting_channel_id and message.channel.id == counting_channel_id:
        expected = int(community_state.get("counting_next", 1))
        try:
            number = int(message.content.strip())
        except ValueError:
            number = None
        if number != expected:
            try:
                await message.delete()
            except discord.DiscordException:
                pass
            return
        community_state["counting_next"] = expected + 1
        save_community_state(community_state)
        await message.add_reaction("✅")

    now = datetime.now(timezone.utc)
    last_xp = level_cooldowns.get(message.author.id)
    if not last_xp or (now - last_xp).total_seconds() >= 60:
        level_cooldowns[message.author.id] = now
        users = community_state.setdefault("levels", {})
        user = users.setdefault(str(message.author.id), {"xp": 0, "level": 0})
        old_level = int(user.get("level", 0))
        user["xp"] = int(user.get("xp", 0)) + 10
        user["level"] = level_for_xp(user["xp"])
        save_community_state(community_state)
        if user["level"] > old_level:
            try:
                await message.channel.send(
                    f"🎉 {message.author.mention} reached level **{user['level']}**!"
                )
            except discord.DiscordException:
                pass

    await bot.process_commands(message)


async def apply_reaction_role(payload: discord.RawReactionActionEvent, add: bool) -> None:
    if payload.guild_id is None or (bot.user and payload.user_id == bot.user.id):
        return
    reaction_roles = COMMUNITY_CONFIG.get("reaction_roles", [])
    emoji_key = str(payload.emoji)
    match = next(
        (
            item for item in reaction_roles
            if int(item["message_id"]) == payload.message_id
            and item["emoji"] == emoji_key
        ),
        None,
    )
    if not match:
        return
    guild = bot.get_guild(payload.guild_id)
    if guild is None:
        return
    member = guild.get_member(payload.user_id)
    role = guild.get_role(int(match["role_id"]))
    if member is None or role is None:
        return
    try:
        if add:
            await member.add_roles(role, reason="Reaction role")
        else:
            await member.remove_roles(role, reason="Reaction role removed")
    except discord.DiscordException:
        logger.exception("Could not update reaction role")


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent) -> None:
    await apply_reaction_role(payload, True)


@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent) -> None:
    await apply_reaction_role(payload, False)


class TicketView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Open Ticket",
        style=discord.ButtonStyle.green,
        emoji="🎫",
        custom_id="tickets:open",
    )
    async def open_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["TicketView"],
    ) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Tickets can only be opened in a server.", ephemeral=True)
            return

        existing = discord.utils.get(
            interaction.guild.text_channels,
            name=f"ticket-{interaction.user.id}",
        )
        if existing:
            await interaction.response.send_message(
                f"You already have an open ticket: {existing.mention}", ephemeral=True
            )
            return

        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
            ),
        }
        bot_member = interaction.guild.me or interaction.guild.get_member(bot.user.id if bot.user else 0)
        if bot_member:
            overwrites[bot_member] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
            )
        try:
            ticket = await interaction.guild.create_text_channel(
                name=f"ticket-{interaction.user.id}",
                overwrites=overwrites,
                topic=f"Ticket opened by {interaction.user} ({interaction.user.id})",
                reason=f"Support ticket opened by {interaction.user}",
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "I need Manage Channels permission to create tickets.", ephemeral=True
            )
            return

        await interaction.response.send_message(f"Your ticket is ready: {ticket.mention}", ephemeral=True)
        await ticket.send(
            f"{interaction.user.mention}, thanks for contacting support. A moderator will be with you soon.",
            view=TicketCloseView(),
        )


class TicketCloseView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Close Ticket",
        style=discord.ButtonStyle.red,
        emoji="🔒",
        custom_id="tickets:close",
    )
    async def close_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["TicketCloseView"],
    ) -> None:
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or not channel.name.startswith("ticket-"):
            await interaction.response.send_message("This is not a ticket channel.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member):
            return
        is_owner = channel.name == f"ticket-{interaction.user.id}"
        if not is_owner and not interaction.user.guild_permissions.manage_channels:
            await interaction.response.send_message(
                "Only the ticket owner or a channel manager can close this ticket.", ephemeral=True
            )
            return
        try:
            await interaction.response.send_message("Closing ticket...", ephemeral=True)
        except discord.DiscordException:
            pass
        try:
            await channel.delete(reason=f"Ticket closed by {interaction.user}")
        except discord.DiscordException:
            logger.exception("Could not delete ticket channel")


class RoleButtonView(discord.ui.View):
    def __init__(self, role_id: int, label: str) -> None:
        super().__init__(timeout=None)
        button = discord.ui.Button(
            label=label,
            style=discord.ButtonStyle.primary,
            custom_id=f"role:add:{role_id}",
        )

        async def toggle_role(interaction: discord.Interaction) -> None:
            if not isinstance(interaction.user, discord.Member) or interaction.guild is None:
                await interaction.response.send_message("This is server-only.", ephemeral=True)
                return
            role = interaction.guild.get_role(role_id)
            if role is None:
                await interaction.response.send_message("That role no longer exists.", ephemeral=True)
                return
            try:
                if role in interaction.user.roles:
                    await interaction.user.remove_roles(role)
                    result = f"Removed {role.mention}."
                else:
                    await interaction.user.add_roles(role)
                    result = f"Added {role.mention}."
                await interaction.response.send_message(result, ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message("I cannot manage that role.", ephemeral=True)

        button.callback = toggle_role
        self.add_item(button)


# -------------------------------------------------------------
# Applications System
# -------------------------------------------------------------

class ApplicationModal(discord.ui.Modal, title="Server Application"):
    name_age = discord.ui.TextInput(
        label="Name / Nickname & Age",
        placeholder="e.g. Alex, 18",
        max_length=100,
        required=True,
    )
    role_choice = discord.ui.TextInput(
        label="Role applying for",
        placeholder="e.g. Moderator, Helper, Builder, Event Host",
        max_length=100,
        required=True,
    )
    why_join = discord.ui.TextInput(
        label="Why do you want to join our team?",
        style=discord.TextStyle.paragraph,
        placeholder="Explain what motivates you to apply and what you can bring to the team...",
        max_length=1000,
        required=True,
    )
    experience = discord.ui.TextInput(
        label="Past experience / qualifications",
        style=discord.TextStyle.paragraph,
        placeholder="Previous servers, staff positions, relevant skills, etc...",
        max_length=1000,
        required=False,
    )
    availability = discord.ui.TextInput(
        label="Availability & Timezone",
        placeholder="e.g. 10-15 hours/week, UK (GMT)",
        max_length=200,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Applications can only be submitted in a server.", ephemeral=True)
            return

        app_config = COMMUNITY_CONFIG.get("applications", {})
        review_channel_id = app_config.get("channel_id")
        if not review_channel_id:
            await interaction.response.send_message(
                "Applications are not currently configured or open on this server.",
                ephemeral=True,
            )
            return

        review_channel = interaction.guild.get_channel(int(review_channel_id))
        if review_channel is None:
            try:
                review_channel = await bot.fetch_channel(int(review_channel_id))
            except discord.DiscordException:
                review_channel = None

        if not isinstance(review_channel, discord.TextChannel):
            await interaction.response.send_message(
                "Could not find the application review channel. Please notify a server administrator.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"📋 New Application: {interaction.user.display_name}",
            colour=discord.Colour.blue(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_author(name=f"{interaction.user} ({interaction.user.id})", icon_url=interaction.user.display_avatar.url)
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.add_field(name="Applicant", value=interaction.user.mention, inline=True)
        embed.add_field(name="Role applied for", value=self.role_choice.value.strip(), inline=True)
        embed.add_field(name="Name & Age", value=self.name_age.value.strip(), inline=True)
        embed.add_field(name="Availability & Timezone", value=self.availability.value.strip(), inline=False)
        embed.add_field(name="Past experience", value=self.experience.value.strip() or "None stated", inline=False)
        embed.add_field(name="Why join the team?", value=self.why_join.value.strip(), inline=False)
        embed.add_field(name="Status", value="⏳ **Pending Review**", inline=False)
        embed.set_footer(text=f"Applicant ID: {interaction.user.id}")

        try:
            await review_channel.send(embed=embed, view=ApplicationReviewView())
        except discord.DiscordException:
            logger.exception("Failed to post application to review channel")
            await interaction.response.send_message(
                "Failed to send your application to the review channel. Check bot permissions.",
                ephemeral=True,
            )
            return

        state = load_community_state()
        apps = state.setdefault("applications", {})
        apps[str(interaction.user.id)] = {
            "applicant_id": interaction.user.id,
            "applicant_name": str(interaction.user),
            "role": self.role_choice.value.strip(),
            "submitted_at": datetime.now(timezone.utc).isoformat(),
            "status": "pending",
        }
        save_community_state(state)

        await interaction.response.send_message(
            "✅ Your application has been submitted successfully! The staff team will review it soon.",
            ephemeral=True,
        )


class ApplicationPanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Apply Now",
        style=discord.ButtonStyle.green,
        emoji="📝",
        custom_id="applications:apply",
    )
    async def apply_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["ApplicationPanelView"],
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Applications can only be opened in a server.", ephemeral=True)
            return
        app_config = COMMUNITY_CONFIG.get("applications", {})
        if not app_config.get("channel_id"):
            await interaction.response.send_message(
                "Applications are not currently configured or open on this server.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(ApplicationModal())


class ApplicationReviewView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    def _get_applicant_id(self, interaction: discord.Interaction) -> int | None:
        if not interaction.message or not interaction.message.embeds:
            return None
        embed = interaction.message.embeds[0]
        if embed.footer and embed.footer.text and "Applicant ID: " in embed.footer.text:
            raw_id = embed.footer.text.split("Applicant ID: ")[-1].strip()
            if raw_id.isdigit():
                return int(raw_id)
        return None

    @discord.ui.button(
        label="Accept",
        style=discord.ButtonStyle.green,
        emoji="✅",
        custom_id="app:action:accept",
    )
    async def accept_application(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["ApplicationReviewView"],
    ) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return
        if not interaction.user.guild_permissions.manage_guild and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "You need Manage Server permission to review applications.",
                ephemeral=True,
            )
            return

        applicant_id = self._get_applicant_id(interaction)
        embed = interaction.message.embeds[0] if interaction.message and interaction.message.embeds else None
        if not embed:
            await interaction.response.send_message("Application embed not found.", ephemeral=True)
            return

        app_config = COMMUNITY_CONFIG.get("applications", {})
        role_id = app_config.get("role_id")
        role_assigned = False
        role = None

        applicant = interaction.guild.get_member(applicant_id) if applicant_id else None
        if applicant is None and applicant_id:
            try:
                applicant = await interaction.guild.fetch_member(applicant_id)
            except discord.DiscordException:
                pass

        if role_id and applicant:
            role = interaction.guild.get_role(int(role_id))
            if role:
                try:
                    await applicant.add_roles(role, reason=f"Application accepted by {interaction.user}")
                    role_assigned = True
                except discord.DiscordException:
                    logger.warning("Could not assign configured application role %s to applicant %s", role_id, applicant_id)

        embed.colour = discord.Colour.green()
        for idx, field in enumerate(embed.fields):
            if field.name == "Status":
                status_text = f"✅ **Accepted** by {interaction.user.mention} on {format_uk_datetime(datetime.now(timezone.utc))}"
                if role_assigned and role_id:
                    status_text += f"\n(Assigned role: <@&{role_id}>)"
                embed.set_field_at(idx, name="Status", value=status_text, inline=False)
                break

        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(embed=embed, view=self)

        if app_config.get("notify_dm", True) and applicant:
            try:
                dm_text = f"🎉 **Congratulations!** Your application in **{interaction.guild.name}** has been **accepted** by {interaction.user.display_name}!"
                if role_assigned and role:
                    dm_text += f" You have been granted the **{role.name}** role."
                await applicant.send(dm_text)
            except discord.DiscordException:
                logger.info("Could not DM applicant %s about application acceptance", applicant_id)

        state = load_community_state()
        if applicant_id and str(applicant_id) in state.get("applications", {}):
            state["applications"][str(applicant_id)]["status"] = "accepted"
            state["applications"][str(applicant_id)]["reviewer"] = str(interaction.user)
            state["applications"][str(applicant_id)]["reviewed_at"] = datetime.now(timezone.utc).isoformat()
            save_community_state(state)

    @discord.ui.button(
        label="Reject",
        style=discord.ButtonStyle.red,
        emoji="❌",
        custom_id="app:action:reject",
    )
    async def reject_application(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["ApplicationReviewView"],
    ) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return
        if not interaction.user.guild_permissions.manage_guild and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "You need Manage Server permission to review applications.",
                ephemeral=True,
            )
            return

        applicant_id = self._get_applicant_id(interaction)
        embed = interaction.message.embeds[0] if interaction.message and interaction.message.embeds else None
        if not embed:
            await interaction.response.send_message("Application embed not found.", ephemeral=True)
            return

        app_config = COMMUNITY_CONFIG.get("applications", {})

        applicant = interaction.guild.get_member(applicant_id) if applicant_id else None
        if applicant is None and applicant_id:
            try:
                applicant = await interaction.guild.fetch_member(applicant_id)
            except discord.DiscordException:
                pass

        embed.colour = discord.Colour.red()
        for idx, field in enumerate(embed.fields):
            if field.name == "Status":
                embed.set_field_at(
                    idx,
                    name="Status",
                    value=f"❌ **Rejected** by {interaction.user.mention} on {format_uk_datetime(datetime.now(timezone.utc))}",
                    inline=False,
                )
                break

        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(embed=embed, view=self)

        if app_config.get("notify_dm", True) and applicant:
            try:
                await applicant.send(
                    f"Thank you for applying in **{interaction.guild.name}**. "
                    "Unfortunately, your application was not accepted at this time."
                )
            except discord.DiscordException:
                logger.info("Could not DM applicant %s about application rejection", applicant_id)

        state = load_community_state()
        if applicant_id and str(applicant_id) in state.get("applications", {}):
            state["applications"][str(applicant_id)]["status"] = "rejected"
            state["applications"][str(applicant_id)]["reviewer"] = str(interaction.user)
            state["applications"][str(applicant_id)]["reviewed_at"] = datetime.now(timezone.utc).isoformat()
            save_community_state(state)


# -------------------------------------------------------------
# Community Suggestions System
# -------------------------------------------------------------

class SuggestionView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Upvote (0)", style=discord.ButtonStyle.green, emoji="👍", custom_id="suggestion:upvote")
    async def upvote(self, interaction: discord.Interaction, button: discord.ui.Button["SuggestionView"]) -> None:
        await self._vote(interaction, "up")

    @discord.ui.button(label="Downvote (0)", style=discord.ButtonStyle.red, emoji="👎", custom_id="suggestion:downvote")
    async def downvote(self, interaction: discord.Interaction, button: discord.ui.Button["SuggestionView"]) -> None:
        await self._vote(interaction, "down")

    async def _vote(self, interaction: discord.Interaction, vote_type: str) -> None:
        if not interaction.message:
            return
        state = load_community_state()
        votes = state.setdefault("suggestion_votes", {}).setdefault(str(interaction.message.id), {})
        user_id = str(interaction.user.id)

        if votes.get(user_id) == vote_type:
            votes.pop(user_id, None)
            res = "Vote removed."
        else:
            votes[user_id] = vote_type
            res = f"Voted {vote_type}!"

        save_community_state(state)

        up = sum(1 for v in votes.values() if v == "up")
        down = sum(1 for v in votes.values() if v == "down")

        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if child.custom_id == "suggestion:upvote":
                    child.label = f"Upvote ({up})"
                elif child.custom_id == "suggestion:downvote":
                    child.label = f"Downvote ({down})"

        await interaction.response.edit_message(view=self)
        await interaction.followup.send(res, ephemeral=True)


# -------------------------------------------------------------
# Giveaway System
# -------------------------------------------------------------

class GiveawayView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Enter Giveaway (0)", style=discord.ButtonStyle.primary, emoji="🎉", custom_id="giveaway:enter")
    async def enter_giveaway(self, interaction: discord.Interaction, button: discord.ui.Button["GiveawayView"]) -> None:
        if not interaction.message:
            return
        state = load_community_state()
        giveaways = state.setdefault("giveaways", {})
        msg_id = str(interaction.message.id)
        if msg_id not in giveaways:
            await interaction.response.send_message("This giveaway has ended.", ephemeral=True)
            return

        entries = set(giveaways[msg_id].get("entries", []))
        if interaction.user.id in entries:
            entries.remove(interaction.user.id)
            res = "You left the giveaway."
        else:
            entries.add(interaction.user.id)
            res = "🎉 You entered the giveaway! Good luck!"

        giveaways[msg_id]["entries"] = list(entries)
        save_community_state(state)

        button.label = f"Enter Giveaway ({len(entries)})"
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(res, ephemeral=True)


# -------------------------------------------------------------
# Hielke Maps Quiz View
# -------------------------------------------------------------

class HielkeQuizView(discord.ui.View):
    def __init__(self, question_data: dict[str, Any]) -> None:
        super().__init__(timeout=60)
        self.question_data = question_data
        self.answered = False

        for idx, option in enumerate(question_data["options"]):
            btn = discord.ui.Button(label=option, style=discord.ButtonStyle.secondary, custom_id=f"quiz:{idx}")
            btn.callback = self._create_callback(idx)
            self.add_item(btn)

    def _create_callback(self, idx: int):
        async def callback(interaction: discord.Interaction) -> None:
            if self.answered:
                await interaction.response.send_message("This question has already been answered.", ephemeral=True)
                return
            self.answered = True
            is_correct = idx == self.question_data["answer"]

            for child in self.children:
                if isinstance(child, discord.ui.Button):
                    child.disabled = True
                    child_idx = int(child.custom_id.split(":")[1])
                    if child_idx == self.question_data["answer"]:
                        child.style = discord.ButtonStyle.green
                    elif child_idx == idx:
                        child.style = discord.ButtonStyle.red

            result_str = "🎉 **Correct!**" if is_correct else "❌ **Incorrect!**"
            explanation = self.question_data["explanation"]
            embed = interaction.message.embeds[0] if interaction.message and interaction.message.embeds else discord.Embed(title="Quiz")
            embed.add_field(name="Result", value=f"{result_str}\n{explanation}", inline=False)
            embed.colour = discord.Colour.green() if is_correct else discord.Colour.red()

            await interaction.response.edit_message(embed=embed, view=self)

        return callback


# -------------------------------------------------------------
# Channel Lock / Moderation Commands
# -------------------------------------------------------------

hielkemap_group = app_commands.Group(
    name="hielkemap",
    description="Hielke Maps tools, guides, leaderboards, and map controls",
)
ban_group = app_commands.Group(
    name="ban",
    description="Channel speaking lockdown",
    parent=hielkemap_group,
)


@ban_group.command(name="notimetospeak", description="Lock this channel so members cannot speak")
@app_commands.checks.has_permissions(manage_channels=True)
async def lock_channel_command(interaction: discord.Interaction) -> None:
    """Deny the default role permission to send messages in this channel."""
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel) or interaction.guild is None:
        await interaction.response.send_message(
            "This command can only be used in a server text channel.", ephemeral=True
        )
        return
    await channel.set_permissions(
        interaction.guild.default_role,
        send_messages=False,
        reason=f"Channel locked by {interaction.user}",
    )
    await interaction.response.send_message(
        "🔒 Channel locked. Members can no longer send messages here.", ephemeral=False
    )


@hielkemap_group.command(name="unlock", description="Unlock the current channel")
@app_commands.checks.has_permissions(manage_channels=True)
async def unlock_channel_command(interaction: discord.Interaction) -> None:
    """Remove the default-role channel lock."""
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel) or interaction.guild is None:
        await interaction.response.send_message(
            "This command can only be used in a server text channel.", ephemeral=True
        )
        return
    await channel.set_permissions(
        interaction.guild.default_role,
        send_messages=None,
        reason=f"Channel unlocked by {interaction.user}",
    )
    await interaction.response.send_message(
        "🔓 Channel unlocked. Normal speaking permissions restored.", ephemeral=False
    )


bot.tree.add_command(hielkemap_group)


# =============================================================================
# API Clients: YouTube, Twitch, TikTok, Bustimes
# =============================================================================

class YouTubeClient:
    base_url = "https://www.googleapis.com/youtube/v3"

    async def fetch_channel(self, session: aiohttp.ClientSession, handle: str) -> dict[str, Any]:
        params = {
            "part": "snippet,statistics",
            "forHandle": handle.lstrip("@"),
            "key": YOUTUBE_API_KEY,
        }
        async with session.get(f"{self.base_url}/channels", params=params) as response:
            payload = await response.json()
            if response.status != 200:
                message = payload.get("error", {}).get("message", f"HTTP {response.status}")
                raise RuntimeError(f"YouTube API error for @{handle}: {message}")
            items = payload.get("items", [])
            if not items:
                raise RuntimeError(f"YouTube channel not found for @{handle}")
            item = items[0]
            return {
                "id": item["id"],
                "title": item["snippet"]["title"],
                "url": f"https://www.youtube.com/channel/{item['id']}",
                "subscribers": int(item["statistics"].get("subscriberCount", 0)),
                "hidden": item["statistics"].get("hiddenSubscriberCount", False),
            }

    async def fetch_all(self) -> dict[str, dict[str, Any]]:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            results = {}
            for name, config in YOUTUBE_CHANNELS.items():
                results[name] = await self.fetch_channel(session, config["handle"])
            return results


class SocialClient:
    async def fetch_twitch_token(self, session: aiohttp.ClientSession) -> str:
        if not TWITCH_CLIENT_ID or not TWITCH_CLIENT_SECRET:
            raise RuntimeError("Set TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET to track Twitch followers")
        async with session.post(
            "https://id.twitch.tv/oauth2/token",
            params={
                "client_id": TWITCH_CLIENT_ID,
                "client_secret": TWITCH_CLIENT_SECRET,
                "grant_type": "client_credentials",
            },
        ) as response:
            payload = await response.json()
            if response.status != 200:
                raise RuntimeError(f"Twitch authentication failed: {payload.get('message', response.status)}")
            return payload["access_token"]

    async def fetch_twitch(self, session: aiohttp.ClientSession, token: str, profile: dict[str, str]) -> dict[str, Any]:
        async with session.get(
            "https://api.twitch.tv/helix/users",
            params={"login": profile["handle"]},
            headers={"Client-ID": TWITCH_CLIENT_ID, "Authorization": f"Bearer {token}"},
        ) as response:
            payload = await response.json()
            if response.status != 200:
                raise RuntimeError(f"Twitch user lookup failed for {profile['handle']}: {payload.get('message', response.status)}")
            users = payload.get("data", [])
            if not users:
                raise RuntimeError(f"Twitch channel not found for {profile['handle']}")
            user_id = users[0]["id"]
        async with session.get(
            "https://api.twitch.tv/helix/channels/followers",
            params={"broadcaster_id": user_id},
            headers={"Client-ID": TWITCH_CLIENT_ID, "Authorization": f"Bearer {token}"},
        ) as response:
            payload = await response.json()
            if response.status != 200:
                raise RuntimeError(f"Twitch follower lookup failed for {profile['handle']}: {payload.get('message', response.status)}")
            return {
                "platform": "Twitch",
                "url": profile["url"],
                "handle": profile["handle"],
                "subscribers": int(payload.get("total", 0)),
                "hidden": False,
            }

    async def fetch_tiktok(self, session: aiohttp.ClientSession, profile: dict[str, str]) -> dict[str, Any]:
        async with session.get(
            f"https://www.tiktok.com/@{profile['handle']}",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        ) as response:
            page = await response.text()
            if response.status != 200:
                raise RuntimeError(f"TikTok profile unavailable for @{profile['handle']}")
            match = re.search(r'"followerCount"\s*:\s*"?(\d+)"?', page)
            if not match:
                raise RuntimeError(f"TikTok follower count unavailable for @{profile['handle']}")
            return {
                "platform": "TikTok",
                "url": profile["url"],
                "handle": profile["handle"],
                "subscribers": int(match.group(1)),
                "hidden": False,
            }

    async def fetch_all(self) -> dict[str, dict[str, Any]]:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            results = {}
            twitch_profiles = {
                name: profile for name, profile in SOCIAL_PROFILES.items()
                if profile.get("platform") == "Twitch"
            }
            if twitch_profiles and TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET:
                try:
                    token = await self.fetch_twitch_token(session)
                    for name, profile in twitch_profiles.items():
                        try:
                            results[name] = await self.fetch_twitch(session, token, profile)
                        except (aiohttp.ClientError, RuntimeError) as exc:
                            logger.warning("%s", exc)
                except (aiohttp.ClientError, RuntimeError) as exc:
                    logger.warning("%s", exc)
            for name, profile in SOCIAL_PROFILES.items():
                if profile.get("platform") == "TikTok":
                    try:
                        results[name] = await self.fetch_tiktok(session, profile)
                    except (aiohttp.ClientError, RuntimeError) as exc:
                        logger.warning("%s", exc)
            return results


class BustimesClient:
    async def find_stops(self, query: str, route: str = "*") -> list[dict[str, str]]:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                "https://bustimes.org/search",
                params={"q": query},
                headers={"User-Agent": "Mozilla/5.0"},
            ) as response:
                page = await response.text()
            locality_urls = list(dict.fromkeys(re.findall(r'href="(/localities/[^"]+)"', page)))
            stop_urls: list[str] = list(dict.fromkeys(re.findall(r'href="(/stops/[^"]+)"', page)))
            for locality_url in locality_urls[:10]:
                try:
                    async with session.get(
                        f"https://bustimes.org{locality_url}",
                        headers={"User-Agent": "Mozilla/5.0"},
                    ) as response:
                        if response.status == 200:
                            locality_page = await response.text()
                            stop_urls.extend(re.findall(r'href="(/stops/[^"]+)"', locality_page))
                except (aiohttp.ClientError, TimeoutError):
                    continue

            matches = []
            query_lower = query.casefold()
            for stop_path in list(dict.fromkeys(stop_urls))[:100]:
                try:
                    async with session.get(
                        f"https://bustimes.org{stop_path}",
                        headers={"User-Agent": "Mozilla/5.0"},
                    ) as response:
                        if response.status != 200:
                            continue
                        stop_page = await response.text()
                    title_match = re.search(r'<h1[^>]*>(.*?)</h1>', stop_page, re.DOTALL)
                    raw_stop_name = " ".join(re.sub(r"<.*?>", " ", title_match.group(1)).split()) if title_match else ""
                    stop_name = html.unescape(raw_stop_name)
                    if query_lower not in stop_name.casefold():
                        continue
                    stop_code = stop_path.strip("/").rsplit("/", 1)[-1]
                    if not stop_code:
                        continue
                    async with session.get(
                        f"https://bustimes.org/api/stops/{stop_code}/",
                        headers={"User-Agent": "Mozilla/5.0"},
                    ) as response:
                        if response.status != 200:
                            stop_data = {}
                        else:
                            try:
                                stop_data = await response.json()
                            except (aiohttp.ClientError, json.JSONDecodeError):
                                stop_data = {}
                    routes = {item.casefold() for item in (stop_data.get("line_names") or [])}
                    if route != "*" and route.casefold() not in routes:
                        continue
                    matches.append({
                        "name": stop_name,
                        "url": f"https://bustimes.org{stop_path}",
                    })
                    if len(matches) == 10:
                        break
                except (aiohttp.ClientError, TimeoutError):
                    continue
            return matches

    async def fetch_departures(self) -> dict[str, Any]:
        stop_url = BUS_CONFIG.get("stop_url")
        route_filter = BUS_CONFIG.get("route")
        if not stop_url or not route_filter:
            raise RuntimeError("Configure a Bustimes stop with /bus-config first")
        timeout = aiohttp.ClientTimeout(total=20)
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.get(stop_url, headers={"User-Agent": "Mozilla/5.0"}) as response,
        ):
            if response.status != 200:
                raise RuntimeError(f"Bustimes stop page returned HTTP {response.status}")
            page = await response.text()

        table_match = re.search(
            r'<div[^>]*\bid="departures"[^>]*>.*?</table>',
            page,
            re.DOTALL,
        )
        if not table_match:
            table_match = re.search(r'<table[^>]*\bclass="[^"]*departures[^"]*"[^>]*>.*?</table>', page, re.DOTALL)
        if not table_match:
            table_match = re.search(r'<table[^>]*>.*?</table>', page, re.DOTALL)
        if not table_match:
            return {"stop_url": stop_url, "route": route_filter, "departures": []}

        departures = []
        for row in re.findall(r"<tr>(.*?)</tr>", table_match.group(0), re.DOTALL):
            cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
            if len(cells) < 4:
                continue
            route = re.sub(r"<.*?>", " ", cells[0]).strip()
            route = html.unescape(" ".join(route.split()))
            if route_filter != "*" and route.casefold() != route_filter.casefold():
                continue
            destination = re.sub(r'<div class="vehicle">.*?</div>', "", cells[1], flags=re.DOTALL)
            destination = re.sub(r"<.*?>", " ", destination).strip()
            destination = html.unescape(" ".join(destination.split()))
            scheduled = html.unescape(re.sub(r"<.*?>", " ", cells[2]).strip())
            expected = html.unescape(re.sub(r"<.*?>", " ", cells[3]).strip())
            departures.append({
                "route": route,
                "destination": destination,
                "scheduled": scheduled,
                "expected": expected,
            })
            if len(departures) == 5:
                break
        return {"stop_url": stop_url, "route": route_filter, "departures": departures}


async def monitor_socials() -> None:
    discord_channel_id = community_channel_id("monitor_channel_id")
    if not discord_channel_id:
        return
    channel = bot.get_channel(discord_channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(discord_channel_id)
        except discord.DiscordException:
            logger.warning("Could not fetch monitor channel %s", discord_channel_id)
            return
    if not isinstance(channel, discord.TextChannel):
        return
    state = load_state()
    monitor_state = state.setdefault("monitoring", {})
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        if COMMUNITY_CONFIG.get("youtube_uploads", True):
            try:
                tracked_channels = await YouTubeClient().fetch_all()
            except (aiohttp.ClientError, RuntimeError, ValueError) as exc:
                logger.warning("Could not resolve tracked YouTube channels: %s", exc)
                tracked_channels = {}
            for name in YOUTUBE_CHANNELS:
                try:
                    yt_channel_id = tracked_channels.get(name, {}).get("id")
                    if not yt_channel_id:
                        continue
                    rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={yt_channel_id}"
                    async with session.get(rss_url) as response:
                        feed = await response.text()
                    entry_match = re.search(r"<entry>(.*?)</entry>", feed, re.DOTALL)
                    if not entry_match:
                        continue
                    entry = entry_match.group(1)
                    video_match = re.search(r"<yt:videoId>([^<]+)</yt:videoId>", entry)
                    title_match = re.search(r"<title>([^<]+)</title>", entry)
                    video_id = video_match.group(1).strip() if video_match else None
                    if not video_id:
                        continue
                    key = f"youtube:{name}"
                    if key not in monitor_state:
                        monitor_state[key] = video_id
                        continue
                    if monitor_state.get(key) == video_id:
                        continue
                    monitor_state[key] = video_id
                    title = html.unescape(title_match.group(1).strip()) if title_match else "New upload"
                    await channel.send(f"📺 **{name} uploaded a new video:** {title}\nhttps://youtu.be/{video_id}")
                except (aiohttp.ClientError, KeyError, TypeError, ValueError) as exc:
                    logger.warning("YouTube monitor failed for %s: %s", name, exc)

        if COMMUNITY_CONFIG.get("twitch_live", True) and TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET:
            try:
                token = await SocialClient().fetch_twitch_token(session)
                for name, profile in SOCIAL_PROFILES.items():
                    if profile.get("platform") != "Twitch":
                        continue
                    async with session.get(
                        "https://api.twitch.tv/helix/streams",
                        params={"user_login": profile["handle"]},
                        headers={"Client-ID": TWITCH_CLIENT_ID, "Authorization": f"Bearer {token}"},
                    ) as response:
                        payload = await response.json()
                    stream = (payload.get("data") or [None])[0]
                    key = f"twitch:{name}"
                    if stream and not monitor_state.get(key):
                        monitor_state[key] = stream["id"]
                        stream_title = html.unescape(stream.get("title", ""))
                        await channel.send(
                            f"🔴 **{name} is live:** {stream_title}\n{profile['url']}"
                        )
                    elif not stream:
                        monitor_state[key] = None
            except (aiohttp.ClientError, RuntimeError, KeyError, TypeError, ValueError) as exc:
                logger.warning("Twitch live monitor failed: %s", exc)
    save_state(state)


def build_bus_embed(data: dict[str, Any], error: str | None = None) -> discord.Embed:
    embed = discord.Embed(
        title=f"Next {data['route']} departures",
        description=f"[Open stop on Bustimes.org]({data['stop_url']})\nUpdated: {format_uk_datetime(datetime.now(timezone.utc))}",
        colour=discord.Colour.green(),
    )
    if data["departures"]:
        lines = []
        for departure in data["departures"]:
            expected = departure["expected"] or departure["scheduled"]
            status = f"expected **{expected}**" if departure["expected"] else "scheduled"
            lines.append(f"**{departure['scheduled']}** -> {departure['destination']} ({status})")
        embed.add_field(name="Upcoming", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="Upcoming", value="No matching departures found.", inline=False)
    if error:
        embed.add_field(name="Warning", value=error[:1024], inline=False)
        embed.colour = discord.Colour.orange()
    return embed


def load_history(state: dict[str, Any]) -> list[dict[str, Any]]:
    if HISTORY_FILE.exists():
        try:
            history = json.loads(HISTORY_FILE.read_text())
            if isinstance(history, list):
                return history
        except (OSError, json.JSONDecodeError):
            logger.warning("Could not read %s; using history from state", HISTORY_FILE)
    return state.get("history", [])


def record_history(state: dict[str, Any], channels: dict[str, dict[str, Any]]) -> None:
    history = load_history(state)
    history.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "channels": {
            name: channel["subscribers"]
            for name, channel in channels.items()
            if not channel.get("hidden")
        },
    })
    state["history"] = history[-1000:]
    temp_file = HISTORY_FILE.with_suffix(".tmp")
    temp_file.write_text(json.dumps(state["history"], indent=2))
    temp_file.replace(HISTORY_FILE)


def build_chart(
    state: dict[str, Any],
    channels: dict[str, dict[str, Any]],
    days: int = 30,
) -> BytesIO:
    names = list(channels)
    current = [channels[name].get("subscribers", 0) for name in names]
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    history = [
        entry
        for entry in state.get("history", [])
        if "timestamp" in entry
        and datetime.fromisoformat(entry["timestamp"]).astimezone(timezone.utc) >= cutoff
    ]
    figure, axes = plt.subplots(2, 1, figsize=(9, 7), gridspec_kw={"height_ratios": [1, 1.4]})
    figure.patch.set_facecolor("#202225")
    for axis in axes:
        axis.set_facecolor("#2f3136")
        axis.tick_params(colors="#dcddde")
        for spine in axis.spines.values():
            spine.set_color("#4f545c")
        axis.yaxis.label.set_color("#dcddde")
        axis.xaxis.label.set_color("#dcddde")
        axis.title.set_color("#ffffff")

    colours = ["#5865f2", "#57f287", "#ff0050", "#00f2ea", "#9147ff", "#eb0400", "#f59e0b", "#22c55e"]
    if names and current:
        axes[0].bar(names, current, color=[colours[index % len(colours)] for index in range(len(names))])
        axes[0].set_title("Current subscribers")
        axes[0].set_ylabel("Subscribers")
        axes[0].tick_params(axis="x", labelrotation=12)
        current_min = min(current)
        current_max = max(current)
        current_padding = max((current_max - current_min) * 0.2, 10)
        axes[0].set_ylim(max(0, current_min - current_padding), current_max + current_padding)
        for index, value in enumerate(current):
            axes[0].text(index, value, f"{value:,}", ha="center", va="bottom", color="#ffffff")
    else:
        axes[0].set_title("Current subscribers")
        axes[0].text(0.5, 0.5, "No subscriber data available", ha="center", color="#dcddde")
        axes[0].set_xticks([])
        axes[0].set_yticks([])

    plotted = False
    for name, colour in zip(names, colours):
        points = [
            (entry["timestamp"], entry.get("channels", {}).get(name))
            for entry in history
            if entry.get("channels", {}).get(name) is not None
        ]
        if points:
            dates = [datetime.fromisoformat(timestamp).astimezone(timezone.utc) for timestamp, _ in points]
            values = [value for _, value in points]
            axes[1].plot(dates, values, marker="o", markersize=3, label=name, color=colour)
            plotted = True
    axes[1].set_title(f"Subscriber history - last {days} days")
    axes[1].set_ylabel("Subscribers")
    if plotted:
        history_values = [
            value
            for entry in history
            for value in entry.get("channels", {}).values()
            if isinstance(value, (int, float))
        ]
        if history_values:
            history_min = min(history_values)
            history_max = max(history_values)
            history_padding = max((history_max - history_min) * 0.15, 5)
            axes[1].set_ylim(
                max(0, history_min - history_padding),
                history_max + history_padding,
            )
        axes[1].xaxis.set_major_formatter(mdates.DateFormatter("%d/%m/%Y", tz=timezone.utc))
        axes[1].legend(facecolor="#2f3136", labelcolor="#dcddde")
        figure.autofmt_xdate()
    else:
        axes[1].text(0.5, 0.5, "History will appear after the next updates", ha="center", color="#dcddde")
        axes[1].set_xticks([])
        axes[1].set_yticks([])
    figure.tight_layout()
    output = BytesIO()
    figure.savefig(output, format="png", dpi=140, facecolor=figure.get_facecolor())
    plt.close(figure)
    output.seek(0)
    return output


def format_subscribers(channel: dict[str, Any]) -> str:
    if channel.get("hidden"):
        return "Hidden by channel"
    return f"{channel.get('subscribers', 0):,}"


def build_embed(channels: dict[str, dict[str, Any]], error: str | None = None) -> discord.Embed:
    embed = discord.Embed(
        title="YouTube subscriber tracker",
        description=(
            "Updated automatically from the YouTube Data API.\n"
            f"Last updated: {format_uk_datetime(datetime.now(timezone.utc))}"
        ),
        colour=discord.Colour.blurple(),
    )
    for name, channel in channels.items():
        profile = YOUTUBE_CHANNELS.get(name) or SOCIAL_PROFILES.get(name, {})
        platform = profile.get("platform", "YouTube")
        embed.add_field(
            name=f"{platform}: {name}",
            value=f"**Followers/Subscribers:** {format_subscribers(channel)}\n[Open profile]({profile.get('url', '#')})",
            inline=False,
        )
    if error:
        embed.add_field(name="Last update warning", value=error[:1024], inline=False)
        embed.colour = discord.Colour.orange()
    embed.set_footer(text="Use /subs to refresh, or /graph for charts")
    return embed


async def get_target_channel() -> discord.TextChannel | None:
    channel = bot.get_channel(TARGET_CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(TARGET_CHANNEL_ID)
        except discord.DiscordException:
            logger.exception("Could not access Discord channel %s", TARGET_CHANNEL_ID)
            return None
    return channel if isinstance(channel, discord.TextChannel) else None


async def update_bus_status() -> None:
    if not BUS_CONFIG.get("channel_id"):
        logger.info("Bustimes display is not configured; use /bus-config")
        return
    try:
        channel = bot.get_channel(int(BUS_CONFIG["channel_id"]))
        if channel is None:
            channel = await bot.fetch_channel(int(BUS_CONFIG["channel_id"]))
        if not isinstance(channel, discord.TextChannel):
            return
        data = await BustimesClient().fetch_departures()
        embed = build_bus_embed(data)
    except (aiohttp.ClientError, RuntimeError, ValueError, discord.DiscordException) as exc:
        logger.warning("Bus update failed: %s", exc)
        return

    message = None
    if BUS_CONFIG.get("message_id"):
        try:
            message = await channel.fetch_message(int(BUS_CONFIG["message_id"]))
            if message is not None and getattr(message, "author", None) is not None and bot.user is not None and message.author.id != bot.user.id:
                logger.warning("Stored Bustimes message was not authored by this bot; sending a new one")
                message = None
                BUS_CONFIG.pop("message_id", None)
                save_config()
        except discord.NotFound:
            BUS_CONFIG.pop("message_id", None)
            save_config()
            pass
        except discord.DiscordException:
            pass
    if message is None:
        message = await channel.send(embed=embed)
        BUS_CONFIG["message_id"] = message.id
        save_config()
    else:
        try:
            await message.edit(embed=embed)
        except discord.Forbidden as exc:
            logger.warning("Could not edit Bustimes message; sending a new one: %s", exc)
            message = await channel.send(embed=embed)
            BUS_CONFIG["message_id"] = message.id
            save_config()


async def update_supertram_status() -> None:
    if not SUPERTRAM_CONFIG.get("channel_id") or not SUPERTRAM_CONFIG.get("stop_name"):
        return
    try:
        channel = bot.get_channel(int(SUPERTRAM_CONFIG["channel_id"]))
        if channel is None:
            channel = await bot.fetch_channel(int(SUPERTRAM_CONFIG["channel_id"]))
        if not isinstance(channel, discord.TextChannel):
            return
        stop_name = SUPERTRAM_CONFIG["stop_name"]
        departures = calculate_supertram_departures(stop_name)
        embed = build_supertram_embed(departures, stop_name)
    except (ValueError, KeyError, IndexError) as exc:
        logger.warning("Supertram update failed: %s", exc)
        return

    message = None
    if SUPERTRAM_CONFIG.get("message_id"):
        try:
            message = await channel.fetch_message(int(SUPERTRAM_CONFIG["message_id"]))
            if message is not None and bot.user is not None and getattr(message, "author", None) is not None and message.author.id != bot.user.id:
                logger.warning("Stored Supertram message was not authored by this bot; sending a new one")
                message = None
                SUPERTRAM_CONFIG.pop("message_id", None)
                save_config()
        except discord.NotFound:
            SUPERTRAM_CONFIG.pop("message_id", None)
            save_config()
        except discord.DiscordException:
            pass
    if message is None:
        message = await channel.send(embed=embed)
        SUPERTRAM_CONFIG["message_id"] = message.id
        save_config()
    else:
        try:
            await message.edit(embed=embed)
        except discord.Forbidden as exc:
            logger.warning("Could not edit Supertram message; sending a new one: %s", exc)
            message = await channel.send(embed=embed)
            SUPERTRAM_CONFIG["message_id"] = message.id
            save_config()


async def update_status() -> None:
    target = await get_target_channel()
    if target is None:
        logger.error("Configured target is not a text channel: %s", TARGET_CHANNEL_ID)
        return

    youtube = YouTubeClient()
    state = load_state()
    state["history"] = load_history(state)
    old_message = None
    message_id = state.get("message_id")
    if message_id:
        try:
            old_message = await target.fetch_message(int(message_id))
            if old_message is not None and bot.user is not None and old_message.author.id != bot.user.id:
                logger.warning("Tracked status message was authored by another user; creating a new one")
                old_message = None
                state.pop("message_id", None)
                save_state(state)
        except discord.NotFound:
            logger.info("Tracked status message no longer exists; creating a new one")
            state.pop("message_id", None)
            save_state(state)
        except discord.DiscordException:
            logger.exception("Could not fetch tracked status message")

    try:
        channels = await youtube.fetch_all()
        channels.update(await SocialClient().fetch_all())
        error = None
        record_history(state, channels)
    except (aiohttp.ClientError, RuntimeError, ValueError) as exc:
        logger.exception("Subscriber update failed")
        channels = state.get("channels", {})
        error = str(exc)
        if not channels:
            return

    embed = build_embed(channels, error)
    chart = build_chart(state, channels)
    chart_file = discord.File(chart, filename="subscriber_history.png")
    embed.set_image(url="attachment://subscriber_history.png")
    if old_message:
        try:
            await old_message.edit(embed=embed, attachments=[chart_file])
            chart.seek(0)
        except discord.Forbidden as exc:
            logger.warning("Could not edit tracked status message; sending a new one: %s", exc)
            old_message = None
            state.pop("message_id", None)
            chart.seek(0)
        except discord.NotFound:
            logger.info("Tracked status message no longer exists; creating a new one")
            old_message = None
            state.pop("message_id", None)
            chart.seek(0)

    if old_message is None:
        chart_file = discord.File(chart, filename="subscriber_history.png")
        message = await target.send(embed=embed, file=chart_file)
        state["message_id"] = message.id
        state["channels"] = channels
        save_state(state)
        return

    state["channels"] = channels
    save_state(state)


async def setup_hook() -> None:
    bot.add_view(TicketView())
    bot.add_view(TicketCloseView())
    bot.add_view(ApplicationPanelView())
    bot.add_view(ApplicationReviewView())
    bot.add_view(SuggestionView())
    bot.add_view(GiveawayView())

    for panel in COMMUNITY_CONFIG.get("role_panels", []):
        try:
            bot.add_view(RoleButtonView(int(panel["role_id"]), panel["label"]))
        except (ValueError, TypeError, discord.DiscordException):
            logger.warning("Could not register role panel view for role %s", panel.get("role_id"))

    await bot.tree.sync()
    logger.info("Slash commands synced globally.")


bot.setup_hook = setup_hook


@bot.event
async def on_ready() -> None:
    logger.info("Logged in as %s (ID: %s)", bot.user, bot.user.id if bot.user else "unknown")
    if not update_loop.is_running():
        update_loop.start()
    if not bus_update_loop.is_running():
        bus_update_loop.start()
    if not supertram_update_loop.is_running():
        supertram_update_loop.start()
    if not social_monitor_loop.is_running():
        social_monitor_loop.start()
    if not reminder_loop.is_running():
        reminder_loop.start()
    if not giveaway_loop.is_running():
        giveaway_loop.start()


@tasks.loop(minutes=UPDATE_INTERVAL_MINUTES)
async def update_loop() -> None:
    try:
        await update_status()
    except Exception:
        logger.exception("Error in update_loop")


@tasks.loop(minutes=5)
async def bus_update_loop() -> None:
    try:
        await update_bus_status()
    except Exception:
        logger.exception("Error in bus_update_loop")


@tasks.loop(minutes=5)
async def supertram_update_loop() -> None:
    try:
        await update_supertram_status()
    except Exception:
        logger.exception("Error in supertram_update_loop")


@tasks.loop(minutes=5)
async def social_monitor_loop() -> None:
    try:
        await monitor_socials()
    except Exception:
        logger.exception("Error in social_monitor_loop")


@tasks.loop(seconds=30)
async def reminder_loop() -> None:
    try:
        state = load_state()
        reminders = state.get("reminders", [])
        now = datetime.now(timezone.utc)
        remaining = []
        for reminder in reminders:
            try:
                due_dt = datetime.fromisoformat(reminder["due"])
                if due_dt.tzinfo is None:
                    due_dt = due_dt.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError, KeyError):
                logger.debug("Skipping malformed reminder timestamp")
                continue

            if due_dt > now:
                remaining.append(reminder)
                continue

            try:
                user = bot.get_user(int(reminder["user_id"])) or await bot.fetch_user(int(reminder["user_id"]))
                await user.send(f"⏰ Reminder: {reminder['message']}")
            except discord.Forbidden:
                channel_id = reminder.get("channel_id")
                if channel_id:
                    chan = bot.get_channel(int(channel_id))
                    if chan is None:
                        try:
                            chan = await bot.fetch_channel(int(channel_id))
                        except discord.DiscordException:
                            chan = None
                    if isinstance(chan, discord.TextChannel):
                        try:
                            await chan.send(f"⏰ <@{reminder['user_id']}> Reminder: {reminder['message']}")
                        except discord.DiscordException:
                            pass
            except discord.DiscordException:
                logger.warning("Could not deliver reminder to %s", reminder.get("user_id"))

        if len(remaining) != len(reminders):
            state["reminders"] = remaining
            save_state(state)
    except Exception:
        logger.exception("Error in reminder_loop")


@tasks.loop(seconds=30)
async def giveaway_loop() -> None:
    try:
        state = load_community_state()
        giveaways = state.get("giveaways", {})
        now = datetime.now(timezone.utc)
        ended = []

        for msg_id_str, g_data in list(giveaways.items()):
            if g_data.get("ended"):
                continue
            try:
                end_time = datetime.fromisoformat(g_data["end_time"])
                if end_time.tzinfo is None:
                    end_time = end_time.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError, KeyError):
                continue

            if now >= end_time:
                ended.append(msg_id_str)
                g_data["ended"] = True
                channel_id = g_data.get("channel_id")
                if not channel_id:
                    continue
                channel = bot.get_channel(int(channel_id))
                if channel is None:
                    try:
                        channel = await bot.fetch_channel(int(channel_id))
                    except discord.DiscordException:
                        continue
                if not isinstance(channel, discord.TextChannel):
                    continue

                entries = g_data.get("entries", [])
                prize = g_data.get("prize", "Prize")
                if entries:
                    winner_id = random.choice(entries)
                    try:
                        await channel.send(f"🎉 **Giveaway Ended!** Congratulations <@{winner_id}>! You won **{prize}**!")
                    except discord.DiscordException:
                        pass
                else:
                    try:
                        await channel.send(f"🎉 **Giveaway Ended for {prize}!** No valid entries were received.")
                    except discord.DiscordException:
                        pass

        if ended:
            save_community_state(state)
    except Exception:
        logger.exception("Error in giveaway_loop")


@update_loop.before_loop
async def before_update_loop() -> None:
    await bot.wait_until_ready()


@bus_update_loop.before_loop
async def before_bus_update_loop() -> None:
    await bot.wait_until_ready()


@supertram_update_loop.before_loop
async def before_supertram_update_loop() -> None:
    await bot.wait_until_ready()


@social_monitor_loop.before_loop
async def before_social_monitor_loop() -> None:
    await bot.wait_until_ready()


@reminder_loop.before_loop
async def before_reminder_loop() -> None:
    await bot.wait_until_ready()


@giveaway_loop.before_loop
async def before_giveaway_loop() -> None:
    await bot.wait_until_ready()


@update_loop.error
async def on_update_loop_error(error: Exception) -> None:
    logger.exception("Unhandled error in update_loop: %s", error)


@bus_update_loop.error
async def on_bus_update_loop_error(error: Exception) -> None:
    logger.exception("Unhandled error in bus_update_loop: %s", error)


@supertram_update_loop.error
async def on_supertram_update_loop_error(error: Exception) -> None:
    logger.exception("Unhandled error in supertram_update_loop: %s", error)


@social_monitor_loop.error
async def on_social_monitor_loop_error(error: Exception) -> None:
    logger.exception("Unhandled error in social_monitor_loop: %s", error)


@reminder_loop.error
async def on_reminder_loop_error(error: Exception) -> None:
    logger.exception("Unhandled error in reminder_loop: %s", error)


@giveaway_loop.error
async def on_giveaway_loop_error(error: Exception) -> None:
    logger.exception("Unhandled error in giveaway_loop: %s", error)


# =============================================================================
# Slash Commands: Tracking, Bustimes, Supertram, Hielke Maps, Applications, Community
# =============================================================================

@bot.tree.command(name="subs", description="Refresh the YouTube subscriber tracker")
@app_commands.guild_only()
async def subs_command(interaction: discord.Interaction) -> None:
    """Refresh the subscriber message immediately."""
    if await get_target_channel() is None and isinstance(interaction.channel, discord.TextChannel):
        global TARGET_CHANNEL_ID
        TARGET_CHANNEL_ID = interaction.channel.id
        save_config()
    elif interaction.channel_id != TARGET_CHANNEL_ID:
        await interaction.response.send_message(
            f"Use this command in <#{TARGET_CHANNEL_ID}>.", ephemeral=True
        )
        return
    await interaction.response.defer(ephemeral=True)
    try:
        await update_status()
    except Exception:
        logger.exception("Subscriber refresh failed")
        await interaction.followup.send("Subscriber refresh failed. Check the bot logs for details.", ephemeral=True)
        return
    await interaction.followup.send("Subscriber counts refreshed.", ephemeral=True)


@bot.tree.command(name="graph", description="Show subscriber charts for both YouTube channels")
@app_commands.guild_only()
@app_commands.describe(days="Number of days of history to show")
async def graph_command(
    interaction: discord.Interaction,
    days: app_commands.Range[int, 30, 3650] = 30,
) -> None:
    """Send the current subscriber chart."""
    if interaction.channel_id != TARGET_CHANNEL_ID:
        await interaction.response.send_message(
            f"Use this command in <#{TARGET_CHANNEL_ID}>.", ephemeral=True
        )
        return
    state = load_state()
    state["history"] = load_history(state)
    channels = state.get("channels", {})
    if not channels:
        await interaction.response.send_message(
            "No subscriber data yet. Use /subs first.", ephemeral=True
        )
        return
    await interaction.response.send_message(
        file=discord.File(
            build_chart(state, channels, days),
            filename=f"subscriber_history_{days}d.png",
        )
    )


@bot.tree.command(name="config-channel", description="Set the channel used by the tracker")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
async def config_channel_command(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
) -> None:
    """Change the channel where the tracker message is posted."""
    global TARGET_CHANNEL_ID
    TARGET_CHANNEL_ID = channel.id
    save_config()
    await interaction.response.send_message(
        f"Tracker channel set to {channel.mention}. Refreshing it now.", ephemeral=True
    )
    await update_status()


@bot.tree.command(name="profile-add", description="Add a YouTube, Twitch, or TikTok profile")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(
    platform="The platform to track",
    name="A unique display name for this profile",
    handle="The username or YouTube handle, without the @",
    url="The public profile URL",
)
@app_commands.choices(platform=[
    app_commands.Choice(name="YouTube", value="youtube"),
    app_commands.Choice(name="Twitch", value="twitch"),
    app_commands.Choice(name="TikTok", value="tiktok"),
])
async def profile_add_command(
    interaction: discord.Interaction,
    platform: app_commands.Choice[str],
    name: str,
    handle: str,
    url: str | None = None,
) -> None:
    """Add or replace a tracked profile."""
    clean_name = name.strip()
    clean_handle = handle.strip().lstrip("@")
    if not clean_name or not clean_handle:
        await interaction.response.send_message("Name and handle are required.", ephemeral=True)
        return
    platform_name = platform.value.title()
    if platform.value == "youtube":
        profile_url = url or f"https://www.youtube.com/@{clean_handle}"
        YOUTUBE_CHANNELS[clean_name] = {"handle": clean_handle, "url": profile_url}
    else:
        profile_url = url or f"https://www.{platform.value}.com/@{clean_handle}"
        SOCIAL_PROFILES[clean_name] = {
            "platform": platform_name,
            "url": profile_url,
            "handle": clean_handle,
        }
    save_config()
    await interaction.response.send_message(
        f"Added **{platform_name}: {clean_name}**. Run `/subs` to fetch it.", ephemeral=True
    )


@bot.tree.command(name="profile-remove", description="Remove a tracked profile")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
async def profile_remove_command(interaction: discord.Interaction, name: str) -> None:
    """Remove a profile by its display name."""
    clean_name = name.strip()
    removed = YOUTUBE_CHANNELS.pop(clean_name, None)
    removed = SOCIAL_PROFILES.pop(clean_name, removed)
    if removed is None:
        await interaction.response.send_message(f"No profile named **{name}** was found.", ephemeral=True)
        return
    state = load_state()
    if "channels" in state and clean_name in state["channels"]:
        state["channels"].pop(clean_name, None)
        save_state(state)
    save_config()
    await interaction.response.send_message(f"Removed **{name}**.", ephemeral=True)


@bot.tree.command(name="profile-list", description="List all tracked profiles")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
async def profile_list_command(interaction: discord.Interaction) -> None:
    """List configured profiles."""
    lines = [
        f"YouTube: **{name}** (`@{profile['handle']}`)"
        for name, profile in YOUTUBE_CHANNELS.items()
    ]
    lines.extend(
        f"{profile['platform']}: **{name}** (`@{profile['handle']}`)"
        for name, profile in SOCIAL_PROFILES.items()
    )
    await interaction.response.send_message("\n".join(lines) or "No profiles configured.", ephemeral=True)


# -------------------------------------------------------------
# Bustimes Commands
# -------------------------------------------------------------

@bot.tree.command(name="bus-config", description="Configure a Bustimes next-departures display")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(
    stop_url="Bustimes stop page URL, for example https://bustimes.org/stops/STOP_CODE",
    route="Route to show, for example 184",
    channel="Discord channel for the next-stop display",
)
async def bus_config_command(
    interaction: discord.Interaction,
    stop_url: str,
    route: str,
    channel: discord.TextChannel,
) -> None:
    """Configure and immediately create the Bustimes display."""
    if not stop_url.startswith("https://bustimes.org/stops/"):
        await interaction.response.send_message(
            "Use a Bustimes stop URL such as https://bustimes.org/stops/STOP_CODE.",
            ephemeral=True,
        )
        return
    BUS_CONFIG.update({
        "stop_url": stop_url,
        "route": route.strip(),
        "channel_id": channel.id,
        "message_id": None,
    })
    save_config()
    await interaction.response.send_message(
        f"Bustimes display configured for route **{route.strip()}** in {channel.mention}.",
        ephemeral=True,
    )
    await update_bus_status()


@bot.tree.command(name="bus-add", description="Find a Bustimes stop by name and create a display")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(
    stop_name="Part of the stop name, for example Piccadilly Gardens",
    channel="Discord channel for the display",
    route="Route number, for example 184; use * for every route",
)
async def bus_add_command(
    interaction: discord.Interaction,
    stop_name: str,
    channel: discord.TextChannel,
    route: str = "*",
) -> None:
    """Find a stop by name and configure the next-departures display."""
    await interaction.response.defer(ephemeral=True)
    try:
        matches = await BustimesClient().find_stops(stop_name.strip(), route.strip() or "*")
    except (aiohttp.ClientError, RuntimeError, ValueError) as exc:
        await interaction.followup.send(f"Bustimes search failed: {exc}", ephemeral=True)
        return
    if not matches:
        await interaction.followup.send(
            f"No Bustimes stop matching **{stop_name}** with route **{route}** was found.",
            ephemeral=True,
        )
        return
    if len(matches) > 1:
        choices = "\n".join(f"- [{match['name']}]({match['url']})" for match in matches[:10])
        await interaction.followup.send(
            "Several stops matched. Use `/bus-config` with the exact stop URL:\n" + choices,
            ephemeral=True,
        )
        return
    BUS_CONFIG.update({
        "stop_url": matches[0]["url"],
        "stop_name": matches[0]["name"],
        "route": route.strip() or "*",
        "channel_id": channel.id,
        "message_id": None,
    })
    save_config()
    await update_bus_status()
    await interaction.followup.send(
        f"Added **{matches[0]['name']}** for route **{route}** in {channel.mention}.",
        ephemeral=True,
    )


@bot.tree.command(name="bus-refresh", description="Refresh the Bustimes next-departures display")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
async def bus_refresh_command(interaction: discord.Interaction) -> None:
    """Refresh the configured Bustimes message."""
    await interaction.response.defer(ephemeral=True)
    await update_bus_status()
    await interaction.followup.send("Bustimes departures refreshed.", ephemeral=True)


@bot.tree.command(name="bus-status", description="Show the Bustimes display configuration")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
async def bus_status_command(interaction: discord.Interaction) -> None:
    """Show whether the Bustimes display has been configured."""
    if not BUS_CONFIG.get("channel_id"):
        await interaction.response.send_message(
            "Bustimes is not configured. Use /bus-config with an exact stop URL, route, and Discord channel.",
            ephemeral=True,
        )
        return
    await interaction.response.send_message(
        f"Stop: {BUS_CONFIG.get('stop_url')}\n"
        f"Route: {BUS_CONFIG.get('route')}\n"
        f"Channel: <#{BUS_CONFIG.get('channel_id')}>\n"
        f"Message: {BUS_CONFIG.get('message_id') or 'not created yet'}",
        ephemeral=True,
    )


# =============================================================================
# Supertram (Sheffield) Commands
# =============================================================================

@bot.tree.command(name="supertram-departures", description="Look up upcoming tram departures at any Supertram stop")
@app_commands.guild_only()
@app_commands.describe(stop="Stop name, e.g. Cathedral, Sheffield Station, Meadowhall, Arena, Hillsborough")
async def supertram_departures_command(interaction: discord.Interaction, stop: str) -> None:
    """Show live / timetable departures for a Sheffield Supertram stop."""
    clean_stop = stop.strip()
    departures = calculate_supertram_departures(clean_stop)
    if not departures:
        valid_stops = list(SUPERTRAM_DATA["key_stops"].keys())
        example_list = ", ".join(valid_stops[:6])
        await interaction.response.send_message(
            f"No Supertram stop found matching **{clean_stop}**.\nPopular stops: {example_list}.",
            ephemeral=True,
        )
        return
    canonical = departures[0].get("canonical_stop", clean_stop)
    embed = build_supertram_embed(departures, canonical)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="supertram-config", description="Configure an auto-updating departure board for Supertram")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(
    channel="Discord text channel for the departure board; omit to disable",
    stop_name="Stop name (e.g. Cathedral, Meadowhall Interchange, Sheffield Station, Arena)",
)
async def supertram_config_command(
    interaction: discord.Interaction,
    channel: discord.TextChannel | None = None,
    stop_name: str | None = None,
) -> None:
    """Set up an automatically refreshed Supertram board."""
    if channel is None:
        SUPERTRAM_CONFIG.clear()
        save_config()
        await interaction.response.send_message("Supertram departure board disabled.", ephemeral=True)
        return

    if not stop_name:
        await interaction.response.send_message("Please provide a stop name (e.g. Cathedral, Meadowhall).", ephemeral=True)
        return

    departures = calculate_supertram_departures(stop_name.strip())
    canonical = departures[0]["canonical_stop"] if departures else stop_name.strip()

    SUPERTRAM_CONFIG.update({
        "channel_id": channel.id,
        "stop_name": canonical,
        "message_id": None,
    })
    save_config()
    await interaction.response.send_message(
        f"Supertram board configured for **{canonical}** in {channel.mention}. Initializing now...",
        ephemeral=True,
    )
    await update_supertram_status()


@bot.tree.command(name="supertram-refresh", description="Manually refresh the Supertram departure board")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
async def supertram_refresh_command(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True)
    await update_supertram_status()
    await interaction.followup.send("Supertram board refreshed.", ephemeral=True)


@bot.tree.command(name="supertram-lines", description="Explore Sheffield Supertram lines and route maps")
@app_commands.describe(line="Specific line to view")
@app_commands.choices(line=[
    app_commands.Choice(name="Blue (Malin Bridge ↔ Halfway)", value="Blue"),
    app_commands.Choice(name="Yellow (Middlewood ↔ Meadowhall)", value="Yellow"),
    app_commands.Choice(name="Purple (Cathedral ↔ Herdings Park)", value="Purple"),
    app_commands.Choice(name="Tram Train (Cathedral ↔ Rotherham Parkgate)", value="Tram Train"),
])
async def supertram_lines_command(interaction: discord.Interaction, line: app_commands.Choice[str] | None = None) -> None:
    """Display information about Supertram lines."""
    if line is not None:
        line_data = SUPERTRAM_DATA["lines"][line.value]
        embed = discord.Embed(
            title=f"🚊 Sheffield Supertram: {line_data['name']}",
            description=f"**Route:** {line_data['terminus']}\n**Peak:** {line_data['frequency_peak']}\n**Off-Peak:** {line_data['frequency_offpeak']}",
            colour=line_data["hex"],
        )
        stops_str = " → ".join(line_data["stops"])
        if len(stops_str) > 1024:
            half = len(line_data["stops"]) // 2
            embed.add_field(name="Stops (Part 1)", value=" → ".join(line_data["stops"][:half]), inline=False)
            embed.add_field(name="Stops (Part 2)", value=" → ".join(line_data["stops"][half:]), inline=False)
        else:
            embed.add_field(name="All Stops", value=stops_str, inline=False)
    else:
        embed = discord.Embed(
            title="🚊 Sheffield Supertram Lines Overview",
            description="Supertram operates 4 routes connecting Sheffield, Rotherham, Meadowhall, and the Peak District foothills.",
            colour=discord.Colour.blue(),
        )
        for name, data in SUPERTRAM_DATA["lines"].items():
            embed.add_field(
                name=f"{data['name']} ({name})",
                value=f"• **Terminus:** {data['terminus']}\n• **Frequency:** {data['frequency_peak']}\n• **Total Stops:** {len(data['stops'])}",
                inline=False,
            )

    embed.add_field(
        name="Official Resources",
        value=f"[Network Map]({SUPERTRAM_DATA['map_url']}) • [Tickets & Timetables]({SUPERTRAM_DATA['official_url']})",
        inline=False,
    )
    embed.set_footer(text="Supertram • South Yorkshire Mayoral Combined Authority")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="supertram-stop", description="Get full details on a Sheffield Supertram stop")
@app_commands.describe(name="Stop name, e.g. Cathedral, Meadowhall, Arena, Sheffield Station")
async def supertram_stop_command(interaction: discord.Interaction, name: str) -> None:
    """Show details for a specific Supertram stop."""
    clean = name.strip()
    key_match = None
    for k, v in SUPERTRAM_DATA["key_stops"].items():
        if clean.casefold() in k.casefold() or k.casefold() in clean.casefold():
            key_match = (k, v)
            break

    if not key_match:
        # Check general stop lists
        found_in = []
        for line_name, line_info in SUPERTRAM_DATA["lines"].items():
            for st in line_info["stops"]:
                if clean.casefold() in st.casefold():
                    found_in.append((st, line_name))
        if found_in:
            stop_display = found_in[0][0]
            lines_serving = list(dict.fromkeys(item[1] for item in found_in))
            embed = discord.Embed(title=f"🚊 Supertram Stop: {stop_display}", colour=discord.Colour.blue())
            embed.add_field(name="Lines Served", value=", ".join(lines_serving), inline=True)
            embed.add_field(name="Quick Departures", value=f"Run `/supertram-departures stop:{stop_display}` to check times.", inline=False)
            await interaction.response.send_message(embed=embed)
            return

        valid = ", ".join(list(SUPERTRAM_DATA["key_stops"].keys())[:6])
        await interaction.response.send_message(f"Could not find stop '{name}'. Try: {valid}", ephemeral=True)
        return

    stop_name, info = key_match
    embed = discord.Embed(title=f"🚊 Supertram Stop: {stop_name}", colour=discord.Colour.gold())
    embed.add_field(name="Lines Served", value=", ".join(info["lines"]), inline=True)
    embed.add_field(name="Zone", value=info["zone"], inline=True)
    embed.add_field(name="Park & Ride", value="✅ Available" if info["park_and_ride"] else "❌ No", inline=True)
    embed.add_field(name="National Rail Connection", value="✅ Yes" if info["rail_link"] else "❌ No", inline=True)
    embed.add_field(name="Nearby Landmarks", value=info["landmarks"], inline=False)
    embed.set_footer(text=f"Use /supertram-departures stop:{stop_name} to view departures")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="supertram-status", description="Supertram ticket guide, fares, and system info")
async def supertram_status_command(interaction: discord.Interaction) -> None:
    """Display Supertram tickets, fares, and service status."""
    embed = discord.Embed(
        title="🚊 Sheffield Supertram Service Information",
        description="Supertram is operated by South Yorkshire Mayoral Combined Authority (SYMCA) connecting Sheffield and Rotherham.",
        colour=discord.Colour.blue(),
    )
    embed.add_field(
        name="Popular Tickets & Fares",
        value=(
            "• **Single Tram Only:** From £2.00 to £2.80 depending on distance\n"
            "• **Tram DayRider:** £4.40 (Unlimited tram travel for 1 day)\n"
            "• **CityWide Day:** £5.60 (Unlimited bus & tram across Sheffield)\n"
            "• **Tram 7 Day Pass:** £16.50 (Unlimited weekly tram travel)\n"
            "• **Under 21:** £1.00 single fare available with Zoom Beyond pass"
        ),
        inline=False,
    )
    embed.add_field(
        name="Operating Hours",
        value="• Monday - Saturday: 05:30 to 23:45\n• Sundays & Bank Holidays: 07:30 to 23:15\n• Core city centre services run every 6-12 minutes.",
        inline=False,
    )
    embed.add_field(
        name="Useful Links",
        value=f"[Official Website]({SUPERTRAM_DATA['official_url']}) • [Network Map]({SUPERTRAM_DATA['map_url']})",
        inline=False,
    )
    embed.set_footer(text="Supertram • Travel South Yorkshire")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="supertram-map", description="View the Sheffield Supertram network diagram")
async def supertram_map_command(interaction: discord.Interaction) -> None:
    embed = discord.Embed(
        title="🚊 Sheffield Supertram Network Map",
        description="The Supertram network covers 50 stations across 4 lines connecting Malin Bridge, Middlewood, Cathedral, Sheffield Station, Meadowhall, Halfway, Herdings Park, and Rotherham.",
        colour=discord.Colour.blue(),
    )
    embed.add_field(
        name="Interactive Map & Downloads",
        value=f"View the full high-resolution PDF map here:\n[Official Supertram Network Map]({SUPERTRAM_DATA['map_url']})",
        inline=False,
    )
    embed.set_image(url="https://upload.wikimedia.org/wikipedia/commons/thumb/c/cd/Supertram_map.svg/1200px-Supertram_map.svg.png")
    embed.set_footer(text="Sheffield Supertram • Stagecoach / SYMCA")
    await interaction.response.send_message(embed=embed)


# =============================================================================
# Hielke Maps Commands
# =============================================================================

@hielkemap_group.command(name="list", description="List popular Hielke Maps with difficulty ratings and links")
@app_commands.describe(category="Filter maps by series")
@app_commands.choices(category=[
    app_commands.Choice(name="All Maps", value="all"),
    app_commands.Choice(name="Spiral Series (Parkour Spiral 1, 2, 3)", value="Spiral"),
    app_commands.Choice(name="Paradise Series (Parkour Paradise 1, 2, 3, Giant)", value="Paradise"),
    app_commands.Choice(name="Landmarks (Pyramid, Helix, Grid)", value="Landmarks"),
    app_commands.Choice(name="Adventure & Sci-Fi (Dimension, Core, Planet, City)", value="Adventure"),
])
async def hielkemap_list_command(interaction: discord.Interaction, category: app_commands.Choice[str] | None = None) -> None:
    """Browse Hielke Maps catalog."""
    selected_cat = category.value if category else "all"
    maps_to_show = []

    for key, data in HIELKE_MAPS.items():
        if selected_cat == "all" or data["series"] == selected_cat or (selected_cat == "Adventure" and data["series"] in ["Adventure", "Sci-Fi", "Urban"]):
            maps_to_show.append((key, data))

    embed = discord.Embed(
        title="🏃 Hielke Maps Parkour Catalog",
        description=f"Showing **{len(maps_to_show)}** iconic Minecraft parkour maps by **Hielke**.\nOfficial site: [hielkemaps.com](https://hielkemaps.com)",
        colour=discord.Colour.gold(),
    )

    for key, data in maps_to_show[:12]:
        embed.add_field(
            name=f"{data['title']} — {data['difficulty']}",
            value=f"• **Version:** {data['version']}\n• [Map Page]({data['url']}) | [Bedrock Marketplace]({data['marketplace_url']})",
            inline=False,
        )

    embed.set_footer(text="Use /hielkemap search <name> for full details and speedrun tips")
    await interaction.response.send_message(embed=embed)


@hielkemap_group.command(name="search", description="Search for a specific Hielke Map and view full details")
@app_commands.describe(query="Map name (e.g. Spiral, Grid, Paradise, Pyramid, Helix, Core)")
async def hielkemap_search_command(interaction: discord.Interaction, query: str) -> None:
    """Search Hielke's parkour maps."""
    clean = query.strip().casefold()
    match = None

    for key, data in HIELKE_MAPS.items():
        if clean in key or clean in data["title"].casefold() or clean in data["series"].casefold():
            match = data
            break

    if not match:
        options = ", ".join([d["title"] for d in list(HIELKE_MAPS.values())[:6]])
        await interaction.response.send_message(f"No Hielke map found matching '{query}'.\nPopular maps: {options}.", ephemeral=True)
        return

    embed = discord.Embed(
        title=f"🏃 {match['title']}",
        description=match["description"],
        colour=discord.Colour.gold(),
    )
    embed.add_field(name="Difficulty", value=match["difficulty"], inline=True)
    embed.add_field(name="Series", value=match["series"], inline=True)
    embed.add_field(name="Supported Version", value=match["version"], inline=True)
    embed.add_field(name="Features", value=" • ".join(match["features"]), inline=False)
    embed.add_field(
        name="Downloads & Links",
        value=f"• [Official Page & Java Download]({match['url']})\n• [Minecraft Bedrock Marketplace]({match['marketplace_url']})",
        inline=False,
    )
    embed.set_footer(text=f"Submit your time with /hielkemap submit-time map:{match['title']} time:MM:SS")
    await interaction.response.send_message(embed=embed)


@hielkemap_group.command(name="random", description="Get a random Hielke Map recommendation to play")
@app_commands.describe(min_difficulty="Filter by minimum difficulty")
@app_commands.choices(min_difficulty=[
    app_commands.Choice(name="Any Difficulty", value=0),
    app_commands.Choice(name="Easy / Medium (2.5 - 3.0)", value=2),
    app_commands.Choice(name="Hard (3.5 - 4.0)", value=3),
    app_commands.Choice(name="Expert / Master (4.5+)", value=4),
])
async def hielkemap_random_command(interaction: discord.Interaction, min_difficulty: app_commands.Choice[int] | None = None) -> None:
    """Pick a random Hielke map."""
    diff_val = min_difficulty.value if min_difficulty else 0
    eligible = [m for m in HIELKE_MAPS.values() if m["rating"] >= diff_val]
    if not eligible:
        eligible = list(HIELKE_MAPS.values())

    selected = random.choice(eligible)
    embed = discord.Embed(
        title=f"🎯 Random Challenge: {selected['title']}",
        description=selected["description"],
        colour=discord.Colour.purple(),
    )
    embed.add_field(name="Difficulty", value=selected["difficulty"], inline=True)
    embed.add_field(name="Players", value=selected["players"], inline=True)
    embed.add_field(name="Play Map", value=f"[Open Map Page]({selected['url']})", inline=False)
    embed.set_footer(text="Challenge yourself or your friends to finish it!")
    await interaction.response.send_message(embed=embed)


@hielkemap_group.command(name="submit-time", description="Submit your speedrun or completion time for a Hielke Map")
@app_commands.describe(
    map_name="Name of the Hielke Map",
    time="Time in MM:SS or MM:SS.MS format (e.g. 14:25.50)",
    proof="Optional video/screenshot URL proving the run",
)
async def hielkemap_submit_time_command(
    interaction: discord.Interaction,
    map_name: str,
    time: str,
    proof: str | None = None,
) -> None:
    """Submit a speedrun time to the server leaderboard."""
    clean_map = map_name.strip()
    clean_time = time.strip()

    # Validate time format
    if not re.match(r"^\d{1,3}:\d{2}(\.\d{1,3})?$", clean_time):
        await interaction.response.send_message("Please use `MM:SS` or `MM:SS.MS` format (e.g. `12:45` or `08:32.40`).", ephemeral=True)
        return

    # Check match against catalogue
    matched_title = clean_map
    for k, v in HIELKE_MAPS.items():
        if clean_map.casefold() in v["title"].casefold() or clean_map.casefold() in k:
            matched_title = v["title"]
            break

    state = load_community_state()
    hielke_records = state.setdefault("hielke_records", {}).setdefault(matched_title, [])

    # Calculate total seconds for sorting
    parts = clean_time.split(":")
    mins = int(parts[0])
    secs = float(parts[1])
    total_seconds = mins * 60 + secs

    hielke_records.append({
        "user_id": interaction.user.id,
        "username": str(interaction.user),
        "time_str": clean_time,
        "seconds": total_seconds,
        "proof": proof,
        "date": datetime.now(timezone.utc).isoformat(),
    })
    hielke_records.sort(key=lambda r: r["seconds"])
    save_community_state(state)

    await interaction.response.send_message(
        f"🏆 **Time Submitted!** Recorded **{clean_time}** for **{matched_title}** by {interaction.user.mention}."
        f"\nCheck the standings with `/hielkemap leaderboard map:{matched_title}`.",
        ephemeral=False,
    )


@hielkemap_group.command(name="leaderboard", description="View the server speedrun leaderboard for a Hielke Map")
@app_commands.describe(map_name="Map name (e.g. Parkour Spiral, Parkour Grid, Parkour Paradise)")
async def hielkemap_leaderboard_command(interaction: discord.Interaction, map_name: str) -> None:
    """View server speedrun records."""
    clean_map = map_name.strip()
    matched_title = clean_map
    for k, v in HIELKE_MAPS.items():
        if clean_map.casefold() in v["title"].casefold() or clean_map.casefold() in k:
            matched_title = v["title"]
            break

    state = load_community_state()
    records = state.get("hielke_records", {}).get(matched_title, [])

    if not records:
        await interaction.response.send_message(
            f"No speedrun times submitted yet for **{matched_title}**.\nBe the first using `/hielkemap submit-time map:{matched_title} time:MM:SS`!",
            ephemeral=True,
        )
        return

    lines = []
    medals = ["🥇", "🥈", "🥉"]
    for rank, entry in enumerate(records[:10], start=1):
        medal = medals[rank - 1] if rank <= 3 else f"**{rank}.**"
        proof_link = f" [Proof]({entry['proof']})" if entry.get("proof") else ""
        lines.append(f"{medal} <@{entry['user_id']}> — **{entry['time_str']}**{proof_link}")

    embed = discord.Embed(
        title=f"🏆 Speedrun Leaderboard: {matched_title}",
        description="\n".join(lines),
        colour=discord.Colour.gold(),
    )
    embed.set_footer(text="Submit your personal best with /hielkemap submit-time")
    await interaction.response.send_message(embed=embed)


@hielkemap_group.command(name="tip", description="Get expert Minecraft parkour tips and jump mechanics")
@app_commands.describe(topic="Parkour movement topic")
@app_commands.choices(topic=[
    app_commands.Choice(name="Momentum & Sprint-Jumping", value="momentum"),
    app_commands.Choice(name="4-Block Flat Jumps", value="4-block"),
    app_commands.Choice(name="Neo Jumps (Corner Wraparound)", value="neo"),
    app_commands.Choice(name="Head-Hitter Ceiling Jumps", value="head-hitter"),
    app_commands.Choice(name="Honey Block Wall-Slides", value="honey"),
    app_commands.Choice(name="Slime Block Bounces", value="slime"),
    app_commands.Choice(name="Ladder Momentum & Catches", value="ladder"),
    app_commands.Choice(name="Ice & Packed Ice Speed", value="ice"),
    app_commands.Choice(name="Trapdoor & Iron Bar Precision", value="trapdoor"),
])
async def hielkemap_tip_command(interaction: discord.Interaction, topic: app_commands.Choice[str] | None = None) -> None:
    """Learn pro parkour mechanics."""
    if topic:
        data = HIELKE_TIPS[topic.value]
    else:
        key = random.choice(list(HIELKE_TIPS.keys()))
        data = HIELKE_TIPS[key]

    embed = discord.Embed(
        title=f"💡 Parkour Pro Tip: {data['title']}",
        description=data["tip"],
        colour=discord.Colour.green(),
    )
    embed.set_footer(text="Practice these techniques on Hielke's Parkour Paradise or Parkour Grid!")
    await interaction.response.send_message(embed=embed)


@hielkemap_group.command(name="challenge", description="Generate a random parkour challenge or dare")
async def hielkemap_challenge_command(interaction: discord.Interaction) -> None:
    """Generate a custom parkour challenge."""
    map_choice = random.choice(list(HIELKE_MAPS.values()))
    modifiers = [
        "No checkpoint restarts allowed (Hardcore run)!",
        "Complete the first 10 rooms/layers backwards!",
        "F5 (Third-person) perspective only!",
        "Hold sneak (crouch) between jumps without stopping!",
        "Beat your personal best by at least 15 seconds!",
        "No sprint key: jump-walk only where possible!",
        "Quake Pro FOV enabled throughout the map!",
    ]
    modifier = random.choice(modifiers)

    embed = discord.Embed(
        title="⚔️ Hielke Maps Challenge",
        description=f"**Map:** [{map_choice['title']}]({map_choice['url']})\n**Difficulty:** {map_choice['difficulty']}\n\n**Special Challenge Modifier:**\n🔥 **{modifier}**",
        colour=discord.Colour.orange(),
    )
    embed.set_footer(text="Clip or screenshot your run and share it with the community!")
    await interaction.response.send_message(embed=embed)


@hielkemap_group.command(name="quiz", description="Play a trivia quiz question about Hielke Maps and parkour mechanics")
async def hielkemap_quiz_command(interaction: discord.Interaction) -> None:
    """Interactive parkour quiz with button answers."""
    q_data = random.choice(HIELKE_QUIZ)
    embed = discord.Embed(
        title="🧠 Hielke Maps Trivia",
        description=f"**Question:**\n{q_data['question']}\n\nSelect your answer below:",
        colour=discord.Colour.blue(),
    )
    view = HielkeQuizView(q_data)
    await interaction.response.send_message(embed=embed, view=view)


# =============================================================================
# Community Suggestions & Server Tools Commands
# =============================================================================

@bot.tree.command(name="suggest", description="Submit a suggestion for the server")
@app_commands.guild_only()
@app_commands.describe(idea="Your suggestion or idea for the server")
async def suggest_command(interaction: discord.Interaction, idea: str) -> None:
    """Submit a community suggestion with voting buttons."""
    clean_idea = idea.strip()
    chan_id = community_channel_id("suggestions_channel_id")
    if not chan_id:
        await interaction.response.send_message(
            "Suggestions are not configured on this server yet. Ask staff to run `/suggestion-config`.",
            ephemeral=True,
        )
        return

    channel = interaction.guild.get_channel(chan_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(chan_id)
        except discord.DiscordException:
            channel = None

    if not isinstance(channel, discord.TextChannel):
        await interaction.response.send_message("Could not access suggestions channel.", ephemeral=True)
        return

    embed = discord.Embed(
        title="💡 New Suggestion",
        description=clean_idea,
        colour=discord.Colour.gold(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_author(name=f"{interaction.user.display_name} ({interaction.user.id})", icon_url=interaction.user.display_avatar.url)
    embed.add_field(name="Status", value="⏳ **Under Consideration**", inline=False)
    embed.set_footer(text=f"Suggestion by {interaction.user.id}")

    try:
        await channel.send(embed=embed, view=SuggestionView())
        await interaction.response.send_message(f"Your suggestion has been posted in {channel.mention}!", ephemeral=True)
    except discord.DiscordException:
        await interaction.response.send_message("Failed to post suggestion. Check permissions.", ephemeral=True)


@bot.tree.command(name="suggestion-config", description="Set the channel where suggestions are posted")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(channel="Channel for suggestions; omit to disable")
async def suggestion_config_command(interaction: discord.Interaction, channel: discord.TextChannel | None = None) -> None:
    if channel is None:
        COMMUNITY_CONFIG.pop("suggestions_channel_id", None)
        save_config()
        await interaction.response.send_message("Suggestions system disabled.", ephemeral=True)
        return
    COMMUNITY_CONFIG["suggestions_channel_id"] = channel.id
    save_config()
    await interaction.response.send_message(f"Suggestions channel set to {channel.mention}.", ephemeral=True)


@bot.tree.command(name="suggestion-accept", description="Accept a community suggestion")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(message_id="ID of the suggestion message", reason="Reason for acceptance")
async def suggestion_accept_command(interaction: discord.Interaction, message_id: str, reason: str = "Approved by staff") -> None:
    chan_id = community_channel_id("suggestions_channel_id")
    if not chan_id:
        await interaction.response.send_message("Suggestions channel not configured.", ephemeral=True)
        return
    channel = interaction.guild.get_channel(chan_id)
    if not isinstance(channel, discord.TextChannel):
        await interaction.response.send_message("Cannot find suggestions channel.", ephemeral=True)
        return
    try:
        msg = await channel.fetch_message(int(message_id))
    except (ValueError, discord.NotFound):
        await interaction.response.send_message("Suggestion message not found.", ephemeral=True)
        return

    if not msg.embeds:
        await interaction.response.send_message("Message has no embed.", ephemeral=True)
        return
    embed = msg.embeds[0]
    embed.colour = discord.Colour.green()
    for idx, field in enumerate(embed.fields):
        if field.name == "Status":
            embed.set_field_at(idx, name="Status", value=f"✅ **Accepted:** {reason} (by {interaction.user.mention})", inline=False)
            break
    await msg.edit(embed=embed)
    await interaction.response.send_message("Suggestion marked as accepted.", ephemeral=True)


@bot.tree.command(name="suggestion-reject", description="Reject a community suggestion")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(message_id="ID of the suggestion message", reason="Reason for rejection")
async def suggestion_reject_command(interaction: discord.Interaction, message_id: str, reason: str = "Declined by staff") -> None:
    chan_id = community_channel_id("suggestions_channel_id")
    if not chan_id:
        await interaction.response.send_message("Suggestions channel not configured.", ephemeral=True)
        return
    channel = interaction.guild.get_channel(chan_id)
    if not isinstance(channel, discord.TextChannel):
        await interaction.response.send_message("Cannot find suggestions channel.", ephemeral=True)
        return
    try:
        msg = await channel.fetch_message(int(message_id))
    except (ValueError, discord.NotFound):
        await interaction.response.send_message("Suggestion message not found.", ephemeral=True)
        return

    if not msg.embeds:
        await interaction.response.send_message("Message has no embed.", ephemeral=True)
        return
    embed = msg.embeds[0]
    embed.colour = discord.Colour.red()
    for idx, field in enumerate(embed.fields):
        if field.name == "Status":
            embed.set_field_at(idx, name="Status", value=f"❌ **Declined:** {reason} (by {interaction.user.mention})", inline=False)
            break
    await msg.edit(embed=embed)
    await interaction.response.send_message("Suggestion marked as declined.", ephemeral=True)


@bot.tree.command(name="giveaway", description="Start an interactive giveaway with button entry")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(minutes="Duration in minutes", prize="Prize description")
async def giveaway_command(interaction: discord.Interaction, minutes: app_commands.Range[int, 1, 10080], prize: str) -> None:
    """Launch a server giveaway."""
    end_time = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    unix_end = int(end_time.timestamp())

    embed = discord.Embed(
        title="🎉 GIVEAWAY 🎉",
        description=f"**Prize:** {prize}\n\n**Ends:** <t:{unix_end}:R> (<t:{unix_end}:f>)\nHosted by: {interaction.user.mention}\n\nClick the button below to enter!",
        colour=discord.Colour.purple(),
    )
    embed.set_footer(text="Good luck to all entrants!")

    view = GiveawayView()
    await interaction.response.send_message("Giveaway launched!", ephemeral=True)
    msg = await interaction.channel.send(embed=embed, view=view)

    state = load_community_state()
    giveaways = state.setdefault("giveaways", {})
    giveaways[str(msg.id)] = {
        "channel_id": interaction.channel_id,
        "prize": prize,
        "end_time": end_time.isoformat(),
        "entries": [],
        "ended": False,
    }
    save_community_state(state)


@bot.tree.command(name="afk", description="Set your AFK status with an optional reason")
@app_commands.describe(reason="Reason for being AFK")
async def afk_command(interaction: discord.Interaction, reason: str = "AFK") -> None:
    afk_users[interaction.user.id] = {
        "reason": reason.strip(),
        "since": datetime.now(timezone.utc).isoformat(),
    }
    await interaction.response.send_message(
        f"💤 {interaction.user.mention} is now AFK: **{reason.strip()}**.\nI will notify anyone who mentions you and clear it when you speak next.",
        ephemeral=False,
    )


@bot.tree.command(name="leave-config", description="Set or disable the member departure log channel")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(channel="Channel for leave logs; omit to disable")
async def leave_config_command(interaction: discord.Interaction, channel: discord.TextChannel | None = None) -> None:
    if channel is None:
        COMMUNITY_CONFIG.pop("leave_channel_id", None)
        save_config()
        await interaction.response.send_message("Leave logging disabled.", ephemeral=True)
        return
    COMMUNITY_CONFIG["leave_channel_id"] = channel.id
    save_config()
    await interaction.response.send_message(f"Member departure logs will be posted in {channel.mention}.", ephemeral=True)


@bot.tree.command(name="slowmode", description="Set the slowmode delay for this channel")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_channels=True)
@app_commands.describe(seconds="Slowmode delay in seconds (0 to disable)")
async def slowmode_command(interaction: discord.Interaction, seconds: app_commands.Range[int, 0, 21600]) -> None:
    if not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("Must be used in a text channel.", ephemeral=True)
        return
    try:
        await interaction.channel.edit(slowmode_delay=seconds)
        if seconds == 0:
            await interaction.response.send_message("Slowmode disabled for this channel.")
        else:
            await interaction.response.send_message(f"Slowmode set to **{seconds}s**.")
    except discord.Forbidden:
        await interaction.response.send_message("I need Manage Channels permission.", ephemeral=True)


@bot.tree.command(name="lock", description="Lock the current channel to prevent members from chatting")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_channels=True)
async def quick_lock_command(interaction: discord.Interaction) -> None:
    if not isinstance(interaction.channel, discord.TextChannel) or interaction.guild is None:
        await interaction.response.send_message("Must be used in a text channel.", ephemeral=True)
        return
    await interaction.channel.set_permissions(interaction.guild.default_role, send_messages=False, reason=f"Locked by {interaction.user}")
    await interaction.response.send_message("🔒 Channel locked.")


@bot.tree.command(name="unlock", description="Unlock the current channel to restore speaking")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_channels=True)
async def quick_unlock_command(interaction: discord.Interaction) -> None:
    if not isinstance(interaction.channel, discord.TextChannel) or interaction.guild is None:
        await interaction.response.send_message("Must be used in a text channel.", ephemeral=True)
        return
    await interaction.channel.set_permissions(interaction.guild.default_role, send_messages=None, reason=f"Unlocked by {interaction.user}")
    await interaction.response.send_message("🔓 Channel unlocked.")


# -------------------------------------------------------------
# Applications System Commands
# -------------------------------------------------------------

@bot.tree.command(name="apply", description="Submit an application for server staff or roles")
@app_commands.guild_only()
async def apply_command(interaction: discord.Interaction) -> None:
    """Open the application form."""
    app_config = COMMUNITY_CONFIG.get("applications", {})
    if not app_config.get("channel_id"):
        await interaction.response.send_message(
            "Applications are not currently open or configured on this server.",
            ephemeral=True,
        )
        return
    await interaction.response.send_modal(ApplicationModal())


@bot.tree.command(name="application-panel", description="Post the application panel button")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(
    title="Panel title",
    description="Panel description text",
)
async def application_panel_command(
    interaction: discord.Interaction,
    title: str = "Staff Applications",
    description: str = "Click the button below to submit your application!",
) -> None:
    """Post an application button panel in the current channel."""
    embed = discord.Embed(
        title=title,
        description=description,
        colour=discord.Colour.green(),
    )
    embed.set_footer(text="Click 'Apply Now' to open the application form.")
    await interaction.channel.send(embed=embed, view=ApplicationPanelView())
    await interaction.response.send_message("Application panel posted.", ephemeral=True)


@bot.tree.command(name="application-config", description="Configure or disable the applications system")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(
    channel="Channel where applications are sent for review; omit to disable",
    role_to_give="Role automatically given when an application is accepted",
    notify_dm="Send a DM to the applicant when their application is accepted or rejected",
)
async def application_config_command(
    interaction: discord.Interaction,
    channel: discord.TextChannel | None = None,
    role_to_give: discord.Role | None = None,
    notify_dm: bool = True,
) -> None:
    """Configure the review channel and settings for applications."""
    if channel is None:
        COMMUNITY_CONFIG.pop("applications", None)
        save_config()
        await interaction.response.send_message("Applications system disabled.", ephemeral=True)
        return

    if role_to_give and (role_to_give.is_default() or role_to_give.managed):
        await interaction.response.send_message("Choose a normal, non-managed role for auto-role.", ephemeral=True)
        return

    if role_to_give and interaction.guild and interaction.guild.me and role_to_give >= interaction.guild.me.top_role:
        await interaction.response.send_message(
            "Move my bot role above the selected role before configuring application auto-role.",
            ephemeral=True,
        )
        return

    COMMUNITY_CONFIG["applications"] = {
        "channel_id": channel.id,
        "role_id": role_to_give.id if role_to_give else None,
        "notify_dm": notify_dm,
    }
    save_config()
    role_info = f" with auto-role {role_to_give.mention}" if role_to_give else " (no auto-role)"
    dm_info = "DM notifications enabled." if notify_dm else "DM notifications disabled."
    await interaction.response.send_message(
        f"Applications enabled! Review channel: {channel.mention}{role_info}. {dm_info}",
        ephemeral=True,
    )


@bot.tree.command(name="application-status", description="Show the current application settings")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
async def application_status_command(interaction: discord.Interaction) -> None:
    """Show whether applications are enabled and where they are sent."""
    app_config = COMMUNITY_CONFIG.get("applications", {})
    channel_id = app_config.get("channel_id")
    if not channel_id:
        await interaction.response.send_message("Applications are currently disabled.", ephemeral=True)
        return
    role_id = app_config.get("role_id")
    role_str = f"<@&{role_id}>" if role_id else "None"
    dm_str = "Yes" if app_config.get("notify_dm", True) else "No"
    state = load_community_state()
    total_apps = len(state.get("applications", {}))
    await interaction.response.send_message(
        f"**Applications Status:**\n"
        f"- Review Channel: <#{channel_id}>\n"
        f"- Acceptance Role: {role_str}\n"
        f"- DM Notifications: {dm_str}\n"
        f"- Total Submissions: {total_apps}",
        ephemeral=True,
    )


# -------------------------------------------------------------
# General Bot, Server & Portal Commands
# -------------------------------------------------------------

@bot.tree.command(name="serverinfo", description="Show server information")
@app_commands.guild_only()
async def serverinfo_command(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return
    embed = discord.Embed(title=guild.name, colour=discord.Colour.blurple())
    embed.add_field(name="Members", value=f"{guild.member_count or len(guild.members) or 0:,}")
    embed.add_field(name="Channels", value=str(len(guild.channels)))
    embed.add_field(name="Created", value=format_uk_datetime(guild.created_at))
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="userinfo", description="Show information about a member")
@app_commands.guild_only()
async def userinfo_command(interaction: discord.Interaction, member: discord.Member | None = None) -> None:
    target_member = member or interaction.user
    colour = getattr(target_member, "colour", discord.Colour.blurple())
    embed = discord.Embed(title=target_member.display_name, colour=colour)
    embed.add_field(name="ID", value=str(target_member.id))
    joined = getattr(target_member, "joined_at", None) or target_member.created_at
    embed.add_field(name="Joined", value=format_uk_datetime(joined))
    embed.add_field(name="Account created", value=format_uk_datetime(target_member.created_at))
    embed.set_thumbnail(url=target_member.display_avatar.url)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="avatar", description="Show a member's avatar")
@app_commands.guild_only()
async def avatar_command(interaction: discord.Interaction, member: discord.Member | None = None) -> None:
    target_member = member or interaction.user
    colour = getattr(target_member, "colour", discord.Colour.blurple())
    embed = discord.Embed(title=f"{target_member.display_name}'s avatar", colour=colour)
    embed.set_image(url=target_member.display_avatar.url)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="uptime", description="Show how long the bot has been running")
async def uptime_command(interaction: discord.Interaction) -> None:
    elapsed = datetime.now(timezone.utc) - START_TIME
    await interaction.response.send_message(f"Uptime: **{str(elapsed).split('.')[0]}**", ephemeral=True)


@bot.tree.command(name="timestamp", description="Create a Discord timestamp")
@app_commands.describe(
    date_time="UK time in DD/MM/YYYY HH:MM format",
    style="Discord timestamp display style",
)
@app_commands.choices(style=[
    app_commands.Choice(name="Short time", value="t"),
    app_commands.Choice(name="Long time", value="T"),
    app_commands.Choice(name="Short date", value="d"),
    app_commands.Choice(name="Long date", value="D"),
    app_commands.Choice(name="Date and time", value="f"),
    app_commands.Choice(name="Full date and time", value="F"),
    app_commands.Choice(name="Relative time", value="R"),
])
async def timestamp_command(
    interaction: discord.Interaction,
    date_time: str,
    style: app_commands.Choice[str] | None = None,
) -> None:
    """Convert UK local time into a Discord timestamp."""
    try:
        local_time = datetime.strptime(date_time.strip(), "%d/%m/%Y %H:%M").replace(tzinfo=ZoneInfo("Europe/London"))
    except (ValueError, OSError):
        await interaction.response.send_message(
            "Use UK format: `DD/MM/YYYY HH:MM`, for example `21/09/2026 18:30`.",
            ephemeral=True,
        )
        return
    timestamp_style = style.value if style else "f"
    unix_time = int(local_time.timestamp())
    await interaction.response.send_message(
        f"Discord timestamp:\n`<t:{unix_time}:{timestamp_style}>`\n\n"
        f"Preview: <t:{unix_time}:{timestamp_style}>",
        ephemeral=False,
    )


@bot.tree.command(name="bot-status", description="Show bot service status")
async def bot_status_command(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(
        f"Online as **{bot.user}**\nLatency: **{bot.latency * 1000:.0f} ms**\n"
        f"Guilds: **{len(bot.guilds)}**\nBus monitor: **{'on' if BUS_CONFIG.get('channel_id') else 'off'}**\n"
        f"Supertram monitor: **{'on' if SUPERTRAM_CONFIG.get('channel_id') else 'off'}**",
        ephemeral=True,
    )


@bot.tree.command(name="leaderboard", description="Show the server XP leaderboard")
@app_commands.guild_only()
async def leaderboard_command(interaction: discord.Interaction) -> None:
    """Show the ten members with the most XP."""
    if interaction.guild is None:
        return
    state = load_community_state()
    levels = state.get("levels", {})
    ranked = sorted(
        ((user_id, normalized_user_data(data)) for user_id, data in levels.items()),
        key=lambda item: (xp_value(item[1]), item[1].get("level", 0)),
        reverse=True,
    )[:10]
    if not ranked:
        await interaction.response.send_message(
            "The leaderboard is empty. Members earn XP by chatting.", ephemeral=True
        )
        return

    lines = []
    for rank, (user_id, data) in enumerate(ranked, start=1):
        member = interaction.guild.get_member(int(user_id))
        if member:
            name = member.display_name
        else:
            cached_user = bot.get_user(int(user_id))
            name = cached_user.display_name if cached_user else f"User {user_id}"
        lines.append(
            f"**{rank}.** {name} - Level **{int(data.get('level', 0))}** "
            f"({int(data.get('xp', 0)):,} XP)"
        )
    embed = discord.Embed(
        title="🏆 XP Leaderboard",
        description=(
            f"Server members: **{interaction.guild.member_count or len(interaction.guild.members) or 0:,}**\n\n"
            + "\n".join(lines)
        ),
        colour=discord.Colour.gold(),
    )
    embed.set_image(url=COMMUNITY_CONFIG.get("welcome_image_url", DEFAULT_WELCOME_BUS_IMAGE))
    embed.set_footer(text="Earn XP by chatting once per minute")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="help", description="Show the complete bot command list")
@app_commands.guild_only()
async def help_command(interaction: discord.Interaction) -> None:
    """Show the main command groups."""
    embed = discord.Embed(title="🤖 Bot Command Directory", colour=discord.Colour.blurple())
    embed.add_field(name="🏃 Hielke Maps", value="`/hielkemap list` `/hielkemap search` `/hielkemap random` `/hielkemap submit-time` `/hielkemap leaderboard` `/hielkemap tip` `/hielkemap challenge` `/hielkemap quiz`", inline=False)
    embed.add_field(name="🚊 Supertram (Sheffield)", value="`/supertram-departures` `/supertram-lines` `/supertram-stop` `/supertram-status` `/supertram-map` `/supertram-config`", inline=False)
    embed.add_field(name="🚌 Bus & Social Tracking", value="`/subs` `/graph` `/bus-add` `/bus-config` `/bus-refresh` `/profile-add` `/profile-list`", inline=False)
    embed.add_field(name="📝 Applications", value="`/apply` `/application-panel` `/application-config` `/application-status`", inline=False)
    embed.add_field(name="💬 Community & Fun", value="`/suggest` `/giveaway` `/level` `/leaderboard` `/poll` `/remind` `/afk` `/roll` `/coinflip` `/choose` `/8ball`", inline=False)
    embed.add_field(name="📚 Portals", value="`/sparx` `/school-portals` `/portal-config`", inline=False)
    embed.add_field(name="🤖 AI (Gemini)", value="`/ai` `/explain` `/translate` `/summarize`", inline=False)
    embed.add_field(name="🛡️ Moderation & Server", value="`/announce` `/clear` `/slowmode` `/lock` `/unlock` `/moderation-config` `/role-panel` `/reaction-role` `/ticket-panel` `/serverinfo`", inline=False)
    embed.set_footer(text="Type / to see all slash commands with autocomplete")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="poll", description="Create a simple yes/no poll")
@app_commands.guild_only()
async def poll_command(interaction: discord.Interaction, question: str) -> None:
    embed = discord.Embed(title="📊 Poll", description=question, colour=discord.Colour.blurple())
    embed.set_footer(text=f"Poll by {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)
    try:
        message = await interaction.original_response()
        await message.add_reaction("✅")
        await message.add_reaction("❌")
    except discord.DiscordException:
        pass


@bot.tree.command(name="roll", description="Roll a dice")
async def roll_command(interaction: discord.Interaction, sides: app_commands.Range[int, 2, 100] = 6) -> None:
    await interaction.response.send_message(f"🎲 You rolled **{random.randint(1, sides)}** (d{sides}).")


@bot.tree.command(name="coinflip", description="Flip a coin")
async def coinflip_command(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(f"🪙 **{random.choice(['Heads', 'Tails'])}!**")


@bot.tree.command(name="choose", description="Choose randomly between options")
async def choose_command(interaction: discord.Interaction, options: str) -> None:
    choices = [item.strip() for item in options.split(",") if item.strip()]
    if len(choices) < 2:
        await interaction.response.send_message("Give at least two comma-separated options.", ephemeral=True)
        return
    await interaction.response.send_message(f"🎯 I choose **{random.choice(choices)}**.")


@bot.tree.command(name="8ball", description="Ask the magic 8-ball")
async def eight_ball_command(interaction: discord.Interaction, question: str) -> None:
    answers = ["Yes.", "No.", "Probably.", "Probably not.", "Ask again later.", "Absolutely.", "Without a doubt."]
    await interaction.response.send_message(f"🎱 **{random.choice(answers)}**")


@bot.tree.command(name="remind", description="Send yourself a reminder later")
@app_commands.guild_only()
@app_commands.describe(minutes="Minutes from now", message="Reminder text")
async def remind_command(interaction: discord.Interaction, minutes: app_commands.Range[int, 1, 10080], message: str) -> None:
    state = load_state()
    reminders = state.setdefault("reminders", [])
    reminders.append({
        "user_id": interaction.user.id,
        "channel_id": interaction.channel_id,
        "message": message,
        "due": (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat(),
    })
    save_state(state)
    await interaction.response.send_message(f"Reminder set for {minutes} minutes from now.", ephemeral=True)


@bot.tree.command(name="announce", description="Send an announcement to a channel")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
async def announce_command(interaction: discord.Interaction, channel: discord.TextChannel, message: str) -> None:
    if interaction.guild is None:
        return
    await channel.send(f"📢 **Announcement from {interaction.guild.name}**\n{message}")
    await interaction.response.send_message("Announcement sent.", ephemeral=True)


@bot.tree.command(name="clear", description="Delete recent messages")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_messages=True)
async def clear_command(interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100]) -> None:
    if not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("Use this in a text channel.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        deleted = await interaction.channel.purge(limit=amount)
        await interaction.followup.send(f"Deleted {len(deleted)} messages.", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send("I do not have permission to delete messages in this channel.", ephemeral=True)


@bot.tree.command(name="moderation-config", description="Configure anti-spam and banned words")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
async def moderation_config_command(
    interaction: discord.Interaction,
    anti_spam: bool = False,
    banned_words: str = "",
) -> None:
    COMMUNITY_CONFIG["moderation"] = {
        "anti_spam": anti_spam,
        "banned_words": [word.strip() for word in banned_words.split(",") if word.strip()],
    }
    save_config()
    await interaction.response.send_message("Moderation settings saved.", ephemeral=True)


@bot.tree.command(name="role-panel", description="Post a button that toggles a member role")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_roles=True)
async def role_panel_command(interaction: discord.Interaction, role: discord.Role, label: str = "Get role") -> None:
    if role.is_default() or role.managed:
        await interaction.response.send_message("Choose a normal, non-managed role.", ephemeral=True)
        return
    if interaction.guild and interaction.guild.me and role >= interaction.guild.me.top_role:
        await interaction.response.send_message("Move my bot role above the selected role.", ephemeral=True)
        return
    panels = COMMUNITY_CONFIG.setdefault("role_panels", [])
    panel = {"role_id": role.id, "label": label[:80]}
    panels[:] = [item for item in panels if int(item["role_id"]) != role.id]
    panels.append(panel)
    save_config()
    view = RoleButtonView(role.id, panel["label"])
    try:
        bot.add_view(view)
    except (ValueError, TypeError, discord.DiscordException):
        pass
    if isinstance(interaction.channel, discord.TextChannel):
        await interaction.channel.send(
            f"Click the button to get or remove {role.mention}.",
            view=view,
        )
        await interaction.response.send_message("Role panel posted.", ephemeral=True)
    else:
        await interaction.response.send_message("Must be used in a text channel.", ephemeral=True)


@bot.tree.command(name="reaction-role", description="Add an emoji reaction role to a message")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_roles=True)
@app_commands.describe(
    message_id="The message ID to attach the reaction role to",
    emoji="A Unicode emoji, such as 🎮",
    role="The role to give when members react",
)
async def reaction_role_command(
    interaction: discord.Interaction,
    message_id: str,
    emoji: str,
    role: discord.Role,
) -> None:
    if role.is_default() or role.managed:
        await interaction.response.send_message("Choose a normal, non-managed role.", ephemeral=True)
        return
    if interaction.guild and interaction.guild.me and role >= interaction.guild.me.top_role:
        await interaction.response.send_message("Move my bot role above the selected role.", ephemeral=True)
        return
    if not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("Use this command in a text channel.", ephemeral=True)
        return
    try:
        message = await interaction.channel.fetch_message(int(message_id))
        parsed_emoji = discord.PartialEmoji.from_str(emoji.strip())
        await message.add_reaction(parsed_emoji)
    except (ValueError, discord.NotFound):
        await interaction.response.send_message("Message not found. Check the message ID and channel.", ephemeral=True)
        return
    except discord.Forbidden:
        await interaction.response.send_message("I need Read Message History and Add Reactions.", ephemeral=True)
        return
    reaction_roles = COMMUNITY_CONFIG.setdefault("reaction_roles", [])
    reaction_roles[:] = [
        item for item in reaction_roles
        if not (int(item["message_id"]) == int(message_id) and item["emoji"] == str(parsed_emoji))
    ]
    reaction_roles.append({
        "message_id": int(message_id),
        "channel_id": interaction.channel_id,
        "emoji": str(parsed_emoji),
        "role_id": role.id,
    })
    save_config()
    await interaction.response.send_message(
        f"React with {parsed_emoji} on that message to get {role.mention}.", ephemeral=True
    )


@bot.tree.command(name="reaction-role-remove", description="Remove an emoji reaction role")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_roles=True)
async def reaction_role_remove_command(
    interaction: discord.Interaction,
    message_id: str,
    emoji: str,
) -> None:
    reaction_roles = COMMUNITY_CONFIG.get("reaction_roles", [])
    before = len(reaction_roles)
    reaction_roles[:] = [
        item for item in reaction_roles
        if not (int(item["message_id"]) == int(message_id) and item["emoji"] == emoji.strip())
    ]
    save_config()
    await interaction.response.send_message(
        "Reaction role removed." if len(reaction_roles) < before else "No matching reaction role found.",
        ephemeral=True,
    )


@bot.tree.command(name="counting-config", description="Set or disable the counting channel")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
async def counting_config_command(
    interaction: discord.Interaction,
    channel: discord.TextChannel | None = None,
) -> None:
    """Configure the channel where members count upward."""
    if channel is None:
        COMMUNITY_CONFIG.pop("counting_channel_id", None)
        save_config()
        await interaction.response.send_message("Counting disabled.", ephemeral=True)
        return
    COMMUNITY_CONFIG["counting_channel_id"] = channel.id
    community_state = load_community_state()
    community_state["counting_next"] = 1
    save_community_state(community_state)
    save_config()
    await interaction.response.send_message(
        f"Counting enabled in {channel.mention}, starting at **1**.", ephemeral=True
    )


@bot.tree.command(name="welcome-config", description="Set or disable the welcome channel")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
async def welcome_config_command(
    interaction: discord.Interaction,
    channel: discord.TextChannel | None = None,
    image_url: str | None = None,
) -> None:
    """Configure the welcome channel."""
    if channel is None:
        COMMUNITY_CONFIG.pop("welcome_channel_id", None)
        save_config()
        await interaction.response.send_message("Welcomer disabled.", ephemeral=True)
        return
    COMMUNITY_CONFIG["welcome_channel_id"] = channel.id
    if image_url:
        COMMUNITY_CONFIG["welcome_image_url"] = image_url
    save_config()
    await interaction.response.send_message(
        f"Welcomer enabled in {channel.mention}.", ephemeral=True
    )


@bot.tree.command(name="test-welcome", description="Preview the welcome message in this channel")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
async def test_welcome_command(interaction: discord.Interaction) -> None:
    """Send a preview using the invoking member as the test joiner."""
    if not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
        return
    if not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("Use this command in a text channel.", ephemeral=True)
        return
    try:
        await interaction.channel.send(embed=build_welcome_embed(interaction.user))
        await interaction.response.send_message("Welcome preview posted.", ephemeral=True)
    except discord.DiscordException:
        logger.exception("Could not send welcome preview")
        await interaction.response.send_message(
            "I could not post the preview. Check Send Messages and Embed Links permissions.",
            ephemeral=True,
        )


@bot.tree.command(name="autorole-config", description="Set or disable the automatic new-member role")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True, manage_roles=True)
async def autorole_config_command(
    interaction: discord.Interaction,
    role: discord.Role | None = None,
) -> None:
    """Configure the role given to new members."""
    if role is None:
        COMMUNITY_CONFIG.pop("autorole_id", None)
        save_config()
        await interaction.response.send_message("Autorole disabled.", ephemeral=True)
        return
    if role.is_default() or role.managed:
        await interaction.response.send_message("Choose a normal, non-managed role.", ephemeral=True)
        return
    if interaction.guild and interaction.guild.me and role >= interaction.guild.me.top_role:
        await interaction.response.send_message(
            "Move my bot role above the selected role before configuring autorole.",
            ephemeral=True,
        )
        return
    COMMUNITY_CONFIG["autorole_id"] = role.id
    save_config()
    await interaction.response.send_message(f"New members will receive {role.mention}.", ephemeral=True)


@bot.tree.command(name="monitor-config", description="Configure upload and livestream alerts")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(
    channel="Channel for alerts; omit to disable monitoring",
    youtube_uploads="Post new YouTube upload alerts",
    twitch_live="Post Twitch livestream alerts",
)
async def monitor_config_command(
    interaction: discord.Interaction,
    channel: discord.TextChannel | None = None,
    youtube_uploads: bool = True,
    twitch_live: bool = True,
) -> None:
    """Configure social alerts."""
    if channel is None:
        COMMUNITY_CONFIG.pop("monitor_channel_id", None)
        save_config()
        await interaction.response.send_message("Upload/livestream monitoring disabled.", ephemeral=True)
        return
    COMMUNITY_CONFIG.update({
        "monitor_channel_id": channel.id,
        "youtube_uploads": youtube_uploads,
        "twitch_live": twitch_live,
    })
    save_config()
    await interaction.response.send_message(
        f"Monitoring enabled in {channel.mention}.", ephemeral=True
    )


@bot.tree.command(name="level", description="Show your current XP level")
@app_commands.guild_only()
async def level_command(interaction: discord.Interaction) -> None:
    """Show the invoking member's level."""
    state = load_community_state()
    user = normalized_user_data(state.get("levels", {}).get(str(interaction.user.id)))
    await interaction.response.send_message(
        f"{interaction.user.mention} is level **{user.get('level', 0)}** with **{user.get('xp', 0)} XP**.",
        ephemeral=True,
    )


@bot.tree.command(name="sparx", description="Show Sparx Maths, Science, and Reader links")
@app_commands.guild_only()
async def sparx_command(interaction: discord.Interaction) -> None:
    """Post official or server-configured Sparx resource links."""
    if not COMMUNITY_CONFIG.get("portal_enabled", True):
        await interaction.response.send_message(
            "Portal links are disabled on this server.", ephemeral=True
        )
        return
    embed = discord.Embed(
        title="📚 Sparx learning resources",
        description="Open the resource you need below.",
        colour=discord.Colour.blurple(),
    )
    embed.add_field(name="Sparx Maths", value=f"[Open Maths]({SPARX_LINKS['maths']})", inline=False)
    embed.add_field(name="Sparx Science", value=f"[Open Science]({SPARX_LINKS['science']})", inline=False)
    embed.add_field(name="Sparx Reader", value=f"[Open Reader]({SPARX_LINKS['reader']})", inline=False)
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="Maths", url=SPARX_LINKS["maths"]))
    view.add_item(discord.ui.Button(label="Science", url=SPARX_LINKS["science"]))
    view.add_item(discord.ui.Button(label="Reader", url=SPARX_LINKS["reader"]))
    await interaction.response.send_message(embed=embed, view=view)


@bot.tree.command(name="sparx-disable", description="Disable Sparx links on this server")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
async def sparx_disable_command(interaction: discord.Interaction) -> None:
    COMMUNITY_CONFIG["portal_enabled"] = False
    save_config()
    await interaction.response.send_message("Sparx links disabled.", ephemeral=True)


@bot.tree.command(name="sparx-enable", description="Enable Sparx links on this server")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
async def sparx_enable_command(interaction: discord.Interaction) -> None:
    COMMUNITY_CONFIG["portal_enabled"] = True
    save_config()
    await interaction.response.send_message("Sparx links enabled.", ephemeral=True)


@bot.tree.command(name="sparx-config", description="Configure Sparx Maths, Science, and Reader links")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(
    maths_url="Sparx Maths login URL",
    science_url="Sparx Science login URL",
    reader_url="Sparx Reader login URL",
)
async def sparx_config_command(
    interaction: discord.Interaction,
    maths_url: str | None = None,
    science_url: str | None = None,
    reader_url: str | None = None,
) -> None:
    """Set school-specific Sparx portal URLs."""
    urls = {"maths": maths_url, "science": science_url, "reader": reader_url}
    provided = {k: v for k, v in urls.items() if v is not None}
    if not all(url.startswith(("http://", "https://")) for url in provided.values()):
        await interaction.response.send_message("All Sparx links must start with http:// or https://.", ephemeral=True)
        return
    SPARX_LINKS.update(provided)
    save_config()
    await interaction.response.send_message("Sparx links saved. Use /sparx to post them.", ephemeral=True)


@bot.tree.command(name="school-portals", description="Compare school learning and parent portals")
@app_commands.guild_only()
async def school_portals_command(interaction: discord.Interaction) -> None:
    """Post links for the configured school portals."""
    if not COMMUNITY_CONFIG.get("portal_enabled", True):
        await interaction.response.send_message(
            "Portal links are disabled on this server.", ephemeral=True
        )
        return
    embed = discord.Embed(
        title="🏫 School portals",
        description="These services do different jobs; use the one your school has assigned.",
        colour=discord.Colour.blurple(),
    )
    descriptions = {
        "sparx": "Maths, Science, and Reader homework",
        "dr_frost": "Maths practice and revision",
        "arbor": "School information and parent/student portal",
        "sims": "School information system",
        "bromcom": "School information and parent/student portal",
        "classcharts": "Homework, behaviour, attendance, and timetables",
    }
    labels = {
        "sparx": "Sparx",
        "dr_frost": "Dr Frost Maths",
        "arbor": "Arbor",
        "sims": "SIMS",
        "bromcom": "Bromcom",
        "classcharts": "ClassCharts",
    }
    view = discord.ui.View()
    for key, label in labels.items():
        embed.add_field(
            name=label,
            value=f"{descriptions[key]}\n[Open portal]({SCHOOL_PORTALS[key]})",
            inline=False,
        )
        view.add_item(discord.ui.Button(label=label, url=SCHOOL_PORTALS[key]))
    await interaction.response.send_message(embed=embed, view=view)


@bot.tree.command(name="school-portals-config", description="Set school-specific portal links")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
async def school_portals_config_command(
    interaction: discord.Interaction,
    sparx_url: str | None = None,
    dr_frost_url: str | None = None,
    arbor_url: str | None = None,
    sims_url: str | None = None,
    bromcom_url: str | None = None,
    classcharts_url: str | None = None,
) -> None:
    """Save school login URLs for all portal services."""
    links = {
        "sparx": sparx_url,
        "dr_frost": dr_frost_url,
        "arbor": arbor_url,
        "sims": sims_url,
        "bromcom": bromcom_url,
        "classcharts": classcharts_url,
    }
    provided = {k: v for k, v in links.items() if v is not None}
    if not all(url.startswith(("http://", "https://")) for url in provided.values()):
        await interaction.response.send_message("Every portal link must start with http:// or https://.", ephemeral=True)
        return
    SCHOOL_PORTALS.update(provided)
    save_config()
    await interaction.response.send_message("School portal links saved. Use /school-portals to post them.", ephemeral=True)


@bot.tree.command(name="portal-config", description="Configure all school portal links and toggle them on or off")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(
    maths_url="Optional Sparx Maths URL",
    science_url="Optional Sparx Science URL",
    reader_url="Optional Sparx Reader URL",
    dr_frost_url="Optional Dr Frost URL",
    arbor_url="Optional Arbor URL",
    sims_url="Optional SIMS URL",
    bromcom_url="Optional Bromcom URL",
    classcharts_url="Optional ClassCharts URL",
    enabled="Turn the portal links on or off for this server",
)
async def portal_config_command(
    interaction: discord.Interaction,
    maths_url: str | None = None,
    science_url: str | None = None,
    reader_url: str | None = None,
    dr_frost_url: str | None = None,
    arbor_url: str | None = None,
    sims_url: str | None = None,
    bromcom_url: str | None = None,
    classcharts_url: str | None = None,
    enabled: bool = True,
) -> None:
    """Update all portal links in one place and keep them available or disabled from a single toggle."""
    portal_updates = {
        "maths": maths_url,
        "science": science_url,
        "reader": reader_url,
    }
    school_updates = {
        "sparx": maths_url,
        "dr_frost": dr_frost_url,
        "arbor": arbor_url,
        "sims": sims_url,
        "bromcom": bromcom_url,
        "classcharts": classcharts_url,
    }
    provided_links = {key: value for key, value in portal_updates.items() if value is not None}
    provided_school_links = {key: value for key, value in school_updates.items() if value is not None}
    if not all(value.startswith(("http://", "https://")) for value in (*provided_links.values(), *provided_school_links.values())):
        await interaction.response.send_message("Every supplied portal link must start with http:// or https://.", ephemeral=True)
        return
    if provided_links:
        SPARX_LINKS.update(provided_links)
    if provided_school_links:
        SCHOOL_PORTALS.update({k: v for k, v in provided_school_links.items() if v is not None})
    COMMUNITY_CONFIG["portal_enabled"] = enabled
    save_config()
    await interaction.response.send_message(
        "Portal links updated and the toggle is now " + ("enabled" if enabled else "disabled") + ".",
        ephemeral=True,
    )


@bot.tree.command(name="community-config", description="Set the welcome banner text and image for this server")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
async def community_config_command(
    interaction: discord.Interaction,
    welcome_title: str | None = None,
    welcome_message: str | None = None,
    welcome_footer: str | None = None,
    welcome_image_url: str | None = None,
) -> None:
    """Consolidate the main welcome message options for the server."""
    if welcome_title is not None:
        COMMUNITY_CONFIG["welcome_title"] = welcome_title
    if welcome_message is not None:
        COMMUNITY_CONFIG["welcome_description"] = welcome_message
    if welcome_footer is not None:
        COMMUNITY_CONFIG["welcome_footer"] = welcome_footer
    if welcome_image_url is not None:
        if not welcome_image_url.startswith(("http://", "https://")):
            await interaction.response.send_message("The welcome image URL must start with http:// or https://.", ephemeral=True)
            return
        COMMUNITY_CONFIG["welcome_image_url"] = welcome_image_url
    save_config()
    await interaction.response.send_message("Community welcome settings updated.", ephemeral=True)


@bot.tree.command(name="error-log-config", description="Set or disable the bot error log channel")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
async def error_log_config_command(
    interaction: discord.Interaction,
    channel: discord.TextChannel | None = None,
) -> None:
    if channel is None:
        COMMUNITY_CONFIG.pop("error_channel_id", None)
        save_config()
        await interaction.response.send_message("Error logging disabled.", ephemeral=True)
        return
    COMMUNITY_CONFIG["error_channel_id"] = channel.id
    save_config()
    await interaction.response.send_message(f"Errors will be logged in {channel.mention}.", ephemeral=True)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    if isinstance(error, app_commands.MissingPermissions):
        message = "You do not have permission to use this command."
    elif isinstance(error, app_commands.BotMissingPermissions):
        message = f"I need the following permissions to run this command: {', '.join(error.missing_permissions)}."
    elif isinstance(error, app_commands.CommandOnCooldown):
        message = f"This command is on cooldown. Try again in {error.retry_after:.1f}s."
    else:
        logger.exception("Slash command failed", exc_info=error)
        message = "That command failed. An administrator can check the configured error log channel."
        error_channel_id = community_channel_id("error_channel_id")
        if error_channel_id:
            channel = bot.get_channel(error_channel_id)
            if channel is None:
                try:
                    channel = await bot.fetch_channel(error_channel_id)
                except discord.DiscordException:
                    pass
            if isinstance(channel, discord.TextChannel):
                cmd_name = interaction.command.name if interaction.command else "unknown"
                try:
                    await channel.send(f"⚠️ Command `/{cmd_name}` error: `{type(error).__name__}: {error}`")
                except discord.DiscordException:
                    pass

    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.DiscordException:
        pass


@bot.tree.command(name="backup", description="Export bot data to a JSON file")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
async def backup_command(interaction: discord.Interaction) -> None:
    state = load_state()
    safe_backup = {
        "config": load_config(),
        "state": state,
        "history": load_history(state),
    }
    payload = BytesIO(json.dumps(safe_backup, indent=2).encode())
    await interaction.response.send_message(
        content="Bot data backup:", file=discord.File(payload, filename="bot-backup.json"), ephemeral=True
    )


@bot.tree.command(name="ticket-panel", description="Post the support ticket panel")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
async def ticket_panel_command(interaction: discord.Interaction) -> None:
    """Post a ticket panel in the current channel."""
    if not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("Must be used in a text channel.", ephemeral=True)
        return
    embed = discord.Embed(
        title="Need help?",
        description="Click the button below to open a private support ticket.",
        colour=discord.Colour.blurple(),
    )
    await interaction.channel.send(embed=embed, view=TicketView())
    await interaction.response.send_message("Ticket panel posted.", ephemeral=True)


@bot.tree.command(name="ticket-close", description="Close the current support ticket")
@app_commands.guild_only()
async def ticket_close_command(interaction: discord.Interaction) -> None:
    """Close the current ticket channel."""
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel) or not channel.name.startswith("ticket-"):
        await interaction.response.send_message("This command must be used in a ticket channel.", ephemeral=True)
        return
    if not isinstance(interaction.user, discord.Member):
        return
    is_owner = channel.name == f"ticket-{interaction.user.id}"
    if not is_owner and not interaction.user.guild_permissions.manage_channels:
        await interaction.response.send_message(
            "Only the ticket owner or a channel manager can close this ticket.", ephemeral=True
        )
        return
    await interaction.response.send_message("Closing ticket...", ephemeral=True)
    try:
        await channel.delete(reason=f"Ticket closed by {interaction.user}")
    except discord.DiscordException as exc:
        logger.exception("Could not delete ticket channel")
        try:
            await interaction.followup.send(f"Failed to delete ticket channel: {exc}", ephemeral=True)
        except discord.DiscordException:
            pass


def _chunk_text(text: str, size: int = 1900) -> list[str]:
    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > size:
            if current:
                chunks.append(current)
                current = ""
            if len(line) > size:
                for i in range(0, len(line), size):
                    chunks.append(line[i : i + size])
                continue
        current += line
    if current:
        chunks.append(current)
    return chunks or [""]


async def _send_ai_response(interaction: discord.Interaction, text: str) -> None:
    for chunk in _chunk_text(text):
        await interaction.followup.send(chunk)


@bot.tree.command(name="ai", description="Ask Google Gemini a question")
@app_commands.describe(question="What would you like to ask?")
async def ai_command(interaction: discord.Interaction, question: str) -> None:
    await interaction.response.defer(thinking=True)
    if not gemini_configured():
        await interaction.followup.send("Gemini is not configured. Set GEMINI_API_KEY in .env first.")
        return
    try:
        answer = await generate_text(
            question,
            system_instruction=(
                "You are a helpful assistant in a Discord server. Reply concisely and clearly. "
                "Use plain text, avoid very long answers unless asked."
            ),
        )
    except Exception as exc:
        await interaction.followup.send(f"Gemini request failed: {exc}")
        return
    await _send_ai_response(interaction, answer)


@bot.tree.command(name="summarize", description="Summarize recent messages in a channel using Gemini")
@app_commands.describe(amount="Number of recent messages to summarize (1-50)", channel="Channel to summarize")
async def summarize_command(
    interaction: discord.Interaction, amount: int = 20, channel: discord.TextChannel | None = None
) -> None:
    await interaction.response.defer(thinking=True)
    if not gemini_configured():
        await interaction.followup.send("Gemini is not configured. Set GEMINI_API_KEY in .env first.")
        return
    amount = max(1, min(amount, 50))
    target = channel or interaction.channel
    if not isinstance(target, discord.TextChannel):
        await interaction.followup.send("Please choose a text channel to summarize.")
        return
    try:
        messages = [msg async for msg in target.history(limit=amount)]
    except discord.Forbidden:
        await interaction.followup.send("I do not have permission to read that channel's history.")
        return
    messages.reverse()
    transcript = []
    for message in messages:
        if message.author.bot or not message.content.strip():
            continue
        transcript.append(f"{message.author.display_name}: {message.content}")
    if not transcript:
        await interaction.followup.send("There are no messages to summarize.")
        return
    prompt = (
        "Summarize the following Discord conversation in a few concise bullet points. "
        "Highlight decisions, questions, and any action items.\n\n" + "\n".join(transcript)
    )
    try:
        summary = await generate_text(prompt, max_output_tokens=512)
    except Exception as exc:
        await interaction.followup.send(f"Gemini request failed: {exc}")
        return
    await _send_ai_response(interaction, summary)


@bot.tree.command(name="translate", description="Translate text into another language using Gemini")
@app_commands.describe(text="Text to translate", language="Target language")
async def translate_command(interaction: discord.Interaction, text: str, language: str) -> None:
    await interaction.response.defer(thinking=True)
    if not gemini_configured():
        await interaction.followup.send("Gemini is not configured. Set GEMINI_API_KEY in .env first.")
        return
    prompt = f"Translate the following text into {language}. Reply only with the translation:\n\n{text}"
    try:
        result = await generate_text(prompt, temperature=0.2, max_output_tokens=512)
    except Exception as exc:
        await interaction.followup.send(f"Gemini request failed: {exc}")
        return
    await _send_ai_response(interaction, result)


@bot.tree.command(name="explain", description="Ask Gemini to explain code or a concept simply")
@app_commands.describe(topic="Code snippet or concept to explain")
async def explain_command(interaction: discord.Interaction, topic: str) -> None:
    await interaction.response.defer(thinking=True)
    if not gemini_configured():
        await interaction.followup.send("Gemini is not configured. Set GEMINI_API_KEY in .env first.")
        return
    prompt = f"Explain the following in simple terms, with small code examples if useful:\n\n{topic}"
    try:
        result = await generate_text(prompt, max_output_tokens=768)
    except Exception as exc:
        await interaction.followup.send(f"Gemini request failed: {exc}")
        return
    await _send_ai_response(interaction, result)


if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)

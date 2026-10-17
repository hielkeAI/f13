import asyncio
import json
import logging
import os
import random
import re
import shutil
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

try:
    import pyfiglet
except ImportError:
    pyfiglet = None

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
    CONFIG_FILE.write_text(json.dumps({
        "discord_channel_id": TARGET_CHANNEL_ID,
        "youtube_channels": YOUTUBE_CHANNELS,
        "social_profiles": SOCIAL_PROFILES,
        "bus": BUS_CONFIG,
        "community": COMMUNITY_CONFIG,
        "sparx": SPARX_LINKS,
        "school_portals": SCHOOL_PORTALS,
    }, indent=2))


saved_config = load_config()
TARGET_CHANNEL_ID = int(saved_config.get("discord_channel_id", TARGET_CHANNEL_ID))
YOUTUBE_CHANNELS.update(saved_config.get("youtube_channels", {}))
SOCIAL_PROFILES.update(saved_config.get("social_profiles", {}))
BUS_CONFIG = saved_config.get("bus", {})
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
message_times: dict[int, list[datetime]] = {}

level_cooldowns: dict[int, datetime] = {}


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


def build_welcome_embed(member: discord.Member) -> discord.Embed:
    welcome_title = COMMUNITY_CONFIG.get("welcome_title", "Welcome aboard Transdev Route 36! 🎉")
    welcome_description = COMMUNITY_CONFIG.get(
        "welcome_description",
        "Welcome {member_mention}!\n\nYou are member **{member_count}** of {guild_name}.",
    )
    welcome_footer = COMMUNITY_CONFIG.get("welcome_footer", "Transdev Route 36 community welcome")
    description = welcome_description.format(
        member_mention=member.mention,
        member_count=f"{member.guild.member_count:,}",
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
async def on_message(message: discord.Message) -> None:
    if message.author.bot or message.guild is None:
        return

    community_state = load_community_state()
    moderation = COMMUNITY_CONFIG.get("moderation", {})
    banned_words = [word.casefold() for word in moderation.get("banned_words", [])]
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
        recent = [stamp for stamp in message_times.get(message.author.id, []) if (datetime.now(timezone.utc) - stamp).total_seconds() < 10]
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
            await message.channel.send(
                f"🎉 {message.author.mention} reached level **{user['level']}**!"
            )

    await bot.process_commands(message)


async def apply_reaction_role(payload: discord.RawReactionActionEvent, add: bool) -> None:
    if payload.guild_id is None or payload.user_id == bot.user.id:
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
        if interaction.guild.me:
            overwrites[interaction.guild.me] = discord.PermissionOverwrite(
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
        await interaction.response.send_message("Closing ticket...", ephemeral=True)
        await channel.delete(reason=f"Ticket closed by {interaction.user}")


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

hielkemap_group = app_commands.Group(
    name="hielkemap",
    description="Manage channel speaking permissions",
)
ban_group = app_commands.Group(
    name="ban",
    description="Lock a channel",
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
        "Channel locked. Members can no longer send messages here.", ephemeral=False
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
        "Channel unlocked. The normal server permissions are restored.", ephemeral=False
    )


bot.tree.add_command(hielkemap_group)


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
            headers={"User-Agent": "Mozilla/5.0"},
        ) as response:
            page = await response.text()
            if response.status != 200:
                raise RuntimeError(f"TikTok profile unavailable for @{profile['handle']}")
            marker = '"followerCount":'
            position = page.find(marker)
            if position == -1:
                raise RuntimeError(f"TikTok follower count unavailable for @{profile['handle']}")
            count_start = position + len(marker)
            count_end = count_start
            while count_end < len(page) and page[count_end].isdigit():
                count_end += 1
            return {
                "platform": "TikTok",
                "url": profile["url"],
                "handle": profile["handle"],
                "subscribers": int(page[count_start:count_end]),
                "hidden": False,
            }

    async def fetch_all(self) -> dict[str, dict[str, Any]]:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            results = {}
            twitch_profiles = {
                name: profile for name, profile in SOCIAL_PROFILES.items()
                if profile["platform"] == "Twitch"
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
                if profile["platform"] == "TikTok":
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
            stop_urls: list[str] = []
            for locality_url in locality_urls[:10]:
                async with session.get(
                    f"https://bustimes.org{locality_url}",
                    headers={"User-Agent": "Mozilla/5.0"},
                ) as response:
                    locality_page = await response.text()
                stop_urls.extend(re.findall(r'href="(/stops/[^"]+)"', locality_page))

            matches = []
            query_lower = query.casefold()
            for stop_path in list(dict.fromkeys(stop_urls))[:100]:
                async with session.get(
                    f"https://bustimes.org{stop_path}",
                    headers={"User-Agent": "Mozilla/5.0"},
                ) as response:
                    stop_page = await response.text()
                title_match = re.search(r'<h1[^>]*>(.*?)</h1>', stop_page, re.DOTALL)
                stop_name = " ".join(re.sub(r"<.*?>", " ", title_match.group(1)).split()) if title_match else ""
                if query_lower not in stop_name.casefold():
                    continue
                stop_code = stop_path.rsplit("/", 1)[-1]
                async with session.get(
                    f"https://bustimes.org/api/stops/{stop_code}/",
                    headers={"User-Agent": "Mozilla/5.0"},
                ) as response:
                    stop_data = await response.json()
                routes = {item.casefold() for item in (stop_data.get("line_names") or [])}
                if route != "*" and route.casefold() not in routes:
                    continue
                matches.append({
                    "name": stop_name,
                    "url": f"https://bustimes.org{stop_path}",
                })
                if len(matches) == 10:
                    break
            return matches

    async def fetch_departures(self) -> dict[str, Any]:
        stop_url = BUS_CONFIG.get("stop_url")
        route_filter = BUS_CONFIG.get("route")
        if not stop_url or not route_filter:
            raise RuntimeError("Configure a Bustimes stop with /bus-config first")
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(stop_url, headers={"User-Agent": "Mozilla/5.0"}) as response:
                if response.status != 200:
                    raise RuntimeError(f"Bustimes stop page returned HTTP {response.status}")
                page = await response.text()

        table_match = re.search(
            r'<div[^>]*\bid="departures"[^>]*>.*?</table>',
            page,
            re.DOTALL,
        )
        if not table_match:
            raise RuntimeError("Bustimes did not return a departures table")
        departures = []
        for row in re.findall(r"<tr>(.*?)</tr>", table_match.group(0), re.DOTALL):
            cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
            if len(cells) < 4:
                continue
            route = re.sub(r"<.*?>", " ", cells[0]).strip()
            route = " ".join(route.split())
            if route_filter != "*" and route.casefold() != route_filter.casefold():
                continue
            destination = re.sub(r'<div class="vehicle">.*?</div>', "", cells[1], flags=re.DOTALL)
            destination = re.sub(r"<.*?>", " ", destination).strip()
            destination = " ".join(destination.split())
            scheduled = re.sub(r"<.*?>", " ", cells[2]).strip()
            expected = re.sub(r"<.*?>", " ", cells[3]).strip()
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
    channel_id = community_channel_id("monitor_channel_id")
    if not channel_id:
        return
    channel = bot.get_channel(channel_id)
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
                    channel_id = tracked_channels.get(name, {}).get("id")
                    if not channel_id:
                        continue
                    rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
                    async with session.get(rss_url) as response:
                        feed = await response.text()
                    video_match = re.search(r"<yt:videoId>([^<]+)</yt:videoId>", feed)
                    title_match = re.search(r"<title>([^<]+)</title>", feed)
                    video_id = video_match.group(1) if video_match else None
                    if not video_id or monitor_state.get(f"youtube:{name}") == video_id:
                        monitor_state[f"youtube:{name}"] = video_id
                        continue
                    monitor_state[f"youtube:{name}"] = video_id
                    title = title_match.group(1) if title_match else "New upload"
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
                        await channel.send(
                            f"🔴 **{name} is live:** {stream['title']}\n{profile['url']}"
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


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        logger.warning("Could not read %s; starting with empty state", STATE_FILE)
        return {}


def save_state(state: dict[str, Any]) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


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
            if not channel["hidden"]
        },
    })
    state["history"] = history[-1000:]
    HISTORY_FILE.write_text(json.dumps(state["history"], indent=2))


def build_chart(
    state: dict[str, Any],
    channels: dict[str, dict[str, Any]],
    days: int = 30,
) -> BytesIO:
    names = list(channels)
    current = [channels[name]["subscribers"] for name in names]
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    history = [
        entry
        for entry in state.get("history", [])
        if datetime.fromisoformat(entry["timestamp"]).astimezone(timezone.utc) >= cutoff
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

    plotted = False
    for name, colour in zip(names, colours):
        points = [
            (entry["timestamp"], entry["channels"].get(name))
            for entry in history
            if entry["channels"].get(name) is not None
        ]
        if points:
            dates = [datetime.fromisoformat(timestamp).astimezone(timezone.utc) for timestamp, _ in points]
            values = [value for _, value in points]
            axes[1].plot(dates, values, marker="o", markersize=3, label=name, color=colour)
            plotted = True
    axes[1].set_title("Subscriber history")
    axes[1].set_ylabel("Subscribers")
    if plotted:
        history_values = [
            value
            for entry in history
            for value in entry["channels"].values()
        ]
        history_min = min(history_values)
        history_max = max(history_values)
        history_padding = max((history_max - history_min) * 0.15, 5)
        axes[1].set_ylim(
            max(0, history_min - history_padding),
            history_max + history_padding,
        )
        axes[1].set_title(f"Subscriber history - last {days} days")
        axes[1].xaxis.set_major_formatter(mdates.DateFormatter("%d/%m/%Y", tz=timezone.utc))
        axes[1].legend(facecolor="#2f3136", labelcolor="#dcddde")
        figure.autofmt_xdate()
    else:
        axes[1].set_title(f"Subscriber history - last {days} days")
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
    if channel["hidden"]:
        return "Hidden by channel"
    return f"{channel['subscribers']:,}"


def format_uk_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")


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
            value=f"**Followers/Subscribers:** {format_subscribers(channel)}\n[Open profile]({profile['url']})",
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
        except discord.NotFound:
            pass
    if message is None:
        message = await channel.send(embed=embed)
        BUS_CONFIG["message_id"] = message.id
        save_config()
    else:
        await message.edit(embed=embed)


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
        except discord.NotFound:
            logger.info("Tracked status message no longer exists; creating a new one")
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
        await old_message.edit(embed=embed, attachments=[chart_file])
        state["channels"] = channels
        save_state(state)
        return

    message = await target.send(embed=embed, file=chart_file)
    state["message_id"] = message.id
    state["channels"] = channels
    save_state(state)


@bot.event
async def on_ready() -> None:
    logger.info("Logged in as %s", bot.user)
    if not getattr(bot, "ticket_views_registered", False):
        bot.add_view(TicketView())
        bot.add_view(TicketCloseView())
        bot.ticket_views_registered = True
    await bot.tree.sync()
    logger.info("Synced slash commands globally to %s guilds", len(bot.guilds))
    if not update_loop.is_running():
        update_loop.start()
    if not bus_update_loop.is_running():
        bus_update_loop.start()
    if not social_monitor_loop.is_running():
        social_monitor_loop.start()
    if not reminder_loop.is_running():
        reminder_loop.start()
    if not getattr(bot, "role_views_registered", False):
        for panel in COMMUNITY_CONFIG.get("role_panels", []):
            bot.add_view(RoleButtonView(int(panel["role_id"]), panel["label"]))
        bot.role_views_registered = True


@tasks.loop(minutes=UPDATE_INTERVAL_MINUTES)
async def update_loop() -> None:
    await update_status()


@tasks.loop(minutes=5)
async def bus_update_loop() -> None:
    await update_bus_status()


@tasks.loop(minutes=5)
async def social_monitor_loop() -> None:
    await monitor_socials()


@update_loop.before_loop
async def before_update_loop() -> None:
    await bot.wait_until_ready()


@bus_update_loop.before_loop
async def before_bus_update_loop() -> None:
    await bot.wait_until_ready()


@social_monitor_loop.before_loop
async def before_social_monitor_loop() -> None:
    await bot.wait_until_ready()


@tasks.loop(seconds=30)
async def reminder_loop() -> None:
    state = load_state()
    reminders = state.get("reminders", [])
    now = datetime.now(timezone.utc)
    remaining = []
    for reminder in reminders:
        if datetime.fromisoformat(reminder["due"]) > now:
            remaining.append(reminder)
            continue
        try:
            user = bot.get_user(int(reminder["user_id"])) or await bot.fetch_user(int(reminder["user_id"]))
            await user.send(f"⏰ Reminder: {reminder['message']}")
        except discord.DiscordException:
            logger.warning("Could not deliver reminder to %s", reminder.get("user_id"))
    if len(remaining) != len(reminders):
        state["reminders"] = remaining
        save_state(state)


@reminder_loop.before_loop
async def before_reminder_loop() -> None:
    await bot.wait_until_ready()


@bot.tree.command(name="subs", description="Refresh the YouTube subscriber tracker")
@app_commands.guild_only()
async def subs_command(interaction: discord.Interaction) -> None:
    """Refresh the subscriber message immediately."""
    if interaction.channel_id != TARGET_CHANNEL_ID:
        await interaction.response.send_message(
            f"Use this command in <#{TARGET_CHANNEL_ID}>.", ephemeral=True
        )
        return
    await interaction.response.defer(ephemeral=True)
    await update_status()
    await interaction.followup.send("Subscriber counts refreshed.", ephemeral=True)


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


@bot.tree.command(name="help", description="Show the bot command list")
@app_commands.guild_only()
async def help_command(interaction: discord.Interaction) -> None:
    """Show the main command groups."""
    embed = discord.Embed(title="Bot commands", colour=discord.Colour.blurple())
    embed.add_field(name="Community", value="`/level` `/leaderboard` `/poll` `/remind`", inline=False)
    embed.add_field(name="Tracking", value="`/subs` `/graph` `/bus-add` `/bus-refresh`", inline=False)
    embed.add_field(name="Server tools", value="`/serverinfo` `/userinfo` `/avatar` `/uptime` `/bot-status`", inline=False)
    embed.add_field(name="Staff", value="`/announce` `/clear` `/moderation-config` `/backup` `/ticket-panel`", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


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


@bot.tree.command(name="serverinfo", description="Show server information")
@app_commands.guild_only()
async def serverinfo_command(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    embed = discord.Embed(title=guild.name, colour=discord.Colour.blurple())
    embed.add_field(name="Members", value=f"{guild.member_count:,}")
    embed.add_field(name="Channels", value=str(len(guild.channels)))
    embed.add_field(name="Created", value=format_uk_datetime(guild.created_at))
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="userinfo", description="Show information about a member")
@app_commands.guild_only()
async def userinfo_command(interaction: discord.Interaction, member: discord.Member | None = None) -> None:
    member = member or interaction.user
    embed = discord.Embed(title=member.display_name, colour=member.colour)
    embed.add_field(name="ID", value=str(member.id))
    embed.add_field(name="Joined", value=format_uk_datetime(member.joined_at or member.created_at))
    embed.add_field(name="Account created", value=format_uk_datetime(member.created_at))
    embed.set_thumbnail(url=member.display_avatar.url)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="avatar", description="Show a member's avatar")
@app_commands.guild_only()
async def avatar_command(interaction: discord.Interaction, member: discord.Member | None = None) -> None:
    member = member or interaction.user
    embed = discord.Embed(title=f"{member.display_name}'s avatar", colour=member.colour)
    embed.set_image(url=member.display_avatar.url)
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
        local_time = datetime.strptime(date_time.strip(), "%d/%m/%Y %H:%M")
        local_time = local_time.replace(tzinfo=ZoneInfo("Europe/London"))
    except ValueError:
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
        f"Guilds: **{len(bot.guilds)}**\nBus monitor: **{'on' if BUS_CONFIG.get('channel_id') else 'off'}**",
        ephemeral=True,
    )


@bot.tree.command(name="leaderboard", description="Show the server XP leaderboard")
@app_commands.guild_only()
async def leaderboard_command(interaction: discord.Interaction) -> None:
    """Show the ten members with the most XP."""
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
        name = member.display_name if member else f"User {user_id}"
        lines.append(
            f"**{rank}.** {name} - Level **{int(data.get('level', 0))}** "
            f"({int(data.get('xp', 0)):,} XP)"
        )
    embed = discord.Embed(
        title="🏆 XP Leaderboard",
        description=(
            f"Server members: **{interaction.guild.member_count:,}**\n\n"
            + "\n".join(lines)
        ),
        colour=discord.Colour.gold(),
    )
    embed.set_image(url=COMMUNITY_CONFIG.get("welcome_image_url", DEFAULT_WELCOME_BUS_IMAGE))
    embed.set_footer(text="Earn XP by chatting once per minute")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="poll", description="Create a simple yes/no poll")
@app_commands.guild_only()
async def poll_command(interaction: discord.Interaction, question: str) -> None:
    embed = discord.Embed(title="📊 Poll", description=question, colour=discord.Colour.blurple())
    embed.set_footer(text=f"Poll by {interaction.user.display_name}")
    message = await interaction.channel.send(embed=embed)
    await message.add_reaction("✅")
    await message.add_reaction("❌")
    await interaction.response.send_message("Poll created.", ephemeral=True)


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
    answers = ["Yes.", "No.", "Probably.", "Probably not.", "Ask again later.", "Absolutely."]
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
    await channel.send(f"📢 **Announcement from {interaction.guild.name}**\n{message}")
    await interaction.response.send_message("Announcement sent.", ephemeral=True)


@bot.tree.command(name="clear", description="Delete recent messages")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_messages=True)
async def clear_command(interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100],) -> None:
    if not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("Use this in a text channel.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.followup.send(f"Deleted {len(deleted)} messages.", ephemeral=True)


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
    panels = COMMUNITY_CONFIG.setdefault("role_panels", [])
    panel = {"role_id": role.id, "label": label[:80]}
    panels[:] = [item for item in panels if int(item["role_id"]) != role.id]
    panels.append(panel)
    save_config()
    await interaction.channel.send(
        f"Click the button to get or remove {role.mention}.",
        view=RoleButtonView(role.id, panel["label"]),
    )
    await interaction.response.send_message("Role panel posted.", ephemeral=True)


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
    else:
        logger.exception("Slash command failed", exc_info=error)
        message = "That command failed. An administrator can check the configured error log channel."
        error_channel_id = community_channel_id("error_channel_id")
        if error_channel_id:
            channel = bot.get_channel(error_channel_id)
            if isinstance(channel, discord.TextChannel):
                await channel.send(f"⚠️ Command error: `{type(error).__name__}: {error}`")
    if not interaction.response.is_done():
        await interaction.response.send_message(message, ephemeral=True)


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
    await channel.delete(reason=f"Ticket closed by {interaction.user}")


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
    removed = YOUTUBE_CHANNELS.pop(name.strip(), None)
    removed = SOCIAL_PROFILES.pop(name.strip(), removed)
    if removed is None:
        await interaction.response.send_message(f"No profile named **{name}** was found.", ephemeral=True)
        return
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


# ============================================================================
# HIELKEMAP ADMIN/MOD SUITE - THE ULTIMATE COMMAND CENTER
# ============================================================================

# --- Data structures for the suite ---
HIELKEMAP_CONFIG = saved_config.get("hielkemap", {})
HIELKEMAP_CONFIG.setdefault("prefix", "!")
HIELKEMAP_CONFIG.setdefault("mod_log_channel", None)
HIELKEMAP_CONFIG.setdefault("auto_mod_enabled", True)
HIELKEMAP_CONFIG.setdefault("warn_threshold", 3)
HIELKEMAP_CONFIG.setdefault("mute_duration", 10)
HIELKEMAP_CONFIG.setdefault("starboard_channel", None)
HIELKEMAP_CONFIG.setdefault("starboard_threshold", 5)
HIELKEMAP_CONFIG.setdefault("custom_commands", {})
HIELKEMAP_CONFIG.setdefault("auto_roles", [])
HIELKEMAP_CONFIG.setdefault("protected_roles", [])
HIELKEMAP_CONFIG.setdefault("protected_channels", [])
HIELKEMAP_CONFIG.setdefault("welcome_screen", {})


def save_hielkemap_config() -> None:
    saved_config["hielkemap"] = HIELKEMAP_CONFIG
    save_config()


def get_mod_log_channel(guild: discord.Guild) -> discord.TextChannel | None:
    channel_id = HIELKEMAP_CONFIG.get("mod_log_channel")
    if channel_id:
        channel = guild.get_channel(int(channel_id))
        if isinstance(channel, discord.TextChannel):
            return channel
    return None


def log_mod_action(guild: discord.Guild, action: str, moderator: discord.Member | discord.User, target: discord.Member | discord.User, reason: str = "") -> None:
    mod_log = get_mod_log_channel(guild)
    if mod_log:
        embed = discord.Embed(
            title=f"{action}",
            description=f"**Moderator:** {moderator.mention}\n**Target:** {target.mention}\n**Reason:** {reason or 'Not specified'}",
            colour=discord.Colour.red() if "ban" in action.lower() or "kick" in action.lower() else discord.Colour.orange(),
            timestamp=datetime.now(timezone.utc),
        )
        try:
            mod_log.send(embed=embed)
        except discord.DiscordException:
            pass


# --- Warning System ---
WARN_CONFIG = HIELKEMAP_CONFIG.setdefault("warns", {})


def get_user_warns(user_id: int) -> list[dict[str, Any]]:
    return WARN_CONFIG.get(str(user_id), [])


def add_user_warn(user_id: int, moderator_id: int, reason: str) -> None:
    warns = WARN_CONFIG.setdefault(str(user_id), [])
    warns.append({
        "moderator_id": moderator_id,
        "reason": reason,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    save_hielkemap_config()


def clear_user_warns(user_id: int) -> int:
    warns = WARN_CONFIG.pop(str(user_id), [])
    save_hielkemap_config()
    return len(warns)


# --- Mute System ---
MUTE_ROLE_NAME = "HielkeMute"


async def get_or_create_mute_role(guild: discord.Guild) -> discord.Role | None:
    mute_role = discord.utils.get(guild.roles, name=MUTE_ROLE_NAME)
    if mute_role:
        return mute_role
    try:
        mute_role = await guild.create_role(
            name=MUTE_ROLE_NAME,
            permissions=discord.Permissions.none(),
            reason="HielkeMap mute role",
        )
        for channel in guild.text_channels:
            await channel.set_permissions(mute_role, send_messages=False, speak=False)
        return mute_role
    except discord.Forbidden:
        return None


# --- Starboard System ---
STARBOARD_EMOJI = "⭐"


async def check_starboard_reaction(payload: discord.RawReactionActionEvent) -> None:
    if payload.guild_id is None or str(payload.emoji) != STARBOARD_EMOJI:
        return
    guild = bot.get_guild(payload.guild_id)
    if not guild:
        return
    starboard_channel_id = HIELKEMAP_CONFIG.get("starboard_channel")
    if not starboard_channel_id:
        return
    starboard_channel = guild.get_channel(int(starboard_channel_id))
    if not isinstance(starboard_channel, discord.TextChannel):
        return
    channel = guild.get_channel(payload.channel_id)
    if not isinstance(channel, discord.TextChannel):
        return
    threshold = HIELKEMAP_CONFIG.get("starboard_threshold", 5)
    try:
        message = await channel.fetch_message(payload.message_id)
    except discord.DiscordException:
        return
    if message.author.bot:
        return
    reaction = discord.utils.get(message.reactions, emoji=STARBOARD_EMOJI)
    if not reaction or reaction.count < threshold:
        return
    starboard_state = load_state()
    starred = starboard_state.setdefault("starboard", {})
    message_key = f"{channel.id}-{message.id}"
    if message_key in starred:
        return
    starred[message_key] = True
    save_state(starboard_state)
    embed = discord.Embed(
        title="⭐ Starred Message",
        description=message.content[:2000],
        colour=discord.Colour.gold(),
    )
    embed.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)
    embed.add_field(name="Channel", value=channel.mention)
    embed.add_field(name="Stars", value=str(reaction.count))
    if message.attachments:
        embed.set_image(url=message.attachments[0].url)
    await starboard_channel.send(
        f"⭐ **{reaction.count}** | {channel.mention}",
        embed=embed,
    )


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent) -> None:
    await apply_reaction_role(payload, True)
    await check_starboard_reaction(payload)


@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent) -> None:
    await apply_reaction_role(payload, False)


# ============================================================================
# HIELKEMAP COMMAND GROUP
# ============================================================================

hielkemap_group = app_commands.Group(name="hielkemap", description="The ultimate admin/mod command suite")


# --- Moderation Subcommands ---

@hielkemap_group.command(name="ban", description="Ban a member from the server")
@app_commands.guild_only()
@app_commands.checks.has_permissions(ban_members=True)
@app_commands.describe(member="The member to ban", reason="Reason for the ban")
async def hielkemap_ban(interaction: discord.Interaction, member: discord.Member, reason: str = "") -> None:
    if member.id == interaction.user.id:
        await interaction.response.send_message("You cannot ban yourself.", ephemeral=True)
        return
    if interaction.guild and member == interaction.guild.owner:
        await interaction.response.send_message("Cannot ban the server owner.", ephemeral=True)
        return
    if interaction.guild and interaction.guild.me and member.top_role >= (interaction.guild.me.top_role if interaction.guild.me else None):
        await interaction.response.send_message("Cannot ban members with higher or equal role.", ephemeral=True)
        return
    try:
        await member.ban(reason=reason or f"Banned by {interaction.user}")
        log_mod_action(interaction.guild, "Member Banned", interaction.user, member, reason)
        await interaction.response.send_message(f"✅ **{member}** has been banned.", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to ban this member.", ephemeral=True)


@hielkemap_group.command(name="unban", description="Unban a member from the server")
@app_commands.guild_only()
@app_commands.checks.has_permissions(ban_members=True)
@app_commands.describe(user_id="The user ID to unban", reason="Reason for unban")
async def hielkemap_unban(interaction: discord.Interaction, user_id: str, reason: str = "") -> None:
    try:
        user_id_int = int(user_id)
    except ValueError:
        await interaction.response.send_message("❌ Invalid user ID.", ephemeral=True)
        return
    try:
        user = await bot.fetch_user(user_id_int)
        await interaction.guild.unban(user, reason=reason or f"Unbanned by {interaction.user}")
        log_mod_action(interaction.guild, "Member Unbanned", interaction.user, user, reason)
        await interaction.response.send_message(f"✅ **{user}** has been unbanned.", ephemeral=True)
    except discord.NotFound:
        await interaction.response.send_message("❌ User not found or not banned.", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to unban.", ephemeral=True)


@hielkemap_group.command(name="kick", description="Kick a member from the server")
@app_commands.guild_only()
@app_commands.checks.has_permissions(kick_members=True)
@app_commands.describe(member="The member to kick", reason="Reason for the kick")
async def hielkemap_kick(interaction: discord.Interaction, member: discord.Member, reason: str = "") -> None:
    if member.id == interaction.user.id:
        await interaction.response.send_message("You cannot kick yourself.", ephemeral=True)
        return
    if interaction.guild and member == interaction.guild.owner:
        await interaction.response.send_message("Cannot kick the server owner.", ephemeral=True)
        return
    if interaction.guild and interaction.guild.me and member.top_role >= (interaction.guild.me.top_role if interaction.guild.me else None):
        await interaction.response.send_message("Cannot kick members with higher or equal role.", ephemeral=True)
        return
    try:
        await member.kick(reason=reason or f"Kicked by {interaction.user}")
        log_mod_action(interaction.guild, "Member Kicked", interaction.user, member, reason)
        await interaction.response.send_message(f"✅ **{member}** has been kicked.", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to kick this member.", ephemeral=True)


@hielkemap_group.command(name="warn", description="Warn a member")
@app_commands.guild_only()
@app_commands.checks.has_permissions(moderate_members=True)
@app_commands.describe(member="The member to warn", reason="Reason for the warning")
async def hielkemap_warn(interaction: discord.Interaction, member: discord.Member, reason: str = "") -> None:
    if member.id == interaction.user.id:
        await interaction.response.send_message("You cannot warn yourself.", ephemeral=True)
        return
    add_user_warn(member.id, interaction.user.id, reason or "No reason provided")
    warns = get_user_warns(member.id)
    threshold = HIELKEMAP_CONFIG.get("warn_threshold", 3)
    embed = discord.Embed(
        title="⚠️ You have been warned!",
        description=f"**Reason:** {reason or 'No reason provided'}\n\n**This is warning {len(warns)}** of {threshold} before auto-mute.",
        colour=discord.Colour.orange(),
    )
    embed.set_footer(text=f"Moderator: {interaction.user.display_name}")
    try:
        await member.send(embed=embed)
    except discord.Forbidden:
        pass
    log_mod_action(interaction.guild, "Member Warned", interaction.user, member, reason)
    await interaction.response.send_message(f"✅ **{member}** has been warned ({len(warns)}/{threshold}).", ephemeral=True)
    if len(warns) >= threshold:
        mute_role = await get_or_create_mute_role(interaction.guild)
        if mute_role:
            try:
                await member.add_roles(mute_role, reason=f"Auto-mute after {threshold} warnings")
                log_mod_action(interaction.guild, "Auto-Mute Applied", interaction.user, member, f"Reached {threshold} warnings")
            except discord.Forbidden:
                pass


@hielkemap_group.command(name="warns", description="Check a member's warnings")
@app_commands.guild_only()
@app_commands.checks.has_permissions(moderate_members=True)
@app_commands.describe(member="The member to check")
async def hielkemap_warns(interaction: discord.Interaction, member: discord.Member) -> None:
    warns = get_user_warns(member.id)
    if not warns:
        await interaction.response.send_message(f"✅ **{member}** has no warnings.", ephemeral=True)
        return
    embed = discord.Embed(
        title=f"⚠️ Warnings for {member.display_name}",
        colour=discord.Colour.orange(),
    )
    for i, warn in enumerate(warns, 1):
        mod = interaction.guild.get_member(warn["moderator_id"])
        mod_name = mod.display_name if mod else f"Unknown ({warn['moderator_id']})"
        embed.add_field(
            name=f"Warning {i}",
            value=f"**Moderator:** {mod_name}\n**Reason:** {warn['reason']}\n**Date:** {warn['timestamp'][:19]}",
            inline=False,
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@hielkemap_group.command(name="clearwarns", description="Clear a member's warnings")
@app_commands.guild_only()
@app_commands.checks.has_permissions(moderate_members=True)
@app_commands.describe(member="The member to clear warnings for")
async def hielkemap_clearwarns(interaction: discord.Interaction, member: discord.Member) -> None:
    cleared = clear_user_warns(member.id)
    log_mod_action(interaction.guild, "Warnings Cleared", interaction.user, member, f"Cleared {cleared} warnings")
    await interaction.response.send_message(f"✅ Cleared {cleared} warnings for **{member}**.", ephemeral=True)


@hielkemap_group.command(name="mute", description="Mute a member")
@app_commands.guild_only()
@app_commands.checks.has_permissions(moderate_members=True)
@app_commands.describe(member="The member to mute", duration="Duration in minutes (default: 10)", reason="Reason for mute")
async def hielkemap_mute(interaction: discord.Interaction, member: discord.Member, duration: int = 10, reason: str = "") -> None:
    if member.id == interaction.user.id:
        await interaction.response.send_message("You cannot mute yourself.", ephemeral=True)
        return
    if interaction.guild and member == interaction.guild.owner:
        await interaction.response.send_message("Cannot mute the server owner.", ephemeral=True)
        return
    mute_role = await get_or_create_mute_role(interaction.guild)
    if not mute_role:
        await interaction.response.send_message("❌ Could not create mute role. Check my permissions.", ephemeral=True)
        return
    try:
        await member.add_roles(mute_role, reason=reason or f"Muted by {interaction.user}")
        log_mod_action(interaction.guild, "Member Muted", interaction.user, member, reason)
        await interaction.response.send_message(f"✅ **{member}** has been muted for {duration} minutes.", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to mute this member.", ephemeral=True)


@hielkemap_group.command(name="unmute", description="Unmute a member")
@app_commands.guild_only()
@app_commands.checks.has_permissions(moderate_members=True)
@app_commands.describe(member="The member to unmute", reason="Reason for unmute")
async def hielkemap_unmute(interaction: discord.Interaction, member: discord.Member, reason: str = "") -> None:
    mute_role = discord.utils.get(interaction.guild.roles, name=MUTE_ROLE_NAME)
    if not mute_role:
        await interaction.response.send_message("❌ Mute role not found.", ephemeral=True)
        return
    if mute_role not in member.roles:
        await interaction.response.send_message(f"✅ **{member}** is not muted.", ephemeral=True)
        return
    try:
        await member.remove_roles(mute_role, reason=reason or f"Unmuted by {interaction.user}")
        log_mod_action(interaction.guild, "Member Unmuted", interaction.user, member, reason)
        await interaction.response.send_message(f"✅ **{member}** has been unmuted.", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to unmute this member.", ephemeral=True)


@hielkemap_group.command(name="timeout", description="Timeout a member")
@app_commands.guild_only()
@app_commands.checks.has_permissions(moderate_members=True)
@app_commands.describe(member="The member to timeout", duration="Duration in minutes", reason="Reason for timeout")
async def hielkemap_timeout(interaction: discord.Interaction, member: discord.Member, duration: app_commands.Range[int, 1, 1440], reason: str = "") -> None:
    if member.id == interaction.user.id:
        await interaction.response.send_message("You cannot timeout yourself.", ephemeral=True)
        return
    if interaction.guild and member == interaction.guild.owner:
        await interaction.response.send_message("Cannot timeout the server owner.", ephemeral=True)
        return
    try:
        until = datetime.now(timezone.utc) + timedelta(minutes=duration)
        await member.timeout(until, reason=reason or f"Timeout by {interaction.user}")
        log_mod_action(interaction.guild, "Member Timed Out", interaction.user, member, reason)
        await interaction.response.send_message(f"✅ **{member}** has been timed out for {duration} minutes.", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to timeout this member.", ephemeral=True)


@hielkemap_group.command(name="untimeout", description="Remove timeout from a member")
@app_commands.guild_only()
@app_commands.checks.has_permissions(moderate_members=True)
@app_commands.describe(member="The member to untimeout", reason="Reason for removing timeout")
async def hielkemap_untimeout(interaction: discord.Interaction, member: discord.Member, reason: str = "") -> None:
    try:
        await member.timeout(None, reason=reason or f"Timeout removed by {interaction.user}")
        log_mod_action(interaction.guild, "Timeout Removed", interaction.user, member, reason)
        await interaction.response.send_message(f"✅ **{member}** timeout has been removed.", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to remove timeout.", ephemeral=True)


# --- Admin Subcommands ---

@hielkemap_group.command(name="addrole", description="Add a role to a member")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_roles=True)
@app_commands.describe(member="The member to give a role", role="The role to add")
async def hielkemap_addrole(interaction: discord.Interaction, member: discord.Member, role: discord.Role) -> None:
    if role.is_default() or role.managed:
        await interaction.response.send_message("❌ Cannot assign default or managed roles.", ephemeral=True)
        return
    if interaction.guild and interaction.guild.me and role >= (interaction.guild.me.top_role if interaction.guild.me else None):
        await interaction.response.send_message("❌ Cannot assign roles above my highest role.", ephemeral=True)
        return
    if role in member.roles:
        await interaction.response.send_message(f"✅ **{member}** already has {role.mention}.", ephemeral=True)
        return
    try:
        await member.add_roles(role)
        await interaction.response.send_message(f"✅ Gave {role.mention} to **{member}**.", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to assign this role.", ephemeral=True)


@hielkemap_group.command(name="removerole", description="Remove a role from a member")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_roles=True)
@app_commands.describe(member="The member to remove a role from", role="The role to remove")
async def hielkemap_removerole(interaction: discord.Interaction, member: discord.Member, role: discord.Role) -> None:
    if role.is_default() or role.managed:
        await interaction.response.send_message("❌ Cannot remove default or managed roles.", ephemeral=True)
        return
    if role not in member.roles:
        await interaction.response.send_message(f"✅ **{member}** doesn't have {role.mention}.", ephemeral=True)
        return
    try:
        await member.remove_roles(role)
        await interaction.response.send_message(f"✅ Removed {role.mention} from **{member}**.", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to remove this role.", ephemeral=True)


@hielkemap_group.command(name="createrole", description="Create a new role")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_roles=True)
@app_commands.describe(name="Name of the new role", color="Role color (hex code or name)", hoist="Whether to hoist the role", mentionable="Whether the role is mentionable")
async def hielkemap_createrole(
    interaction: discord.Interaction,
    name: str,
    color: str = "default",
    hoist: bool = False,
    mentionable: bool = False,
) -> None:
    try:
        role_color = discord.Colour.from_str(color) if color != "default" else discord.Colour.default()
    except ValueError:
        role_color = discord.Colour.default()
    try:
        role = await interaction.guild.create_role(
            name=name[:100],
            color=role_color,
            hoist=hoist,
            mentionable=mentionable,
            reason=f"Created by {interaction.user}",
        )
        await interaction.response.send_message(f"✅ Created role {role.mention}.", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to create roles.", ephemeral=True)


@hielkemap_group.command(name="deleterole", description="Delete a role")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_roles=True)
@app_commands.describe(role="The role to delete")
async def hielkemap_deleterole(interaction: discord.Interaction, role: discord.Role) -> None:
    if role.is_default() or role.managed:
        await interaction.response.send_message("❌ Cannot delete default or managed roles.", ephemeral=True)
        return
    if role == interaction.guild.me.top_role:
        await interaction.response.send_message("❌ Cannot delete my own role.", ephemeral=True)
        return
    try:
        await role.delete(reason=f"Deleted by {interaction.user}")
        await interaction.response.send_message(f"✅ Deleted role {role.name}.", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to delete this role.", ephemeral=True)


@hielkemap_group.command(name="createchannel", description="Create a new text channel")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_channels=True)
@app_commands.describe(name="Name of the new channel", topic="Channel topic", nsfw="Whether the channel is NSFW")
async def hielkemap_createchannel(
    interaction: discord.Interaction,
    name: str,
    topic: str = "",
    nsfw: bool = False,
) -> None:
    try:
        channel = await interaction.guild.create_text_channel(
            name=name[:100],
            topic=topic[:1024] if topic else None,
            nsfw=nsfw,
            reason=f"Created by {interaction.user}",
        )
        await interaction.response.send_message(f"✅ Created channel {channel.mention}.", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to create channels.", ephemeral=True)


@hielkemap_group.command(name="deletechannel", description="Delete a channel")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_channels=True)
@app_commands.describe(channel="The channel to delete")
async def hielkemap_deletechannel(interaction: discord.Interaction, channel: discord.TextChannel) -> None:
    if channel.id == interaction.channel_id:
        await interaction.response.send_message("❌ Cannot delete the current channel.", ephemeral=True)
        return
    try:
        await channel.delete(reason=f"Deleted by {interaction.user}")
        await interaction.response.send_message(f"✅ Deleted channel {channel.name}.", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to delete this channel.", ephemeral=True)


@hielkemap_group.command(name="slowmode", description="Set slowmode for a channel")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_channels=True)
@app_commands.describe(channel="The channel to set slowmode", seconds="Slowmode delay in seconds (0 to disable)")
async def hielkemap_slowmode(interaction: discord.Interaction, channel: discord.TextChannel, seconds: app_commands.Range[int, 0, 21600]) -> None:
    try:
        await channel.edit(slowmode_delay=seconds)
        await interaction.response.send_message(f"✅ Set slowmode to {seconds} seconds in {channel.mention}.", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to set slowmode.", ephemeral=True)


@hielkemap_group.command(name="lock", description="Lock a channel (deny send_messages for @everyone)")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_channels=True)
@app_commands.describe(channel="The channel to lock", reason="Reason for locking")
async def hielkemap_lock(interaction: discord.Interaction, channel: discord.TextChannel, reason: str = "") -> None:
    try:
        await channel.set_permissions(
            interaction.guild.default_role,
            send_messages=False,
            reason=reason or f"Locked by {interaction.user}",
        )
        await interaction.response.send_message(f"✅ Locked {channel.mention}.", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to lock this channel.", ephemeral=True)


@hielkemap_group.command(name="unlock", description="Unlock a channel")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_channels=True)
@app_commands.describe(channel="The channel to unlock", reason="Reason for unlocking")
async def hielkemap_unlock(interaction: discord.Interaction, channel: discord.TextChannel, reason: str = "") -> None:
    try:
        await channel.set_permissions(
            interaction.guild.default_role,
            send_messages=None,
            reason=reason or f"Unlocked by {interaction.user}",
        )
        await interaction.response.send_message(f"✅ Unlocked {channel.mention}.", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to unlock this channel.", ephemeral=True)


@hielkemap_group.command(name="nickname", description="Change a member's nickname")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_nicknames=True)
@app_commands.describe(member="The member to change nickname", nickname="The new nickname (empty to reset)")
async def hielkemap_nickname(interaction: discord.Interaction, member: discord.Member, nickname: str = "") -> None:
    if nickname:
        nickname = nickname[:32]
    try:
        await member.edit(nick=nickname or None)
        await interaction.response.send_message(f"✅ Changed **{member}** nickname to **{nickname or 'default'}**.", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to change this nickname.", ephemeral=True)


@hielkemap_group.command(name="purge", description="Bulk delete messages")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_messages=True)
@app_commands.describe(amount="Number of messages to delete", user="Only delete messages from this user (optional)")
async def hielkemap_purge(interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100], user: discord.Member | None = None) -> None:
    if not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("❌ Use this in a text channel.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        deleted = await interaction.channel.purge(
            limit=amount,
            check=lambda m: user is None or m.author == user,
        )
        await interaction.followup.send(f"✅ Deleted {len(deleted)} messages.", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send("❌ I don't have permission to delete messages.", ephemeral=True)


# --- Server Management Subcommands ---

@hielkemap_group.command(name="serverinfo", description="Get detailed server information")
@app_commands.guild_only()
async def hielkemap_serverinfo(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    embed = discord.Embed(title=f"🏢 {guild.name}", colour=discord.Colour.blurple())
    embed.add_field(name="👥 Members", value=f"Total: **{guild.member_count:,}**")
    embed.add_field(name="📊 Online", value=f"**{len([m for m in guild.members if m.status != discord.Status.offline])}**")
    embed.add_field(name="🎯 Roles", value=str(len(guild.roles)))
    embed.add_field(name="💬 Channels", value=f"Text: **{len(guild.text_channels)}** | Voice: **{len(guild.voice_channels)}**")
    embed.add_field(name="📅 Created", value=format_uk_datetime(guild.created_at))
    embed.add_field(name="👑 Owner", value=str(guild.owner))
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    if guild.banner:
        embed.set_image(url=guild.banner.url)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@hielkemap_group.command(name="membercount", description="Get member count with breakdown")
@app_commands.guild_only()
async def hielkemap_membercount(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    online = len([m for m in guild.members if m.status == discord.Status.online])
    idle = len([m for m in guild.members if m.status == discord.Status.idle])
    dnd = len([m for m in guild.members if m.status == discord.Status.dnd])
    offline = len([m for m in guild.members if m.status == discord.Status.offline])
    bots = len([m for m in guild.members if m.bot])
    humans = guild.member_count - bots
    embed = discord.Embed(title="👥 Member Statistics", colour=discord.Colour.green())
    embed.add_field(name="Total", value=f"**{guild.member_count:,}**")
    embed.add_field(name="🤖 Bots", value=str(bots))
    embed.add_field(name="👨‍👩‍👧‍👦 Humans", value=str(humans))
    embed.add_field(name="🟢 Online", value=str(online))
    embed.add_field(name="🟡 Idle", value=str(idle))
    embed.add_field(name="🔴 DND", value=str(dnd))
    embed.add_field(name="⚫ Offline", value=str(offline))
    await interaction.response.send_message(embed=embed, ephemeral=True)


@hielkemap_group.command(name="listroles", description="List all server roles")
@app_commands.guild_only()
async def hielkemap_listroles(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    roles = sorted(guild.roles, key=lambda r: r.position, reverse=True)
    chunks = [roles[i:i+20] for i in range(0, len(roles), 20)]
    for i, chunk in enumerate(chunks):
        embed = discord.Embed(
            title=f"🎭 Roles (Part {i+1}/{len(chunks)})",
            colour=discord.Colour.blurple(),
        )
        for role in chunk:
            if role.members:
                embed.add_field(name=role.name, value=f"Members: **{len(role.members)}** | ID: {role.id}", inline=False)
            else:
                embed.add_field(name=role.name, value=f"ID: {role.id}", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        if i < len(chunks) - 1:
            await interaction.followup.send(content="*More roles...*", ephemeral=True)


@hielkemap_group.command(name="listbans", description="List banned members")
@app_commands.guild_only()
@app_commands.checks.has_permissions(ban_members=True)
async def hielkemap_listbans(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True)
    try:
        bans = asyncio.run_coroutine_threadsafe(interaction.guild.bans(), bot.loop).result()
        if not bans:
            await interaction.followup.send("✅ No banned members.", ephemeral=True)
            return
        chunks = [bans[i:i+20] for i in range(0, len(bans), 20)]
        for i, chunk in enumerate(chunks):
            embed = discord.Embed(
                title=f"🔨 Banned Members (Part {i+1}/{len(chunks)})",
                colour=discord.Colour.red(),
            )
            for ban in chunk:
                embed.add_field(name=str(ban.user), value=f"Reason: {ban.reason or 'Not specified'}", inline=False)
            await interaction.followup.send(embed=embed, ephemeral=True)
    except Exception:
        await interaction.followup.send("❌ Could not fetch ban list.", ephemeral=True)


# --- Configuration Subcommands ---

@hielkemap_group.command(name="modlog", description="Set the moderation log channel")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(channel="The channel for mod logs (omit to disable)")
async def hielkemap_modlog(interaction: discord.Interaction, channel: discord.TextChannel | None = None) -> None:
    if channel:
        HIELKEMAP_CONFIG["mod_log_channel"] = channel.id
        save_hielkemap_config()
        await interaction.response.send_message(f"✅ Mod log set to {channel.mention}.", ephemeral=True)
    else:
        HIELKEMAP_CONFIG.pop("mod_log_channel", None)
        save_hielkemap_config()
        await interaction.response.send_message("✅ Mod log disabled.", ephemeral=True)


@hielkemap_group.command(name="warnthreshold", description="Set the warning threshold for auto-mute")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(threshold="Number of warnings before auto-mute")
async def hielkemap_warnthreshold(interaction: discord.Interaction, threshold: app_commands.Range[int, 1, 10]) -> None:
    HIELKEMAP_CONFIG["warn_threshold"] = threshold
    save_hielkemap_config()
    await interaction.response.send_message(f"✅ Warning threshold set to {threshold}.", ephemeral=True)


@hielkemap_group.command(name="starboard", description="Configure the starboard")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(channel="The starboard channel (omit to disable)", threshold="Number of ⭐ reactions needed")
async def hielkemap_starboard(interaction: discord.Interaction, channel: discord.TextChannel | None = None, threshold: int = 5) -> None:
    if channel:
        HIELKEMAP_CONFIG["starboard_channel"] = channel.id
        HIELKEMAP_CONFIG["starboard_threshold"] = threshold
        save_hielkemap_config()
        await interaction.response.send_message(f"✅ Starboard set to {channel.mention} with threshold {threshold}.", ephemeral=True)
    else:
        HIELKEMAP_CONFIG.pop("starboard_channel", None)
        save_hielkemap_config()
        await interaction.response.send_message("✅ Starboard disabled.", ephemeral=True)


@hielkemap_group.command(name="autorole", description="Configure auto-role for new members")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(role="The role to give new members (omit to disable)")
async def hielkemap_autorole(interaction: discord.Interaction, role: discord.Role | None = None) -> None:
    if role:
        if role.is_default() or role.managed:
            await interaction.response.send_message("❌ Cannot use default or managed roles.", ephemeral=True)
            return
        HIELKEMAP_CONFIG["auto_role"] = role.id
        save_hielkemap_config()
        await interaction.response.send_message(f"✅ Auto-role set to {role.mention}.", ephemeral=True)
    else:
        HIELKEMAP_CONFIG.pop("auto_role", None)
        save_hielkemap_config()
        await interaction.response.send_message("✅ Auto-role disabled.", ephemeral=True)


@hielkemap_group.command(name="welcome", description="Configure welcome messages")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(channel="The channel for welcome messages (omit to disable)", message="Welcome message template")
async def hielkemap_welcome(interaction: discord.Interaction, channel: discord.TextChannel | None = None, message: str = "") -> None:
    if channel:
        HIELKEMAP_CONFIG["welcome_channel"] = channel.id
        if message:
            HIELKEMAP_CONFIG["welcome_message"] = message
        save_hielkemap_config()
        await interaction.response.send_message(f"✅ Welcome configured in {channel.mention}.", ephemeral=True)
    else:
        HIELKEMAP_CONFIG.pop("welcome_channel", None)
        save_hielkemap_config()
        await interaction.response.send_message("✅ Welcome messages disabled.", ephemeral=True)


# --- Fun Subcommands ---

@hielkemap_group.command(name="meme", description="Generate a meme")
@app_commands.describe(top_text="Top text", bottom_text="Bottom text")
async def hielkemap_meme(interaction: discord.Interaction, top_text: str = "", bottom_text: str = "") -> None:
    memes = [
        "https://i.imgur.com/dQw4w9W.png",
        "https://i.imgur.com/6X46s.png",
        "https://i.imgur.com/9E0fA.png",
        "https://i.imgur.com/0v5M1.png",
    ]
    embed = discord.Embed(title="🤣 Meme Generator", colour=discord.Colour.purple())
    embed.set_image(url=random.choice(memes))
    if top_text:
        embed.add_field(name="Top Text", value=top_text)
    if bottom_text:
        embed.add_field(name="Bottom Text", value=bottom_text)
    await interaction.response.send_message(embed=embed)


@hielkemap_group.command(name="joke", description="Tell a random joke")
async def hielkemap_joke(interaction: discord.Interaction) -> None:
    jokes = [
        "Why don't scientists trust atoms? Because they make up everything!",
        "Why did the scarecrow win an award? Because he was outstanding in his field!",
        "What do you call a fake noodle? An impasta!",
        "How do you organize a space party? You planet!",
        "Why did the bicycle fall over? Because it was two-tired!",
        "What's brown and sticky? A stick!",
        "How do you catch a squirrel? Climb a tree and act like a nut!",
        "Why can't you give Elsa from Frozen a balloon? Because she will let it go!",
        "What's the best time to go to the dentist? Tooth-hurty!",
        "Why did the math book look sad? Because it had too many problems!",
    ]
    await interaction.response.send_message(f"😂 **{random.choice(jokes)}**")


@hielkemap_group.command(name="quote", description="Get a random motivational quote")
async def hielkemap_quote(interaction: discord.Interaction) -> None:
    quotes = [
        "The only way to do great work is to love what you do. - Steve Jobs",
        "Life is what happens when you're busy making other plans. - John Lennon",
        "In the middle of every difficulty lies opportunity. - Albert Einstein",
        "Believe you can and you're halfway there. - Theodore Roosevelt",
        "The future belongs to those who believe in the beauty of their dreams. - Eleanor Roosevelt",
        "It does not matter how slowly you go as long as you do not stop. - Confucius",
        "Success is not final, failure is not fatal: It is the courage to continue that counts. - Winston Churchill",
        "You are never too old to set another goal or to dream a new dream. - C.S. Lewis",
    ]
    await interaction.response.send_message(f"💬 *{random.choice(quotes)}*")


@hielkemap_group.command(name="ascii", description="Convert text to ASCII art")
@app_commands.describe(text="Text to convert")
async def hielkemap_ascii(interaction: discord.Interaction, text: str) -> None:
    ascii_art = pyfiglet.figlet_format(text[:20], font="block")
    await interaction.response.send_message(f"```\n{ascii_art}\n```")


@hielkemap_group.command(name="emoji", description="Get a random emoji")
async def hielkemap_emoji(interaction: discord.Interaction) -> None:
    emojis = [
        "😀", "😂", "😍", "😎", "🤔", "🙄", "😇", "😈", "👻", "💀",
        "🎉", "🎊", "🎈", "🎁", "🎮", "🎵", "🎧", "🎬", "📺", "📱",
        "🚀", "🌟", "⭐", "🌈", "🌻", "🌺", "🌵", "🍀", "🌲", "🌳",
        "🍎", "🍕", "🍔", "🍰", "🍩", "🍪", "☕", "🍹", "🍺", "🍻",
        "⚽", "🏀", "🏈", "⚾", "🎾", "🏐", "🏉", "🎱", "🏓", "🏸",
    ]
    await interaction.response.send_message(f"{random.choice(emojis)} {random.choice(emojis)} {random.choice(emojis)}")


@hielkemap_group.command(name="dice", description="Roll multiple dice")
@app_commands.describe(dice="Dice format (e.g., 2d6, 1d20)")
async def hielkemap_dice(interaction: discord.Interaction, dice: str = "1d6") -> None:
    try:
        count, sides = map(int, dice.lower().split("d"))
        count = min(count, 10)
        sides = min(sides, 100)
        rolls = [random.randint(1, sides) for _ in range(count)]
        total = sum(rolls)
        await interaction.response.send_message(
            f"🎲 Rolling {count}d{sides}: {', '.join(map(str, rolls))} = **{total}**"
        )
    except (ValueError, AttributeError):
        await interaction.response.send_message("❌ Use format like 2d6 or 1d20.", ephemeral=True)


@hielkemap_group.command(name="flip", description="Flip a coin")
async def hielkemap_flip(interaction: discord.Interaction) -> None:
    result = random.choice(["Heads", "Tails"])
    await interaction.response.send_message(f"🪙 **{result}!**")


@hielkemap_group.command(name="rps", description="Play Rock Paper Scissors")
@app_commands.choices(choice=[
    app_commands.Choice(name="Rock", value="rock"),
    app_commands.Choice(name="Paper", value="paper"),
    app_commands.Choice(name="Scissors", value="scissors"),
])
async def hielkemap_rps(interaction: discord.Interaction, choice: app_commands.Choice[str]) -> None:
    choices = ["rock", "paper", "scissors"]
    bot_choice = random.choice(choices)
    winner = "You" if choice.value == bot_choice else "Bot"
    if choice.value != bot_choice:
        if (choice.value == "rock" and bot_choice == "scissors") or \
           (choice.value == "paper" and bot_choice == "rock") or \
           (choice.value == "scissors" and bot_choice == "paper"):
            winner = "You"
        else:
            winner = "Bot"
    else:
        winner = "Tie"
    emojis = {"rock": "🪨", "paper": "📄", "scissors": "✂️"}
    await interaction.response.send_message(
        f"🤖 **{winner} win!**\nYou: {emojis[choice.value]} | Bot: {emojis[bot_choice]}"
    )


# --- Utility Subcommands ---

@hielkemap_group.command(name="ping", description="Check bot latency")
async def hielkemap_ping(interaction: discord.Interaction) -> None:
    latency = bot.latency * 1000
    await interaction.response.send_message(f"🏓 Pong! **{latency:.0f} ms**")


@hielkemap_group.command(name="userinfo", description="Get detailed user information")
@app_commands.describe(member="The member to check")
async def hielkemap_userinfo(interaction: discord.Interaction, member: discord.Member | None = None) -> None:
    member = member or interaction.user
    embed = discord.Embed(title=f"👤 {member.display_name}", colour=member.colour)
    embed.add_field(name="🆔 ID", value=str(member.id))
    embed.add_field(name="👑 Nickname", value=member.nick or "None")
    embed.add_field(name="📅 Joined Server", value=format_uk_datetime(member.joined_at) if member.joined_at else "Unknown")
    embed.add_field(name="🌍 Account Created", value=format_uk_datetime(member.created_at))
    embed.add_field(name="🤖 Bot", value="Yes" if member.bot else "No")
    if member.roles[1:]:
        roles = ", ".join(role.mention for role in member.roles[1:])
        embed.add_field(name="🎭 Roles", value=roles[:1024], inline=False)
    embed.set_thumbnail(url=member.display_avatar.url)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@hielkemap_group.command(name="avatar", description="Get a user's avatar")
@app_commands.describe(member="The member to check")
async def hielkemap_avatar(interaction: discord.Interaction, member: discord.Member | None = None) -> None:
    member = member or interaction.user
    embed = discord.Embed(title=f"🖼️ {member.display_name}'s Avatar", colour=member.colour)
    embed.set_image(url=member.display_avatar.url)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@hielkemap_group.command(name="banner", description="Get a user's banner")
@app_commands.describe(member="The member to check")
async def hielkemap_banner(interaction: discord.Interaction, member: discord.Member | None = None) -> None:
    member = member or interaction.user
    if not member.banner:
        await interaction.response.send_message("❌ This user has no banner.", ephemeral=True)
        return
    embed = discord.Embed(title=f"🎨 {member.display_name}'s Banner", colour=member.colour)
    embed.set_image(url=member.banner.url)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@hielkemap_group.command(name="servericon", description="Get the server icon")
@app_commands.guild_only()
async def hielkemap_servericon(interaction: discord.Interaction) -> None:
    if not interaction.guild.icon:
        await interaction.response.send_message("❌ This server has no icon.", ephemeral=True)
        return
    embed = discord.Embed(title=f"🏢 {interaction.guild.name}'s Icon", colour=discord.Colour.blurple())
    embed.set_image(url=interaction.guild.icon.url)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@hielkemap_group.command(name="calc", description="Calculate a math expression")
@app_commands.describe(expression="Math expression (e.g., 2+2*3)")
async def hielkemap_calc(interaction: discord.Interaction, expression: str) -> None:
    try:
        result = eval(expression, {"__builtins__": None}, {"math": __import__("math")})
        await interaction.response.send_message(f"🧮 **{expression} = {result}**")
    except Exception:
        await interaction.response.send_message("❌ Invalid expression.", ephemeral=True)


@hielkemap_group.command(name="poll", description="Create a poll with multiple options")
@app_commands.describe(question="The poll question", options="Comma-separated options")
async def hielkemap_poll(interaction: discord.Interaction, question: str, options: str) -> None:
    option_list = [opt.strip() for opt in options.split(",") if opt.strip()]
    if len(option_list) < 2:
        await interaction.response.send_message("❌ Need at least 2 options.", ephemeral=True)
        return
    if len(option_list) > 10:
        await interaction.response.send_message("❌ Maximum 10 options.", ephemeral=True)
        return
    embed = discord.Embed(title="📊 Poll", description=question, colour=discord.Colour.blurple())
    embed.set_footer(text=f"Poll by {interaction.user.display_name}")
    view = discord.ui.View()
    for i, option in enumerate(option_list):
        view.add_item(discord.ui.Button(
            label=option[:80],
            style=discord.ButtonStyle.primary,
            custom_id=f"poll_option_{i}",
        ))
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message("✅ Poll created!", ephemeral=True)


@hielkemap_group.command(name="remindme", description="Set a personal reminder")
@app_commands.describe(time="Time (e.g., 5m, 1h, 2d)", message="Reminder message")
async def hielkemap_remindme(interaction: discord.Interaction, time: str, message: str) -> None:
    import asyncio
    time_units = {
        "s": 1,
        "m": 60,
        "h": 3600,
        "d": 86400,
    }
    try:
        unit = time[-1].lower()
        if unit not in time_units:
            raise ValueError
        seconds = int(time[:-1]) * time_units[unit]
        if seconds < 10 or seconds > 86400 * 7:
            raise ValueError
    except (ValueError, IndexError):
        await interaction.response.send_message(
            "❌ Use format like 5m, 1h, or 2d (10s to 7d).", ephemeral=True
        )
        return
    await interaction.response.send_message(
        f"⏰ Reminder set! I'll remind you in **{time}** with: {message}", ephemeral=True
    )
    await asyncio.sleep(seconds)
    if isinstance(interaction.channel, discord.TextChannel):
        await interaction.channel.send(
            f"{interaction.user.mention} ⏰ **REMINDER:** {message}"
        )


@hielkemap_group.command(name="notes", description="Add or view notes for a user")
@app_commands.guild_only()
@app_commands.checks.has_permissions(moderate_members=True)
@app_commands.describe(member="The member to add notes for", note="Note to add (omit to view notes)")
async def hielkemap_notes(interaction: discord.Interaction, member: discord.Member, note: str = "") -> None:
    user_notes = HIELKEMAP_CONFIG.setdefault("notes", {}).setdefault(str(member.id), [])
    if note:
        user_notes.append({
            "moderator_id": interaction.user.id,
            "note": note,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        save_hielkemap_config()
        await interaction.response.send_message(f"✅ Note added for **{member}**.", ephemeral=True)
    else:
        if not user_notes:
            await interaction.response.send_message(f"✅ **{member}** has no notes.", ephemeral=True)
            return
        embed = discord.Embed(
            title=f"📝 Notes for {member.display_name}",
            colour=discord.Colour.blue(),
        )
        for n in user_notes:
            mod = interaction.guild.get_member(n["moderator_id"])
            mod_name = mod.display_name if mod else f"Unknown ({n['moderator_id']})"
            embed.add_field(
                name=f"Note from {mod_name}",
                value=f"{n['note']}\n{n['timestamp'][:19]}",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)


@hielkemap_group.command(name="clearnotes", description="Clear notes for a user")
@app_commands.guild_only()
@app_commands.checks.has_permissions(moderate_members=True)
@app_commands.describe(member="The member to clear notes for")
async def hielkemap_clearnotes(interaction: discord.Interaction, member: discord.Member) -> None:
    user_notes = HIELKEMAP_CONFIG.setdefault("notes", {}).pop(str(member.id), [])
    save_hielkemap_config()
    await interaction.response.send_message(f"✅ Cleared {len(user_notes)} notes for **{member}**.", ephemeral=True)


# --- Custom Commands ---

@hielkemap_group.command(name="customcmd-add", description="Add a custom command")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(name="Command name", response="Response text")
async def hielkemap_customcmd_add(interaction: discord.Interaction, name: str, response: str) -> None:
    custom_commands = HIELKEMAP_CONFIG.setdefault("custom_commands", {})
    name = name.lower().strip()
    if name in custom_commands:
        await interaction.response.send_message(f"❌ Command **{name}** already exists.", ephemeral=True)
        return
    custom_commands[name] = response
    save_hielkemap_config()
    await interaction.response.send_message(f"✅ Custom command **{name}** added.", ephemeral=True)


@hielkemap_group.command(name="customcmd-remove", description="Remove a custom command")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(name="Command name")
async def hielkemap_customcmd_remove(interaction: discord.Interaction, name: str) -> None:
    custom_commands = HIELKEMAP_CONFIG.setdefault("custom_commands", {})
    name = name.lower().strip()
    if name not in custom_commands:
        await interaction.response.send_message(f"❌ Command **{name}** not found.", ephemeral=True)
        return
    del custom_commands[name]
    save_hielkemap_config()
    await interaction.response.send_message(f"✅ Custom command **{name}** removed.", ephemeral=True)


@hielkemap_group.command(name="customcmd-list", description="List all custom commands")
@app_commands.guild_only()
async def hielkemap_customcmd_list(interaction: discord.Interaction) -> None:
    custom_commands = HIELKEMAP_CONFIG.get("custom_commands", {})
    if not custom_commands:
        await interaction.response.send_message("✅ No custom commands configured.", ephemeral=True)
        return
    embed = discord.Embed(title="⚙️ Custom Commands", colour=discord.Colour.purple())
    for name, response in list(custom_commands.items())[:25]:
        embed.add_field(name=f"/{name}", value=response[:1024], inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="custom")
async def custom_command(interaction: discord.Interaction, name: str) -> None:
    """Execute a custom command."""
    custom_commands = HIELKEMAP_CONFIG.get("custom_commands", {})
    name = name.lower().strip()
    if name not in custom_commands:
        await interaction.response.send_message(f"❌ Custom command **{name}** not found.", ephemeral=True)
        return
    response = custom_commands[name]
    await interaction.response.send_message(response)


# --- Help Command ---

@hielkemap_group.command(name="help", description="Show all HielkeMap commands")
async def hielkemap_help(interaction: discord.Interaction) -> None:
    embed = discord.Embed(
        title="🏆 HielkeMap Admin/Mod Suite",
        description="The ultimate Discord bot command suite with **everything** you need!",
        colour=discord.Colour.gold(),
    )
    
    # Moderation
    embed.add_field(
        name="🛡️ Moderation",
        value="`/hielkemap ban` `/hielkemap unban` `/hielkemap kick` `/hielkemap warn` `/hielkemap warns` `/hielkemap clearwarns` `/hielkemap mute` `/hielkemap unmute` `/hielkemap timeout` `/hielkemap untimeout`",
        inline=False,
    )
    
    # Admin
    embed.add_field(
        name="👑 Admin",
        value="`/hielkemap addrole` `/hielkemap removerole` `/hielkemap createrole` `/hielkemap deleterole` `/hielkemap createchannel` `/hielkemap deletechannel` `/hielkemap slowmode` `/hielkemap lock` `/hielkemap unlock` `/hielkemap nickname` `/hielkemap purge`",
        inline=False,
    )
    
    # Server Management
    embed.add_field(
        name="📊 Server Management",
        value="`/hielkemap serverinfo` `/hielkemap membercount` `/hielkemap listroles` `/hielkemap listbans`",
        inline=False,
    )
    
    # Configuration
    embed.add_field(
        name="⚙️ Configuration",
        value="`/hielkemap modlog` `/hielkemap warnthreshold` `/hielkemap starboard` `/hielkemap autorole` `/hielkemap welcome`",
        inline=False,
    )
    
    # Fun
    embed.add_field(
        name="🎉 Fun",
        value="`/hielkemap meme` `/hielkemap joke` `/hielkemap quote` `/hielkemap ascii` `/hielkemap emoji` `/hielkemap dice` `/hielkemap flip` `/hielkemap rps`",
        inline=False,
    )
    
    # Utility
    embed.add_field(
        name="🔧 Utility",
        value="`/hielkemap ping` `/hielkemap userinfo` `/hielkemap avatar` `/hielkemap banner` `/hielkemap servericon` `/hielkemap calc` `/hielkemap poll` `/hielkemap remindme`",
        inline=False,
    )
    
    # Mod Tools
    embed.add_field(
        name="📋 Mod Tools",
        value="`/hielkemap notes` `/hielkemap clearnotes` `/hielkemap customcmd-add` `/hielkemap customcmd-remove` `/hielkemap customcmd-list`",
        inline=False,
    )
    
    embed.set_footer(text="Type /hielkemap help for this menu. Use /custom <name> for custom commands.")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# --- AI Commands ---

@bot.tree.command(name="ai", description="Interact with AI - ask anything, generate text, images, and more!")
@app_commands.describe(prompt="Your AI prompt", model="AI model to use")
@app_commands.choices(model=[
    app_commands.Choice(name="GPT-4", value="gpt4"),
    app_commands.Choice(name="GPT-3.5", value="gpt35"),
    app_commands.Choice(name="Claude", value="claude"),
    app_commands.Choice(name="Mistral", value="mistral"),
    app_commands.Choice(name="Random", value="random"),
])
async def ai_command(interaction: discord.Interaction, prompt: str, model: app_commands.Choice[str] | None = None) -> None:
    """Simulate AI responses with fun personality."""
    await interaction.response.defer(thinking=True)
    
    # Fun AI personalities
    personalities = {
        "gpt4": {"name": "GPT-4", "style": "intelligent", "emoji": "🧠"},
        "gpt35": {"name": "GPT-3.5", "style": "helpful", "emoji": "💡"},
        "claude": {"name": "Claude", "style": "friendly", "emoji": "🤖"},
        "mistral": {"name": "Mistral", "style": "witty", "emoji": "🌪️"},
    }
    
    chosen_model = model.value if model else "random"
    if chosen_model == "random":
        chosen_model = random.choice(["gpt4", "gpt35", "claude", "mistral"])
    
    personality = personalities[chosen_model]
    
    # Generate a fun AI response
    responses = [
        f"{personality['emoji']} **{personality['name']}:** I've analyzed your query '{prompt}' using my advanced neural networks. The answer is: **{random.choice(['Yes', 'No', 'Maybe', '42', 'Try again later', 'Absolutely!', 'No way!', 'Interesting question...'])}**",
        f"{personality['emoji']} **{personality['name']}:** After processing {len(prompt)} characters and consulting my {random.randint(100, 999)} terabytes of data... **{random.choice(['You are correct', 'That is false', 'The answer is complex', 'I need more context'])}**",
        f"{personality['emoji']} **{personality['name']}:** *typing... thinking... calculating...* **{random.choice(['Done!', 'Here you go!', 'Let me think...', 'Processing...'])}** {prompt[:50]}...",
        f"{personality['emoji']} **{personality['name']}:** I'm a {personality['style']} AI assistant. Your prompt '{prompt[:30]}...' has been received. My response: **{random.choice(['Affirmative', 'Negative', 'Perhaps', 'Definitely', 'Never'])}**",
        f"{personality['emoji']} **{personality['name']}:** *beep boop* Analyzing '{prompt}'... **Result: {random.randint(0, 100)}% confidence in my answer!**",
        f"{personality['emoji']} **{personality['name']}:** As a {personality['style']} AI, I can tell you that '{prompt}' is {random.choice(['fascinating', 'interesting', 'confusing', 'amazing', 'weird'])}. My verdict: **{random.choice(['✅', '❌', '⚠️', '🎉', '🤯'])}**",
        f"{personality['emoji']} **{personality['name']}:** Query processed in {random.uniform(0.1, 2.5):.2f} seconds. Response: **{prompt.upper() if random.random() > 0.5 else prompt.lower()}**",
        f"{personality['emoji']} **{personality['name']}:** I've consulted my {random.randint(1, 10)} trillion parameters. The definitive answer to '{prompt[:20]}...' is: **{random.choice(['YES', 'NO', 'MAYBE', 'IDK', 'LOL'])}**",
    ]
    
    response = random.choice(responses)
    
    # Sometimes add extra flair
    if random.random() > 0.7:
        response += f"\n\n*Fun fact: This response was generated using {random.choice(['quantum computing', 'neural lace', 'AI magic', 'pure Python', 'discord.py'])}*"
    
    if random.random() > 0.8:
        response += f"\n*Tokens used: {random.randint(10, 500)} | Temperature: {random.uniform(0.1, 1.0):.2f}*"
    
    embed = discord.Embed(
        title=f"{personality['emoji']} {personality['name']} AI Response",
        description=response,
        colour=discord.Colour.purple(),
    )
    embed.add_field(name="Model", value=personality['name'])
    embed.add_field(name="Style", value=personality['style'])
    embed.add_field(name="Prompt", value=prompt[:100] + "..." if len(prompt) > 100 else prompt)
    embed.set_footer(text="⚡ Powered by HielkeMap AI Suite")
    
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="aichat", description="Start an AI chat session")
@app_commands.describe(message="Your message to start the chat")
async def aichat_command(interaction: discord.Interaction, message: str) -> None:
    """Simulate an AI chat conversation."""
    await interaction.response.defer(thinking=True)
    
    ai_names = ["HielkeBot", "AI Assistant", "ChatGPT", "Claude", "Mistral", "Jarvis", "Friday", "HAL 9000"]
    ai_name = random.choice(ai_names)
    
    greetings = [
        f"Hello! I'm {ai_name}, your AI assistant. You said: '{message}'. My response: **{random.choice(['How can I help?', 'Interesting!', 'Tell me more!', 'I see.', 'Go on...'])}**",
        f"Hi there! {ai_name} here. I've received your message: '{message}'. **{random.choice(['What would you like to know?', 'How may I assist?', 'What can I do for you?'])}**",
        f"Greetings human! {ai_name} at your service. Your input: '{message}'. **{random.choice(['Processing...', 'Understood.', 'Acknowledged.'])}**",
        f"🤖 {ai_name} activated! You: '{message}'. Me: **{random.choice(['Yes?', 'No?', 'Maybe?', 'I need to think...'])}**",
    ]
    
    response = random.choice(greetings)
    
    embed = discord.Embed(
        title=f"💬 AI Chat with {ai_name}",
        description=response,
        colour=discord.Colour.blue(),
    )
    embed.add_field(name="Your Message", value=message[:200])
    embed.add_field(name="AI Status", value=random.choice(["Online ✅", "Thinking 🤔", "Processing ⚙️", "Ready 🚀"]))
    embed.set_footer(text="Type /ai for single prompts | /aichat for conversations")
    
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="aigen", description="Generate AI content (text, code, stories, etc.)")
@app_commands.describe(content_type="Type of content to generate", prompt="Your generation prompt")
@app_commands.choices(content_type=[
    app_commands.Choice(name="Text", value="text"),
    app_commands.Choice(name="Code", value="code"),
    app_commands.Choice(name="Story", value="story"),
    app_commands.Choice(name="Poem", value="poem"),
    app_commands.Choice(name="Joke", value="joke"),
    app_commands.Choice(name="Song", value="song"),
    app_commands.Choice(name="Essay", value="essay"),
])
async def aigen_command(interaction: discord.Interaction, content_type: app_commands.Choice[str], prompt: str) -> None:
    """Generate different types of AI content."""
    await interaction.response.defer(thinking=True)
    
    type_display = {
        "text": "📝 Text",
        "code": "💻 Code",
        "story": "📖 Story",
        "poem": "✍️ Poem",
        "joke": "😂 Joke",
        "song": "🎵 Song",
        "essay": "📄 Essay",
    }
    
    generators = {
        "text": lambda p: f"Here is your generated text about '{p}':\n\n{random.choice(['This is some text.', 'Interesting content here.', 'AI generated text based on your prompt.', 'The quick brown fox jumps over the lazy dog.'])} \n\n{random.choice(['More text here.', 'Additional paragraphs.', 'Detailed explanation.'])} \n\n**Total words: {random.randint(50, 500)}**",
        "code": lambda p: f"```python\n# Generated code for: {p}\n{random.choice(['def hello():', 'class AI:', 'import random', 'for i in range(10):'])} \n    {random.choice(['print("Hello World")', 'return 42', 'pass', '...'])} \n```\n*Lines of code: {random.randint(5, 100)} | Language: Python*",
        "story": lambda p: f"**Once upon a time...**\n\n{random.choice(['In a galaxy far away', 'There was a hero named', 'A magical adventure began', 'The story of'])} {p}. \n\n{random.choice(['They faced many challenges.', 'The journey was long and hard.', 'Adventure awaited at every turn.', 'Magic filled the air.'])} \n\n**The End.** 📖",
        "poem": lambda p: f"**Roses are red**\nViolets are blue\n{p} is great\nAnd so are you! 💖",
        "joke": lambda p: f"Why did {p} cross the road?\nTo get to the other side! 😂",
        "song": lambda p: f"🎶 *{p} by AI* 🎶\n\n{random.choice(['Verse 1: La la la', 'Chorus: Na na na', 'Bridge: Oh oh oh'])} \n{random.choice(['Verse 2: Be be be', 'Chorus: Na na na', 'Outro: The end'])} \n\n*Duration: {random.randint(1, 5)}:{random.randint(0, 59):02d}*",
        "essay": lambda p: f"**Essay: {p}**\n\nIntroduction: {random.choice(['In this essay, we will explore', 'The topic of', 'Understanding'])} {p}. \n\nBody: {random.choice(['Firstly,', 'Moreover,', 'Additionally,'])} {random.choice(['this is important', 'we must consider', 'the evidence shows'])}. \n\nConclusion: {random.choice(['In conclusion,', 'To summarize,', 'Finally,'])} {p} is {random.choice(['fascinating', 'important', 'complex', 'interesting'])}. \n\n**Word count: {random.randint(200, 2000)}**",
    }
    
    generated = generators[content_type.value](prompt)
    
    embed = discord.Embed(
        title=f"{type_display[content_type.value]} Generation",
        description=generated,
        colour=discord.Colour.magenta(),
    )
    embed.add_field(name="Prompt", value=prompt[:100])
    embed.add_field(name="Type", value=content_type.name)
    embed.set_footer(text="✨ Generated by HielkeMap AI")
    
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="aiimage", description="Generate an AI image from a prompt")
@app_commands.describe(prompt="Image description", style="Art style")
@app_commands.choices(style=[
    app_commands.Choice(name="Realistic", value="realistic"),
    app_commands.Choice(name="Anime", value="anime"),
    app_commands.Choice(name="Cartoon", value="cartoon"),
    app_commands.Choice(name="Fantasy", value="fantasy"),
    app_commands.Choice(name="Cyberpunk", value="cyberpunk"),
    app_commands.Choice(name="Watercolor", value="watercolor"),
    app_commands.Choice(name="Pixel Art", value="pixel"),
])
async def aiimage_command(interaction: discord.Interaction, prompt: str, style: app_commands.Choice[str] | None = None) -> None:
    """Simulate AI image generation."""
    await interaction.response.defer(thinking=True)
    
    styles = {
        "realistic": "📸 Realistic Photography",
        "anime": "🎌 Anime Style",
        "cartoon": "🎨 Cartoon",
        "fantasy": "✨ Fantasy Art",
        "cyberpunk": "🤖 Cyberpunk",
        "watercolor": "🎨 Watercolor Painting",
        "pixel": "🎮 Pixel Art",
    }
    
    chosen_style = style.value if style else random.choice(list(styles.keys()))
    
    # Generate a fake image URL (placeholder)
    fake_images = [
        "https://via.placeholder.com/512x512/000000/FFFFFF?text=AI+Generated+Image",
        "https://via.placeholder.com/512x512/FF0000/FFFFFF?text=Your+AI+Art",
        "https://via.placeholder.com/512x512/00FF00/000000?text=Generated+Pic",
        "https://via.placeholder.com/512x512/0000FF/FFFFFF?text=AI+Creation",
        "https://via.placeholder.com/512x512/FFFF00/000000?text=Art+By+AI",
    ]
    
    image_url = random.choice(fake_images)
    
    embed = discord.Embed(
        title=f"🎨 AI Image Generation",
        description=f"**Prompt:** {prompt}\n**Style:** {styles[chosen_style]}\n\n*Generating your image... ⏳*",
        colour=discord.Colour.dark_blue(),
    )
    embed.set_image(url=image_url)
    embed.add_field(name="Resolution", value=f"{random.choice(['512x512', '768x768', '1024x1024', '1920x1080'])}")
    embed.add_field(name="Steps", value=str(random.randint(20, 100)))
    embed.add_field(name="Guidance", value=f"{random.uniform(1.0, 20.0):.1f}")
    embed.set_footer(text="⚡ Image generated by HielkeMap AI | This is a simulation")
    
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="aitranslate", description="Translate text using AI")
@app_commands.describe(text="Text to translate", source_lang="Source language", target_lang="Target language")
async def aitranslate_command(interaction: discord.Interaction, text: str, source_lang: str = "auto", target_lang: str = "en") -> None:
    """Simulate AI translation."""
    await interaction.response.defer(thinking=True)
    
    languages = {
        "en": "English",
        "es": "Spanish",
        "fr": "French",
        "de": "German",
        "it": "Italian",
        "ja": "Japanese",
        "zh": "Chinese",
        "ru": "Russian",
        "pt": "Portuguese",
        "ar": "Arabic",
        "hi": "Hindi",
        "auto": "Auto-detect",
    }
    
    source = languages.get(source_lang, source_lang)
    target = languages.get(target_lang, target_lang)
    
    # Simulate translation
    translated = text
    if random.random() > 0.5:
        # Sometimes "translate" to something funny
        translated = f"[{target} translation] {text[::-1]}"  # Reverse text
    else:
        translated = f"[{target}] {text.upper() if random.random() > 0.5 else text.lower()}"
    
    embed = discord.Embed(
        title="🌍 AI Translation",
        description=f"**Original:** {text}\n\n**Translated:** {translated}",
        colour=discord.Colour.green(),
    )
    embed.add_field(name="From", value=source)
    embed.add_field(name="To", value=target)
    embed.add_field(name="Confidence", value=f"{random.uniform(0.5, 1.0)*100:.1f}%")
    embed.set_footer(text="✨ HielkeMap AI Translator")
    
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="aicode", description="Generate AI-powered code")
@app_commands.describe(language="Programming language", task="What code to generate")
@app_commands.choices(language=[
    app_commands.Choice(name="Python", value="python"),
    app_commands.Choice(name="JavaScript", value="javascript"),
    app_commands.Choice(name="Java", value="java"),
    app_commands.Choice(name="C++", value="cpp"),
    app_commands.Choice(name="C#", value="csharp"),
    app_commands.Choice(name="Go", value="go"),
    app_commands.Choice(name="Rust", value="rust"),
    app_commands.Choice(name="PHP", value="php"),
    app_commands.Choice(name="Ruby", value="ruby"),
    app_commands.Choice(name="Swift", value="swift"),
])
async def aicode_command(interaction: discord.Interaction, language: app_commands.Choice[str], task: str) -> None:
    """Generate AI code snippets."""
    await interaction.response.defer(thinking=True)
    
    py_lines = ["print('Hello')", "return 42", "pass", "..."]
    js_lines = ["console.log('Hello');", "return 42;", "// TODO"]
    java_lines = ["System.out.println('Hello');", "int x = 42;", "// Code here"]
    cpp_lines = ["cout << 'Hello';", "int x = 42;", "return 0;"]
    cs_lines = ["Console.WriteLine('Hello');", "int x = 42;"]
    go_lines = ["fmt.Println('Hello')", "x := 42"]
    rust_lines = ["println!('Hello');", "let x = 42;"]
    php_lines = ["echo 'Hello';", "$x = 42;"]
    ruby_lines = ["puts 'Hello'", "x = 42"]
    swift_lines = ["print('Hello')", "let x = 42"]
    
    py_imports = ["import random", "import sys", "from datetime import datetime", "import os"]
    py_starts = ["def main():", "class Solution:", "if __name__ == '__main__':"]
    py_ends = ["print('Done')", "return result", ""]
    js_starts = ["const x = 5;", "function main() {", "let result = 42;"]
    js_ends = ["}", "export default x;", ""]
    
    code_templates = {
        "python": f"```python\n# {task}\n{random.choice(py_imports)} \n\n{random.choice(py_starts)} \n    {random.choice(py_lines)} \n\n{random.choice(py_ends)} \n```",
        "javascript": f"```javascript\n// {task}\n{random.choice(js_starts)} \n{random.choice(js_lines)} \n{random.choice(js_ends)} \n```",
        "java": f"```java\n// {task}\npublic class Main {{\n    public static void main(String[] args) {{\n        {random.choice(java_lines)} \n    }}\n}}\n```",
        "cpp": f"```cpp\n// {task}\n#include <iostream>\nusing namespace std;\n\nint main() {{\n    {random.choice(cpp_lines)} \n    return 0;\n}}\n```",
        "csharp": f"```csharp\n// {task}\nusing System;\n\nclass Program {{\n    static void Main() {{\n        {random.choice(cs_lines)} \n    }}\n}}\n```",
        "go": f"```go\n// {task}\npackage main\n\nimport \"fmt\"\n\nfunc main() {{\n    {random.choice(go_lines)} \n}}\n```",
        "rust": f"```rust\n// {task}\nfn main() {{\n    {random.choice(rust_lines)} \n}}\n```",
        "php": f"```php\n<?php\n// {task}\n{random.choice(php_lines)} \n?>\n```",
        "ruby": f"```ruby\n# {task}\n{random.choice(ruby_lines)} \n```",
        "swift": f"```swift\n// {task}\nimport Swift\n\n{random.choice(swift_lines)} \n```",
    }
    
    code = code_templates.get(language.value, code_templates["python"])
    
    embed = discord.Embed(
        title=f"💻 AI Code Generator - {language.name}",
        description=f"**Task:** {task}",
        colour=discord.Colour.dark_green(),
    )
    embed.add_field(name="Code", value=code, inline=False)
    embed.add_field(name="Lines", value=str(random.randint(5, 50)))
    embed.add_field(name="Complexity", value=random.choice(["Low", "Medium", "High", "Very High"]))
    embed.set_footer(text="✨ Generated by HielkeMap AI")
    
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="aichar", description="Create an AI character/chatbot")
@app_commands.describe(name="Character name", personality="Character personality")
async def aichar_command(interaction: discord.Interaction, name: str, personality: str = "friendly") -> None:
    """Create a custom AI character."""
    await interaction.response.defer(thinking=True)
    
    personalities = {
        "friendly": "😊 Always happy and helpful",
        "angry": "😠 Grumpy and sarcastic",
        "sad": "😢 Melancholic and poetic",
        "robot": "🤖 Logical and mechanical",
        "villain": "😈 Evil and scheming",
        "hero": "🦸‍♂️ Brave and noble",
        "funny": "😂 Always joking",
        "smart": "🧠 Intellectual and wise",
    }
    
    chosen_personality = personalities.get(personality, personalities["friendly"])
    
    # Generate character card
    traits = [
        f"**Name:** {name}",
        f"**Personality:** {chosen_personality}",
        f"**IQ:** {random.randint(50, 200)}",
        f"**Mood:** {random.choice(['Happy', 'Sad', 'Angry', 'Chill', 'Excited'])}",
        f"**Catchphrase:** {random.choice(['Hello!', 'What do you want?', 'Leave me alone!', 'I am here to help', 'Resistance is futile'])} ",
        f"**Favorite Color:** {random.choice(['Red', 'Blue', 'Green', 'Purple', 'Gold'])}",
    ]
    
    embed = discord.Embed(
        title=f"🎭 AI Character: {name}",
        description="\n".join(traits),
        colour=discord.Colour.random(),
    )
    embed.set_thumbnail(url="https://via.placeholder.com/128x128/FF00FF/FFFFFF?text=AI")
    embed.set_footer(text="✨ Created by HielkeMap AI Character Creator")
    
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="aistats", description="Get AI usage statistics")
async def aistats_command(interaction: discord.Interaction) -> None:
    """Show fake AI usage statistics."""
    await interaction.response.defer(thinking=True)
    
    embed = discord.Embed(
        title="📊 HielkeMap AI Statistics",
        description="Your AI usage analytics",
        colour=discord.Colour.blue(),
    )
    
    embed.add_field(name="Total Prompts", value=f"{random.randint(100, 10000):,}")
    embed.add_field(name="Total Tokens", value=f"{random.randint(1000, 100000):,}")
    embed.add_field(name="Total Images", value=f"{random.randint(10, 1000)}")
    embed.add_field(name="Total Translations", value=f"{random.randint(50, 5000)}")
    embed.add_field(name="Total Code Generated", value=f"{random.randint(100, 5000)} lines")
    embed.add_field(name="Most Used Model", value=random.choice(["GPT-4", "Claude", "Mistral", "GPT-3.5"]))
    embed.add_field(name="Average Response Time", value=f"{random.uniform(0.5, 5.0):.2f}s")
    embed.add_field(name="Accuracy", value=f"{random.uniform(85.0, 99.9):.1f}%")
    
    embed.set_footer(text="⚡ Statistics generated by HielkeMap AI")
    
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="aihelp", description="Show all AI commands")
async def aihelp_command(interaction: discord.Interaction) -> None:
    """Show AI command help."""
    embed = discord.Embed(
        title="🤖 HielkeMap AI Commands",
        description="All AI-powered commands available in the bot",
        colour=discord.Colour.purple(),
    )
    
    embed.add_field(
        name="💬 AI Chat",
        value="`/ai` - Single AI prompt\n`/aichat` - Start AI chat session\n`/aichar` - Create AI character",
        inline=False,
    )
    
    embed.add_field(
        name="✨ AI Generation",
        value="`/aigen` - Generate text/code/stories\n`/aicode` - Generate code snippets\n`/aiimage` - Generate AI images",
        inline=False,
    )
    
    embed.add_field(
        name="🌍 AI Tools",
        value="`/aitranslate` - AI translation\n`/aistats` - AI usage statistics\n`/aihelp` - This help menu",
        inline=False,
    )
    
    embed.set_footer(text="✨ Powered by HielkeMap AI Suite | All commands are simulated for fun!")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)


# --- Add the command group to the bot ---
bot.tree.add_command(hielkemap_group)


bot.run(DISCORD_TOKEN)


@bot.tree.command(name="aitierlist", description="Show AI tier list ranking")
async def aitierlist_command(interaction: discord.Interaction) -> None:
    """Show a fun AI tier list."""
    await interaction.response.defer(thinking=True)
    
    embed = discord.Embed(
        title="\ud83c\udfaf AI TIER LIST \ud83c\udfaf",
        description="Ranking of AI models from best to worst (opinion based)",
        colour=discord.Colour.gold(),
    )
    
    embed.add_field(name="\u2728 S+ TIER \u2728", value="`GPT-5` `Claude 4` `Mistral Large 2` `Gemini 2.0 Ultra`", inline=False)
    embed.add_field(name="\u2604 S TIER \u2604", value="`GPT-4` `Claude 3 Opus` `Llama 3 70B` `Gemini 1.5 Pro` `Mistral Large`", inline=False)
    embed.add_field(name="\ud83c\udf1f A TIER \ud83c\udf1f", value="`GPT-4 Turbo` `Claude 3 Sonnet` `Llama 3 8B` `Gemini 1.5 Flash` `Mixtral 8x7B` `Qwen 2 72B`", inline=False)
    embed.add_field(name="\ud83c\udf1e B TIER \ud83c\udf1e", value="`GPT-3.5 Turbo` `Claude 3 Haiku` `Llama 2 70B` `Gemini 1.0 Pro` `Phi-3 14B` `Cohere Command R+`", inline=False)
    embed.add_field(name="\u26a0 C TIER \u26a0", value="`GPT-3.5` `Llama 2 13B` `Gemini 1.0` `Mistral 7B` `Phi-3 7B` `Qwen 2 14B`", inline=False)
    embed.add_field(name="\ud83d\udca9 D TIER \ud83d\udca9", value="`Bing Chat` `Bard` `Perplexity 7B` `StableLM 2` `TinyLlama`", inline=False)
    embed.add_field(name="\ud83d\udd14 F TIER \ud83d\udd14", value="`AI Dungeon` `Character.AI` `Replika` `Anima` `Your ex's brain`", inline=False)
    embed.add_field(name="\ud83e\udd80 MEME TIER \ud83e\udd80", value="`AI that says 'I am an AI and cannot'` `AI that hallucinates URLs` `AI that refuses to help`", inline=False)
    embed.add_field(name="\ud83d\udc80 LEGENDARY TIER \ud83d\udc80", value="`HielkeMap AI` `Your imagination` `The AI from the future`", inline=False)
    embed.set_footer(text="\u2728 Tier list by HielkeMap | All opinions are subjective | Made for fun")
    await interaction.followup.send(embed=embed)

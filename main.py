"""Status Notifier's main module"""

import logging
from asyncio import create_task
from os import getenv
from sys import stdout

import orjson
from aiohttp import ClientSession
from loguru import logger

from dis_snek import Snake, Intents, listen
from dis_snek import Permissions
from dis_snek import OptionTypes, InteractionContext, slash_command, slash_option
from dis_snek import ChannelTypes, GuildText, GuildNews
from dis_snek import Activity, ActivityType
from dis_snek import Embed, BrandColors, Timestamp
from dis_snek import Task, IntervalTrigger
from dis_snek.api.events import ChannelDelete

import db
import exceptions


logging.basicConfig(filename="other.log", encoding="utf-8", level=logging.INFO)

logger.remove()
logger.add(
    stdout,
    format="{time:MM.DD HH:m A ZZ} | {level.no} | {module}:{line} | {message}",
    level="DEBUG",
)
logger.add(
    "main.log",
    format="{time:MM.DD HH:m A ZZ} | {level.icon} ({level.no}) | {module}:{line} | {message}",
    level="DEBUG",
    rotation="15 MB",
    retention=2,
)

LAST_INCIDENT_CACHE = [None, set()]
LAST_SENT_LIST_CACHE = set()
NEW_CHANNELS_CACHE = set()

FETCH_API_UPDATES_TIMEOUT = int(float(getenv("FETCH_API_UPDATES_TIMEOUT")))

bot = Snake(
    intents=Intents.GUILDS | Intents.GUILD_MEMBERS,
    sync_interactions=True,
    fetch_members=True,
)


@listen()
@logger.catch()
async def on_startup() -> None:
    global session
    session = ClientSession(
        base_url="https://discordstatus.com",
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/89.0.4389.114 Safari/537.36"
        },
    )
    db.init()
    fetch_api_updates.start()
    logger.success("The bot has been started")


@listen()
async def on_ready() -> None:
    logger.debug("The bot is ready")


@listen()
async def channel_delete(ev: ChannelDelete) -> None:
    if ev.channel and ((channel_id := ev.channel.id) in NEW_CHANNELS_CACHE):
        NEW_CHANNELS_CACHE.discard(channel_id)
        await db.update_new_channels(NEW_CHANNELS_CACHE)
        logger.debug(f"Deleted {channel_id=} removed from the new channels list")

        LAST_SENT_LIST_CACHE.discard(channel_id)
        await db.update_last_sent_list(LAST_SENT_LIST_CACHE)
        logger.debug(f"Deleted {channel_id=} removed from the last sent channels list")


@slash_command("set", "Set status updates channel")
@slash_option(
    "channel",
    "The channel to receive incidents",
    required=True,
    opt_type=OptionTypes.CHANNEL,
    channel_types=[ChannelTypes.GUILD_TEXT, ChannelTypes.GUILD_NEWS],
)
async def set_channel(ctx: InteractionContext, channel: GuildText | GuildNews) -> None:
    if not ctx.author.has_permission(Permissions.MANAGE_CHANNELS):
        await ctx.send("You must have the `MANAGE CHANNELS` permission to operate!", ephemeral=True)
        return
    perms = ctx.guild.me.channel_permissions(channel)
    if not (
        perms & Permissions.VIEW_CHANNEL
        and perms & Permissions.SEND_MESSAGES
        and perms & Permissions.EMBED_LINKS
    ):
        await ctx.send(
            "I don't have permissions to (view channel/send messages/send embeds links) in this channel!",
            ephemeral=True,
        )
        return
    await ctx.defer(ephemeral=True)
    await db.set_guild_channel(ctx.guild_id, channel.id)
    NEW_CHANNELS_CACHE.add(channel.id)
    await db.update_new_channels(NEW_CHANNELS_CACHE)
    await ctx.send(f"New channel for updates: {channel.mention}!", ephemeral=True)


@slash_command("remove", "Remove status updates channel")
async def remove_channel(ctx: InteractionContext) -> None:
    if not ctx.author.has_permission(Permissions.MANAGE_CHANNELS):
        await ctx.send("You must have the `MANAGE CHANNELS` permission to operate!", ephemeral=True)
        return
    await ctx.defer(ephemeral=True)
    channel_id = await db.remove_guild_channel(ctx.guild_id)
    if channel_id:
        NEW_CHANNELS_CACHE.discard(channel_id)
        await db.update_new_channels(NEW_CHANNELS_CACHE)
    await ctx.send(
        "Successfully removed status updates channel!\nUse `/set` to select another channel!",
        ephemeral=True,
    )


@Task.create(IntervalTrigger(seconds=FETCH_API_UPDATES_TIMEOUT))
@logger.catch()
async def fetch_api_updates() -> None:
    logger.debug("Handling updates..")
    try:
        await update_presence()
        await fetch_incidents()
    except Exception:
        # TODO: handle common errors
        raise
    else:
        logger.success("Updates are handled")


async def update_presence() -> None:
    async with session.get("/metrics-display/5k2rt9f7pmny/day.json") as resp:
        if resp.status != 200:
            raise exceptions.InvalidHTTPStatusError(resp.status)

        raw = await resp.read()

    ping = round(orjson.loads(raw)["summary"]["mean"])
    logger.debug(f"New presence {ping=}")
    await bot.change_presence(activity=Activity(name=f"{ping}ms", type=ActivityType.LISTENING))


async def fetch_incidents() -> None:
    async with session.get("/api/v2/incidents.json") as resp:
        if resp.status != 200:
            raise exceptions.InvalidHTTPStatusError(resp.status)

        raw = await resp.read()

    incidents: list[dict] = orjson.loads(raw)["incidents"]
    incidents.reverse()

    global LAST_INCIDENT_CACHE
    global LAST_SENT_LIST_CACHE
    global NEW_CHANNELS_CACHE

    if not LAST_INCIDENT_CACHE[0]:
        logger.debug("Last incident cache is missing")
        db_last_incident = await db.get_last_incident()
        if db_last_incident and len(db_last_incident) > 1:
            LAST_INCIDENT_CACHE = [db_last_incident["i"], set(db_last_incident["u"])]
            logger.info("Using last incident from the db")
        else:
            LAST_INCIDENT_CACHE[0] = incidents[-1]["id"]
            logger.info("Using last incident from the statuspage")

    if not NEW_CHANNELS_CACHE:
        logger.debug("New channels cache is missing")
        db_new_channels = await db.get_new_channels()
        if db_new_channels and len(db_new_channels) > 1:
            NEW_CHANNELS_CACHE = set(db_new_channels["l"])
            logger.info("Using new channels from the db")

    if (
        not NEW_CHANNELS_CACHE
        and LAST_INCIDENT_CACHE[0] == incidents[-1]["id"]
        and all(
            u in LAST_INCIDENT_CACHE[1]
            for u in (u["id"] for u in incidents[-1]["incident_updates"])
        )
    ):
        logger.debug("Already up-to-date")
        return

    if not LAST_SENT_LIST_CACHE:
        logger.debug("Last sent list cache is missing")
        db_last_sent_list = await db.get_last_sent_list()
        if db_last_sent_list and len(db_last_sent_list) > 1:
            LAST_SENT_LIST_CACHE = set(db_last_sent_list["l"])
            logger.info("Using last sent list from the db")

    for last_index, incident in enumerate(incidents):  # TODO: optimise
        if incident["id"] == LAST_INCIDENT_CACHE[0]:
            break

    cache_deprecated = LAST_INCIDENT_CACHE[0] != incidents[-1]["id"]
    if cache_deprecated:
        LAST_SENT_LIST_CACHE.clear()
        await db.update_last_sent_list(LAST_SENT_LIST_CACHE)

    for i, incident in enumerate(incidents):
        if i < last_index:
            continue

        updates: list = incident["incident_updates"]
        if need_updates_fetch := not not updates:
            updates.reverse()

        if i == 49:
            LAST_INCIDENT_CACHE[0] = incident["id"]
            cache_deprecated = False
            if NEW_CHANNELS_CACHE or not LAST_INCIDENT_CACHE[1]:
                need_updates_fetch = True
            else:
                need_updates_fetch = updates[-1]["id"] != LAST_INCIDENT_CACHE[1][-1]

        if need_updates_fetch:

            invalid_channels = set()
            local_sent_list = set()

            for update in updates:
                if not NEW_CHANNELS_CACHE and update["id"] in LAST_INCIDENT_CACHE[1]:
                    continue

                if not cache_deprecated:
                    LAST_INCIDENT_CACHE[1].add(update["id"])

                embed = Embed(
                    title=f"{incident['name']} - {update['status']}",
                    description=f"First seen: {Timestamp.fromisoformat(incident['created_at'])}\nView at: {incident['shortlink']}",
                    color=BrandColors.BLURPLE,
                )
                if components := incident["components"]:
                    embed.add_field(
                        "Components affected",
                        ", ".join(*((c["name"] for c in components),)),
                    )
                embed.add_field("New comment", update["body"])
                embed.add_field("Commented at", Timestamp.fromisoformat(update["updated_at"]))

                async for channel_id in await db.get_all_channels():
                    if channel_id in LAST_SENT_LIST_CACHE or (channel_id in invalid_channels):
                        continue

                    channel = await bot.cache.fetch_channel(channel_id)

                    if is_valid := isinstance(channel, (GuildText, GuildNews)):
                        perms = channel.guild.me.channel_permissions(channel)
                        is_valid = (
                            perms & Permissions.VIEW_CHANNEL
                            and perms & Permissions.SEND_MESSAGES
                            and perms & Permissions.EMBED_LINKS
                        )

                    if not is_valid:
                        create_task(db.remove_guild_channel(channel_id))
                        invalid_channels.add(channel_id)
                        logger.debug(f"{channel_id=} has been removed")
                        continue

                    create_task(channel.send(embed=embed))
                    local_sent_list.add(channel_id)
                    logger.debug(f"Posted into {channel_id=}")

    if locals().get("local_sent_list"):
        LAST_SENT_LIST_CACHE.update(local_sent_list)
        await db.update_last_sent_list(LAST_SENT_LIST_CACHE)
        NEW_CHANNELS_CACHE.difference_update(LAST_SENT_LIST_CACHE)
        await db.update_new_channels(NEW_CHANNELS_CACHE)

    await db.update_last_incident(*LAST_INCIDENT_CACHE)


if __name__ == "__main__":
    bot.start(getenv("TOKEN"))

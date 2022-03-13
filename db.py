"""Database operations module"""

from os import getenv
from typing import Any, AsyncGenerator, Optional, Sequence

from loguru import logger
from motor.motor_asyncio import AsyncIOMotorClient


@logger.catch()
def init() -> None:
    global guilds, last
    logger.debug("Preparing database..")
    db = AsyncIOMotorClient(getenv("DB_URI"))
    guilds = db.NotifyInfo.guilds
    last = db.NotifyInfo.last
    logger.success("Database initialization completed")


@logger.catch()
async def get_last_incident() -> Optional[dict[str, str | list[str]]]:
    return await last.find_one({"_id": 0})


@logger.catch()
async def update_last_incident(incident_id: str, incident_updates: Sequence[str]) -> None:
    incident_updates = list(incident_updates)
    if not await get_last_incident():
        await last.insert_one({"_id": 0, "i": incident_id, "u": incident_updates})
    else:
        await last.update_one({"_id": 0, "i": incident_id}, {"$set": {"u": incident_updates}})
    logger.info(f"Last incident has been updated: {incident_id=}, {incident_updates=}")


@logger.catch()
async def get_last_sent_list() -> None:
    return await last.find_one({"_id": 1})


@logger.catch()
async def update_last_sent_list(channel_ids: Sequence[int]) -> None:
    channel_ids = list(channel_ids)
    if not await get_last_sent_list():
        await last.insert_one({"_id": 1, "l": channel_ids})
    else:
        await last.update_one({"_id": 1}, {"$set": {"l": channel_ids}})
    logger.info(f"Last sent list has been updated with {channel_ids=}")


@logger.catch()
async def get_new_channels() -> None:
    return await last.find_one({"_id": 2})


@logger.catch()
async def update_new_channels(channel_ids: Sequence[int]) -> None:
    channel_ids = list(channel_ids)
    if not await get_new_channels():
        await last.insert_one({"_id": 2, "l": channel_ids})
    else:
        await last.update_one({"_id": 2}, {"$set": {"l": channel_ids}})
    logger.info(f"New channels list has been updated with {channel_ids=}")


@logger.catch()
async def get_all_channels() -> AsyncGenerator[int, Any]:
    return (int(guild["c"]) async for guild in guilds.find())


@logger.catch()
async def set_guild_channel(guild_id: int, channel_id: int) -> None:
    # We store guild id to avoid spam and to increase ease of delete
    if await guilds.count_documents({"_id": guild_id}) == 0:
        await guilds.insert_one({"_id": guild_id, "c": channel_id})
    else:
        await guilds.update_one({"_id": guild_id}, {"$set": {"c": channel_id}})
    logger.info(f"Guild channel has been updated {guild_id=}, {channel_id=}")


@logger.catch()
async def remove_guild_channel(guild_or_channel_id: int) -> Optional[int]:
    # We store guild id to avoid spam and to increase ease of delete
    if (current := await guilds.find_one({"_id": guild_or_channel_id})) and (len(current) > 1):
        await guilds.delete_one({"_id": guild_or_channel_id})
    elif (current := await guilds.find_one({"c": guild_or_channel_id})) and (len(current) > 1):
        await guilds.delete_one({"c": guild_or_channel_id})
    else:
        return None
    logger.info(f"Guild channel has been removed {guild_or_channel_id=}")
    return int(current["c"])

import logging
import discord

log = logging.getLogger("soulslike")


async def get_text_channel(bot, channel_id: int):
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            log.exception("Não foi possível acessar o canal Discord %s", channel_id)
            return None
    if not hasattr(channel, "send"):
        log.error("O ID %s não corresponde a um canal que aceita mensagens.", channel_id)
        return None
    return channel

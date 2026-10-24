import logging
import discord
from discord.ext import commands

import config
from database import Database

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")


class SoulslikeBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=commands.when_mentioned, intents=discord.Intents.default())
        self.db = Database(config.DB_PATH)

    async def setup_hook(self):
        await self.db.connect()
        await self.load_extension("cogs.soulslike")
        if config.GUILD_ID:  # aparece na hora no seu servidor
            guild = discord.Object(config.GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            # apaga comandos GLOBAIS que sobraram de código antigo (ex.: /status_youtube), para não aparecerem duplicados
            self.tree.clear_commands(guild=None)
            await self.tree.sync()
        else:  # global pode levar até 1h
            await self.tree.sync()

    async def on_ready(self):
        logging.info("Soulslike Bot online como %s", self.user)


if __name__ == "__main__":
    if not config.TOKEN:
        raise SystemExit("Defina DISCORD_TOKEN nas variáveis de ambiente.")
    SoulslikeBot().run(config.TOKEN)

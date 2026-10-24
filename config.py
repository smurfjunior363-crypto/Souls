import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GUILD_ID = int(os.getenv("GUILD_ID", "0")) or None
MOD_CHANNEL_ID = int(os.getenv("MOD_CHANNEL_ID", "0"))            # onde a moderação aprova as runs
SOULSLIKE_CHANNEL_ID = int(os.getenv("SOULSLIKE_CHANNEL_ID", "0"))  # onde saem os anúncios de run/recorde
MOD_ROLE_ID = int(os.getenv("MOD_ROLE_ID", "0"))                  # cargo de moderador (opcional)
DB_PATH = os.getenv("DB_PATH", "/data/soulslike.db" if os.path.isdir("/data") else "data/soulslike.db")

# Cores
COR_OURO = 0xD4AF37
COR_PRATA = 0xC0C0C0
COR_PRETO = 0x0D0D0D

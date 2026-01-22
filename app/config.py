import os
from dotenv import load_dotenv

load_dotenv()

# Секреты
API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
PHONE = os.getenv("PHONE", "")

# Настройки бота
SOURCES = ["@vokop_test", "@bots_paty", "@cvo_yarmarka_chat", "@kapyushon_kupol_svo"]
DEST = "@vokopetestdest"
MODEL = "tngtech/deepseek-r1t2-chimera:free" # xiaomi/mimo-v2-flash:free
POST_DELAY = 2.0

# Пути к базам и сессиям
DB_PATH = "data/seen.sqlite3"
SESSION_NAME = "data/userbot_session"  # Telethon сам добавит .session

# Пути к медиа
TEMP_DIR = "tmp_media"
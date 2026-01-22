import os
from dotenv import load_dotenv

load_dotenv()

# Секреты
API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
PHONE = os.getenv("PHONE", "")

# Настройки бота
SOURCES = ["@vokop_test", "@bots_paty"]
DEST = "@vokopetestdest"
MODEL = "tngtech/deepseek-r1t2-chimera:free"
POST_DELAY = 2.0

# Пути
DB_PATH = "seen.sqlite3"
TEMP_DIR = "tmp_media"
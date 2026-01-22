import os
from dotenv import load_dotenv

load_dotenv()

# API Telegram
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
PHONE = os.getenv("PHONE")

# Настройки каналов (читаем из .env)
# .split(",") превращает строку в список, а .strip() убирает случайные пробелы
SOURCES_RAW = os.getenv("SOURCES", "")
SOURCES = [s.strip() for s in SOURCES_RAW.split(",") if s.strip()]

DEST = os.getenv("DEST")

# AI настройки
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
MODEL = os.getenv("MODEL", "tngtech/deepseek-r1t2-chimera:free")

# Прочие настройки
SESSION_NAME = "data/userbot_session"
DB_PATH = "data/seen.sqlite3"
TEMP_DIR = "tmp_media"
POST_DELAY = 600  # 10 минут
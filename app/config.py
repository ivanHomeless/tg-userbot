import os
from dotenv import load_dotenv

load_dotenv()

# API Telegram
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
PHONE = os.getenv("PHONE")

from pathlib import Path

# Пути к файлам
IDS_FILE = Path("data/sources_ids.txt")
LINKS_FILE = Path("data/sources_links.txt")


def load_sources():
    """Загружает ID (активные) и Ссылки (новые задачи)"""

    # 1. Загружаем проверенные ID как set из int
    ids = set()
    if IDS_FILE.exists():
        with open(IDS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                # Проверяем, что это число (поддержка отрицательных ID)
                if line and line.lstrip('-').isdigit():
                    ids.add(int(line))

    # 2. Загружаем ссылки для вступления (задачи на вход)
    links = []
    if LINKS_FILE.exists():
        with open(LINKS_FILE, "r", encoding="utf-8") as f:
            links = [line.strip() for line in f if line.strip()]

    return ids, links


# Инициализируем списки
SOURCES_IDS, SOURCES_LINKS = load_sources()

DEST = os.getenv("DEST")

# AI настройки
# Провайдер: openrouter | deepseek
AI_PROVIDER = (os.getenv("AI_PROVIDER", "openrouter") or "openrouter").strip().lower()

# OpenRouter: можно указать несколько ключей через запятую для ротации
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("MODEL", "tngtech/deepseek-r1t2-chimera:free")

# DeepSeek: один ключ, без ротации
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# Прочие настройки
SESSION_NAME = "data/userbot_session"
DB_PATH = "data/seen.sqlite3"
TEMP_DIR = "tmp_media"
POST_DELAY = 10 #600  # 10 минут
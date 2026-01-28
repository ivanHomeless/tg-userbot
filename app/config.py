import os
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

# ============================================
# TELEGRAM API
# ============================================
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
PHONE = os.getenv("PHONE")
DEST = os.getenv("DEST")

# ============================================
# POSTGRESQL
# ============================================
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://user:password@localhost:5432/telegram_bot"
)

# ============================================
# ФАЙЛЫ И ПУТИ
# ============================================
IDS_FILE = Path("data/sources_ids.txt")
LINKS_FILE = Path("data/sources_links.txt")
SESSION_NAME = "data/userbot_session"
TEMP_DIR = "tmp_media"

# ============================================
# AI НАСТРОЙКИ
# ============================================
AI_PROVIDER = (os.getenv("AI_PROVIDER", "openrouter") or "openrouter").strip().lower()

# OpenRouter
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("MODEL", "tngtech/deepseek-r1t2-chimera:free")

# DeepSeek
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# Google Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")

# ============================================
# НАСТРОЙКИ БОТА
# ============================================
POST_DELAY = int(os.getenv("POST_DELAY", 10))  # секунд между постами
AWAIT_TEXT_TIMEOUT = 20  # секунд ожидания текста после медиа/альбома
MEDIA_ONLY_CAPTION = "По всем вопросам с удовольствием отвечу, для заказа пишите @VES_nn"

# Лимит caption (1024 без премиума, 2048 с премиумом)
CAPTION_LIMIT = int(os.getenv("CAPTION_LIMIT", 1024))

# ============================================
# ЗАГРУЗКА ИСТОЧНИКОВ (для миграции)
# ============================================
def load_sources():
    """Загружает ID и ссылки из файлов (для первичной миграции в PostgreSQL)"""
    ids = set()
    if IDS_FILE.exists():
        with open(IDS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and line.lstrip('-').isdigit():
                    ids.add(int(line))
    
    links = []
    if LINKS_FILE.exists():
        with open(LINKS_FILE, "r", encoding="utf-8") as f:
            links = [line.strip() for line in f if line.strip()]
    
    return ids, links


# Для обратной совместимости (можно удалить после миграции)
SOURCES_IDS, SOURCES_LINKS = load_sources()

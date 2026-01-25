import logging
import time
from openai import OpenAI
from google import genai  # Добавили импорт Google GenAI
from app.config import (
    AI_PROVIDER,
    OPENROUTER_API_KEY,
    OPENROUTER_MODEL,
    DEEPSEEK_API_KEY,
    DEEPSEEK_MODEL,
    GEMINI_API_KEY,    # Добавить в config
    GEMINI_MODEL,      # Добавить в config
)
from app.prompts import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# --- Универсальная инициализация ключей ---
_PROVIDER = (AI_PROVIDER or "openrouter").strip().lower()


def _setup_keys():
    """Определяем список ключей в зависимости от провайдера"""
    if _PROVIDER == "google":
        source = GEMINI_API_KEY
    elif _PROVIDER == "deepseek":
        source = DEEPSEEK_API_KEY
    else:  # openrouter
        source = OPENROUTER_API_KEY

    if not source:
        return []
    return [k.strip() for k in source.split(",") if k.strip()]


_KEYS = _setup_keys()
_current_idx = 0


def get_llm_client():
    """Получаем клиента, используя текущий активный ключ"""
    global _current_idx

    if not _KEYS:
        raise RuntimeError(f"API ключи для {_PROVIDER} не настроены в .env")

    current_key = _KEYS[_current_idx]

    if _PROVIDER == "google":
        return genai.Client(api_key=current_key)

    if _PROVIDER == "deepseek":
        return OpenAI(api_key=current_key, base_url="https://api.deepseek.com")

    # default: openrouter
    return OpenAI(base_url="https://openrouter.ai/api/v1", api_key=current_key)


def rotate_key():
    """Универсальный сдвиг индекса для любого провайдера"""
    global _current_idx
    if len(_KEYS) > 1:
        _current_idx = (_current_idx + 1) % len(_KEYS)
        logger.warning(f"🔄 {AI_PROVIDER}: Переключение на ключ №{_current_idx + 1}")

def rewrite_text(text, max_retries=6):
    if not text:
        return ""

    attempt = 0
    base_delay = 2
    provider = (AI_PROVIDER or "openrouter").strip().lower()

    while attempt < max_retries:
        try:
            client = get_llm_client()

            if provider == "google":
                # Логика для Google Gemini
                model_name = GEMINI_MODEL or "gemini-1.5-flash"
                # В Gemini system_instruction выносится отдельно
                response = client.models.generate_content(
                    model=model_name,
                    config={'system_instruction': str(SYSTEM_PROMPT)},
                    contents=str(text)
                )
                return response.text

            else:
                # Логика для OpenAI-совместимых (DeepSeek, OpenRouter)
                model = DEEPSEEK_MODEL if provider == "deepseek" else OPENROUTER_MODEL
                messages = [
                    {"role": "system", "content": str(SYSTEM_PROMPT)},
                    {"role": "user", "content": str(text)}
                ]
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    timeout=45
                )
                return response.choices[0].message.content

        except Exception as e:
            attempt += 1
            err_str = str(e).lower()

            # Если ошибка лимитов — крутим ключ
            if any(x in err_str for x in ["429", "limit", "quota", "402", "exhausted"]):
                rotate_key()

            logger.error(f"❌ Ошибка {_PROVIDER} (попытка {attempt}/{max_retries}): {e}")

            if attempt < max_retries:
                time.sleep(base_delay * (2 ** (attempt - 1)))
            else:
                return f"**[Ошибка рерайта]**\n\n{text}"

    return text
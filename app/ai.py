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

_OPENROUTER_KEYS = []
if OPENROUTER_API_KEY:
    _OPENROUTER_KEYS = [k.strip() for k in OPENROUTER_API_KEY.split(",") if k.strip()]
_openrouter_key_index = 0

def get_llm_client():
    """Получаем клиента выбранного провайдера"""
    global _openrouter_key_index
    provider = (AI_PROVIDER or "openrouter").strip().lower()

    if provider == "google":
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is not set")
        return genai.Client(api_key=GEMINI_API_KEY)

    if provider == "deepseek":
        if not DEEPSEEK_API_KEY:
            raise RuntimeError("DEEPSEEK_API_KEY is not set")
        return OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")

    # default: openrouter
    if not _OPENROUTER_KEYS:
        raise RuntimeError("OPENROUTER_API_KEY is not set (or empty)")
    key = _OPENROUTER_KEYS[_openrouter_key_index]
    return OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key)

def rotate_key():
    global _openrouter_key_index
    if not _OPENROUTER_KEYS:
        return
    _openrouter_key_index = (_openrouter_key_index + 1) % len(_OPENROUTER_KEYS)
    logger.warning(f"🔄 Смена API ключа. Используем ключ №{_openrouter_key_index + 1}")

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
            error_str = str(e).lower()

            # Ротация только для OpenRouter
            can_rotate = provider == "openrouter" and len(_OPENROUTER_KEYS) > 1
            if can_rotate and any(x in error_str for x in ["429", "limit", "insufficient"]):
                logger.error(f"⚠️ Лимит OpenRouter исчерпан: {e}")
                rotate_key()
            else:
                logger.error(f"❌ Ошибка {provider} (попытка {attempt}/{max_retries}): {e}")

            if attempt < max_retries:
                wait_time = base_delay * (2 ** (attempt - 1))
                time.sleep(wait_time)
            else:
                logger.critical("🚨 Все попытки рерайта провалены.")
                return f"**[Ошибка рерайта]**\n\n{text}"

    return text
import logging
import time
from openai import OpenAI
from app.config import (
    AI_PROVIDER,
    OPENROUTER_API_KEY,
    OPENROUTER_MODEL,
    DEEPSEEK_API_KEY,
    DEEPSEEK_MODEL,
)
from app.prompts import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# OpenRouter: ключи через запятую, с ротацией
_OPENROUTER_KEYS = []
if OPENROUTER_API_KEY:
    _OPENROUTER_KEYS = [k.strip() for k in OPENROUTER_API_KEY.split(",") if k.strip()]
_openrouter_key_index = 0

def get_llm_client():
    """Получаем клиента выбранного провайдера"""
    global _openrouter_key_index

    provider = (AI_PROVIDER or "openrouter").strip().lower()
    if provider == "deepseek":
        if not DEEPSEEK_API_KEY:
            raise RuntimeError("DEEPSEEK_API_KEY is not set")
        print('DeepSeek')
        return OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")

    # default: openrouter
    if not _OPENROUTER_KEYS:
        raise RuntimeError("OPENROUTER_API_KEY is not set (or empty)")
    key = _OPENROUTER_KEYS[_openrouter_key_index]
    return OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key)

def rotate_key():
    """Переключаемся на следующий ключ в списке (только OpenRouter)"""
    global _openrouter_key_index
    if not _OPENROUTER_KEYS:
        return

    _openrouter_key_index = (_openrouter_key_index + 1) % len(_OPENROUTER_KEYS)
    logger.warning(f"🔄 Смена API ключа. Используем ключ №{_openrouter_key_index + 1}")


def rewrite_text(text, client=None, max_retries=6):
    """Рерайт текста с механизмом повторов и ротацией ключей"""
    if not text:
        return ""
    # Указываем тип переменной явно
    messages = [
        {"role": "system", "content": str(SYSTEM_PROMPT)}, # Напиши крипипасту на основе сообщения пользователя размером от 1024 до 1600 символов"
        {"role": "user", "content": str(text)}
    ]
    attempt = 0
    base_delay = 2  # Начальная задержка в секундах

    while attempt < max_retries:
        try:
            # Обновляем клиент при каждой попытке (на случай, если сменили ключ)
            current_client = get_llm_client()

            provider = (AI_PROVIDER or "openrouter").strip().lower()
            model = DEEPSEEK_MODEL if provider == "deepseek" else OPENROUTER_MODEL
            response = current_client.chat.completions.create(
                model=model,
                messages=messages,
                timeout=45  # Чтобы запрос не висел вечно
            )
            return response.choices[0].message.content

        except Exception as e:
            attempt += 1
            error_str = str(e).lower()

            # Если ошибка лимитов (429) или баланса (402) — меняем ключ немедленно
            provider = (AI_PROVIDER or "openrouter").strip().lower()
            can_rotate = provider != "deepseek" and len(_OPENROUTER_KEYS) > 1
            if can_rotate and ("429" in error_str or "limit" in error_str or "insufficient" in error_str):
                logger.error(f"⚠️ Лимит ключа исчерпан: {e}")
                rotate_key()
            else:
                logger.error(f"❌ Ошибка API (попытка {attempt}/{max_retries}): {e}")

            if attempt < max_retries:
                wait_time = base_delay * (2 ** (attempt - 1))  # 2, 4, 8 секунд
                logger.info(f"Ждем {wait_time}с перед повтором...")
                time.sleep(wait_time)
            else:
                logger.critical("🚨 Все попытки рерайта провалены.")
                return f"**[Ошибка рерайта]**\n\n{text}"  # Возвращаем оригинал, если AI сдох

    return text
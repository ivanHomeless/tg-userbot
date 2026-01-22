import logging
import time
from openai import OpenAI
from app.config import OPENROUTER_API_KEY, MODEL
from app.prompts import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# Превращаем строку ключей в список
API_KEYS = [k.strip() for k in OPENROUTER_API_KEY.split(",")]
current_key_index = 0

def get_llm_client():
    """Получаем клиент с текущим активным ключом"""
    global current_key_index
    key = API_KEYS[current_key_index]
    return OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=key,
    )
def rotate_key():
    """Переключаемся на следующий ключ в списке"""
    global current_key_index
    current_key_index = (current_key_index + 1) % len(API_KEYS)
    logger.warning(# Переход на новый круг ключей
        f"🔄 Смена API ключа. Используем ключ №{current_key_index + 1}"
    )


def rewrite_text(text, client=None, max_retries=6):
    """Рерайт текста с механизмом повторов и ротацией ключей"""
    if not text:
        return ""
    # Указываем тип переменной явно
    messages = [
        {"role": "system", "content": str(SYSTEM_PROMPT)},
        {"role": "user", "content": str(text)}
    ]
    attempt = 0
    base_delay = 2  # Начальная задержка в секундах

    while attempt < max_retries:
        try:
            # Обновляем клиент при каждой попытке (на случай, если сменили ключ)
            current_client = get_llm_client()

            response = current_client.chat.completions.create(
                model=MODEL,
                messages=messages,
                timeout=45  # Чтобы запрос не висел вечно
            )
            return response.choices[0].message.content

        except Exception as e:
            attempt += 1
            error_str = str(e).lower()

            # Если ошибка лимитов (429) или баланса (402) — меняем ключ немедленно
            if "429" in error_str or "limit" in error_str or "insufficient" in error_str:
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
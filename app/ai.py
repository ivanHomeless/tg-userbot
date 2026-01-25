import logging
import time
from openai import OpenAI
from google import genai
from app.config import (
    AI_PROVIDER,
    OPENROUTER_API_KEY,
    OPENROUTER_MODEL,
    DEEPSEEK_API_KEY,
    DEEPSEEK_MODEL,
    GEMINI_API_KEY,
    GEMINI_MODEL,
)
from app.prompts import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

_PROVIDER = (AI_PROVIDER or "openrouter").strip().lower()


def _setup_keys():
    """Определяем список ключей в зависимости от провайдера"""
    if _PROVIDER == "google":
        source = GEMINI_API_KEY
    elif _PROVIDER == "deepseek":
        source = DEEPSEEK_API_KEY
    else:
        source = OPENROUTER_API_KEY

    if not source:
        return []
    return [k.strip() for k in source.split(",") if k.strip()]


def _setup_models():
    """Определяем список моделей в зависимости от провайдера"""
    if _PROVIDER == "google":
        source = GEMINI_MODEL or "gemini-1.5-flash"
    elif _PROVIDER == "deepseek":
        source = DEEPSEEK_MODEL or "deepseek-chat"
    else:
        source = OPENROUTER_MODEL or "anthropic/claude-3.5-sonnet"

    if not source:
        return []
    return [m.strip() for m in source.split(",") if m.strip()]


_KEYS = _setup_keys()
_MODELS = _setup_models()
_current_key_idx = 0
_current_model_idx = 0
_failed_combinations = set()  # Храним проблемные пары (key_idx, model_idx)


def get_llm_client():
    """Получаем клиента, используя текущий активный ключ"""
    global _current_key_idx

    if not _KEYS:
        raise RuntimeError(f"API ключи для {_PROVIDER} не настроены в .env")

    # Пропускаем проблемные ключи
    attempts = 0
    while attempts < len(_KEYS):
        combo = (_current_key_idx, _current_model_idx)
        if combo not in _failed_combinations:
            break
        _current_key_idx = (_current_key_idx + 1) % len(_KEYS)
        attempts += 1

    if attempts >= len(_KEYS):
        logger.warning("⚠️ Все комбинации ключ+модель исчерпаны, сбрасываем метки")
        _failed_combinations.clear()

    current_key = _KEYS[_current_key_idx]

    if _PROVIDER == "google":
        return genai.Client(api_key=current_key)

    if _PROVIDER == "deepseek":
        return OpenAI(api_key=current_key, base_url="https://api.deepseek.com")

    return OpenAI(base_url="https://openrouter.ai/api/v1", api_key=current_key)


def get_current_model():
    """Получаем текущую активную модель"""
    if not _MODELS:
        raise RuntimeError(f"Модели для {_PROVIDER} не настроены в .env")
    return _MODELS[_current_model_idx]


def rotate_key(mark_failed=False):
    """Ротация ключа API"""
    global _current_key_idx

    if mark_failed:
        combo = (_current_key_idx, _current_model_idx)
        _failed_combinations.add(combo)
        logger.warning(
            f"⚠️ Комбинация ключ №{_current_key_idx + 1} + модель '{_MODELS[_current_model_idx]}' помечена как исчерпанная"
        )

    if len(_KEYS) > 1:
        old_idx = _current_key_idx
        _current_key_idx = (_current_key_idx + 1) % len(_KEYS)
        logger.warning(
            f"🔄 {_PROVIDER}: Переключение ключа №{old_idx + 1} → №{_current_key_idx + 1}"
        )


def rotate_model(mark_failed=False):
    """Ротация модели"""
    global _current_model_idx

    if mark_failed:
        combo = (_current_key_idx, _current_model_idx)
        _failed_combinations.add(combo)
        logger.warning(
            f"⚠️ Комбинация ключ №{_current_key_idx + 1} + модель '{_MODELS[_current_model_idx]}' помечена как проблемная"
        )

    if len(_MODELS) > 1:
        old_idx = _current_model_idx
        _current_model_idx = (_current_model_idx + 1) % len(_MODELS)
        logger.warning(
            f"🔄 {_PROVIDER}: Переключение модели '{_MODELS[old_idx]}' → '{_MODELS[_current_model_idx]}'"
        )


def rewrite_text(text, max_retries=6):
    if not text:
        return ""

    attempt = 0
    base_delay = 2
    provider = (AI_PROVIDER or "openrouter").strip().lower()

    while attempt < max_retries:
        try:
            client = get_llm_client()
            model = get_current_model()

            logger.info(
                f"🤖 Запрос к {provider}: ключ №{_current_key_idx + 1}, модель '{model}'"
            )

            if provider == "google":
                response = client.models.generate_content(
                    model=model,
                    config={'system_instruction': str(SYSTEM_PROMPT)},
                    contents=str(text)
                )
                # Успех - снимаем метку с комбинации
                _failed_combinations.discard((_current_key_idx, _current_model_idx))
                return response.text

            else:
                # OpenAI-совместимые (DeepSeek, OpenRouter)
                messages = [
                    {"role": "system", "content": str(SYSTEM_PROMPT)},
                    {"role": "user", "content": str(text)}
                ]
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    timeout=45
                )
                # Успех - снимаем метку с комбинации
                _failed_combinations.discard((_current_key_idx, _current_model_idx))
                return response.choices[0].message.content

        except Exception as e:
            attempt += 1
            err_str = str(e).lower()

            # Определяем тип ошибки
            is_limit_error = any(x in err_str for x in ["429", "limit", "quota", "402", "exhausted"])
            is_model_error = any(x in err_str for x in ["model", "not found", "invalid", "unsupported"])

            logger.error(f"❌ Ошибка {provider} (попытка {attempt}/{max_retries}): {e}")

            # Стратегия ротации:
            if is_model_error:
                # Проблема с моделью - пробуем другую модель
                rotate_model(mark_failed=True)
            elif is_limit_error:
                # Лимиты - сначала пробуем другой ключ
                rotate_key(mark_failed=True)
                # Если ключи кончились, пробуем другую модель
                if len(_KEYS) > 1 and attempt % len(_KEYS) == 0:
                    rotate_model(mark_failed=False)
            else:
                # Другая ошибка - пробуем следующий ключ без метки
                rotate_key(mark_failed=False)

            if attempt < max_retries:
                delay = base_delay * (2 ** (attempt - 1))
                logger.info(f"⏳ Ожидание {delay}с перед повтором...")
                time.sleep(delay)
            else:
                return f"**[Ошибка рерайта после {max_retries} попыток]**\n\n{text}"

    return text
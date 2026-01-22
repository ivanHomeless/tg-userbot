import re
import logging
from openai import OpenAI
from config import OPENROUTER_API_KEY, MODEL
from prompts import SYSTEM_PROMPT  # <-- Добавили импорт

logger = logging.getLogger(__name__)

def get_llm_client():
    return OpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1"
    )

def rewrite_text(text: str, llm) -> str:
    try:
        resp = llm.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT}, # <-- Используем здесь
                {"role": "user", "content": f"Сделай рерайт следующего текста:\n\n{text}"}
            ],
            temperature=0.7
        )
        result = (resp.choices[0].message.content or "").strip()
        result = re.sub(r"<think>.*?</think>", "", result, flags=re.DOTALL).strip()
        return result
    except Exception as e:
        logger.error(f"Ошибка AI: {e}")
        return ""
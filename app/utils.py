import logging

from pathlib import Path
from app.config import LINKS_FILE, IDS_FILE, SOURCES_IDS

logger = logging.getLogger(__name__)

def split_text(text, limit=4096):
    """Режет текст на куски по 4096 символов"""
    return [text[i:i+limit] for i in range(0, len(text), limit)]


def save_source_id(new_id, file_path=IDS_FILE):
    """
    Записывает ID в файл, если его там нет.
    Правильно обрабатывает форматы ID Telegram.
    """
    # Конвертируем в int для проверки
    try:
        chat_id = int(new_id)
    except (ValueError, TypeError):
        logger.error(f"Невалидный ID: {new_id}")
        return False

    # Telegram ID логика:
    # - Обычные группы: отрицательные, начинаются с -
    # - Супергруппы и каналы: начинаются с -100
    # - Если ID положительный, это всегда -100 формат

    if chat_id > 0:
        # Положительный ID → это маскированный канал/супергруппа
        chat_id = int(f"-100{chat_id}")
    elif chat_id < 0 and not str(chat_id).startswith('-100'):
        # Уже отрицательный, но без -100
        # Это либо старая группа, либо нужно добавить -100
        # Проверяем длину: если больше 10 цифр - это канал
        if len(str(abs(chat_id))) >= 10:
            chat_id = int(f"-100{abs(chat_id)}")

    # Если уже начинается с -100 или это короткий ID - оставляем как есть

    new_id = str(chat_id)

    file = Path(file_path)
    file.parent.mkdir(parents=True, exist_ok=True)

    if not file.exists():
        file.touch()

    # Читаем существующие ID
    with open(file, "r", encoding="utf-8") as f:
        existing_ids = {line.strip() for line in f if line.strip()}

    if new_id not in existing_ids:
        with open(file, "a", encoding="utf-8") as f:
            f.write(f"{new_id}\n")
        logger.info(f"✅ ID {new_id} добавлен в список источников")

        # Обновляем глобальный set
        SOURCES_IDS.add(int(new_id))
        return True

    return False


def remove_link_from_file(link_to_remove, file_path=LINKS_FILE):
    if not file_path.exists():
        return

    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    link_normalized = link_to_remove.strip().lower()

    # Оставляем строки, которые НЕ совпадают с удаляемой ссылкой
    new_lines = []
    removed = False

    for line in lines:
        if line.strip().lower() == link_normalized:
            removed = True
            continue  # Пропускаем эту строку
        new_lines.append(line)  # Сохраняем с оригинальным \n

    if removed:
        with open(file_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        logging.info(f"🗑️ Ссылка удалена: {link_to_remove}")
    else:
        logging.warning(f"⚠️ Ссылка не найдена: {link_to_remove}")
import logging

from pathlib import Path
from app.config import LINKS_FILE


def split_text(text, limit=4096):
    """Режет текст на куски по 4096 символов"""
    return [text[i:i+limit] for i in range(0, len(text), limit)]

def save_source_id(new_id, file_path="data/sources_ids.txt"):
    """
    Записывает ID в файл, если его там нет.
    Каждый ID на новой строке.
    """
    new_id = str(new_id).strip()

    # Если это длинный положительный ID, превращаем его в ID канала
    if not new_id.startswith('-'):
        # Канальные/групповые ID обычно длинные
        if len(new_id) > 7:
            new_id = f"-100{new_id}"
        else:
            # Маленькие группы начинаются просто с минуса
            new_id = f"-{new_id}"

    file = Path(file_path)

    # Создаем папку data, если её нет
    file.parent.mkdir(parents=True, exist_ok=True)

    # Если файл не существует, создаем его
    if not file.exists():
        file.touch()

    # Читаем существующие ID
    with open(file, "r", encoding="utf-8") as f:
        # strip() убирает пробелы и символы переноса строки
        existing_ids = {line.strip() for line in f if line.strip()}

    # Проверяем наличие
    if new_id not in existing_ids:
        with open(file, "a", encoding="utf-8") as f:
            f.write(f"{new_id}\n")
        return True

    return False

def remove_link_from_file(link_to_remove, file_path=LINKS_FILE):
    """
    Удаляет конкретную ссылку из файла LINKS_FILE, когда бот в нее успешно вступил.
    """
    if not file_path.exists():
        return

    # 1. Читаем все текущие ссылки
    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # 2. Фильтруем список, оставляя всё, кроме удаляемой ссылки
    # strip() важен, так как в файле ссылки могут быть с пробелами или \n
    new_lines = [
        line for line in lines
        if line.strip().lower() != link_to_remove.strip().lower()
    ]

    # 3. Перезаписываем файл только если что-то изменилось
    if len(new_lines) < len(lines):
        with open(file_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        logging.info(f"🗑️ Ссылка удалена из списка задач: {link_to_remove}")
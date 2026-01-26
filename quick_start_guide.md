# ⚡ Быстрый старт (3 минуты)

## 🎯 Цель

Запустить бота с PostgreSQL за **3 минуты**!

---

## ✅ Шаг 1: PostgreSQL (30 сек)

### Вариант A: Docker (рекомендуется)

```bash
docker-compose up -d
```

Готово! PostgreSQL запущен на `localhost:5432`

### Вариант B: Локально

```bash
# Установка (Ubuntu/Debian)
sudo apt install postgresql

# Создание БД
sudo -u postgres psql -c "CREATE DATABASE telegram_bot;"
sudo -u postgres psql -c "CREATE USER botuser WITH PASSWORD 'secure_password';"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE telegram_bot TO botuser;"
```

---

## ✅ Шаг 2: Настройка проекта (1 мин)

```bash
# Создай виртуальное окружение
python3 -m venv venv
source venv/bin/activate

# Установи зависимости
pip install -r requirements.txt

# Создай .env
cp .env.example .env
nano .env  # отредактируй
```

**Обязательно заполни в `.env`:**

```env
# Telegram
API_ID=твой_api_id
API_HASH=твой_api_hash
PHONE=+7...
DEST=@твой_канал

# PostgreSQL
DATABASE_URL=postgresql+asyncpg://botuser:secure_password@localhost:5432/telegram_bot

# AI
OPENROUTER_API_KEY=твой_ключ
```

---

## ✅ Шаг 3: Создание структуры (30 сек)

```bash
# Создай папки
mkdir -p app/{models,database,services}
mkdir -p alembic/versions
mkdir -p data logs tmp_media

# Создай __init__.py
touch app/__init__.py
touch app/{models,database,services}/__init__.py
```

---

## ✅ Шаг 4: Миграции (30 сек)

```bash
# Создай первую миграцию
alembic revision --autogenerate -m "Initial schema"

# Примени к БД
alembic upgrade head
```

**Проверка:**

```bash
# Подключись к PostgreSQL
psql -U botuser -d telegram_bot

# Список таблиц
\dt

# Должны быть: sources, message_queue, posts, post_media
```

---

## ✅ Шаг 5: Запуск! (10 сек)

```bash
python main.py
```

**Ожидаемый вывод:**

```
============================================================
🚀 Запуск Telegram бота с PostgreSQL
============================================================
✅ База данных инициализирована
✅ База и папки готовы
🔄 Проверка подписок (0 источников)...
🚀 Бот запущен и слушает каналы...
```

---

## 🎉 Готово!

Теперь добавь источники и тестируй!

---

## 📝 Добавление источника

### Через Python консоль:

```python
import asyncio
from app.bot_logic import TGBot

async def add():
    bot = TGBot()
    await bot.client.start(phone="+7...")
    await bot.add_source_by_link("@test_channel")
    await bot.client.disconnect()

asyncio.run(add())
```

### Через psql:

```sql
INSERT INTO sources (chat_id, is_active) VALUES (-1001234567890, TRUE);
```

---

## 🔍 Проверка работы

### 1. Отправь тестовое сообщение в источник

**Тест A: Текст + короткое фото**
- Отправь фото с подписью (100 символов)
- Ожидается: **1 пост** (медиа + текст вместе)

**Тест B: Текст + длинное описание**
- Отправь фото с текстом (2000 символов)
- Ожидается: **2 сообщения** (медиа отдельно, текст отдельно)

**Тест C: Альбом**
- Отправь 3 фото сразу с описанием
- Ожидается: **1 пост** (альбом + текст)

### 2. Смотри логи

```bash
tail -f logs/bot_work.log
```

### 3. Проверяй БД

```bash
psql -U botuser -d telegram_bot

# Очередь сообщений
SELECT id, source_id, message_id, media_type, rewrite_status FROM message_queue;

# Готовые посты
SELECT id, status, final_text FROM posts;
```

---

## ⚙️ Настройки (опционально)

### Премиум аккаунт:

```env
# .env
CAPTION_LIMIT=2048  # вместо 1024
```

### Быстрее публиковать:

```env
POST_DELAY=5  # вместо 10
```

---

## 🐛 Troubleshooting

### "could not connect to server"

```bash
# Проверь PostgreSQL
docker-compose ps
# или
sudo systemctl status postgresql
```

### "relation does not exist"

```bash
# Примени миграции
alembic upgrade head
```

### Медиа не отправляются

Проблема: устарел `file_reference`

**Решение:** Telegram обновляет file_reference каждые ~24 часа. Если медиа старше суток, пересылай через forward или скачивай заново.

---

## 📚 Дальше

- Прочитай `FIXES.md` — понимание логики
- Прочитай `migration_script.md` — полная документация
- Настрой автозапуск (systemd)
- Настрой бэкап PostgreSQL

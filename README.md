# Telegram Content Aggregator Bot

Автоматизированный бот для сбора, обработки и публикации контента из Telegram каналов с AI-рерайтингом.

## 📋 Содержание

- [Описание](#описание)
- [Возможности](#возможности)
- [Архитектура](#архитектура)
- [Структура проекта](#структура-проекта)
- [Технологии](#технологии)
- [Установка](#установка)
- [Настройка](#настройка)
- [Использование](#использование)
- [API компонентов](#api-компонентов)
- [База данных](#база-данных)
- [Скрипты управления](#скрипты-управления)
- [Тестирование](#тестирование)
- [Deployment](#deployment)
- [Troubleshooting](#troubleshooting)
- [Лицензия](#лицензия)

---

## Описание

Бот-агрегатор автоматически:
1. **Собирает** сообщения из указанных Telegram каналов через Telethon (userbot)
2. **Обрабатывает** текст через AI (OpenRouter/DeepSeek/Gemini) для рерайтинга
3. **Публикует** в целевой канал с заданным интервалом

### Ключевые особенности

- ✅ Поддержка текста, фото, видео, документов
- ✅ **Надежная** обработка альбомов через ручной буфер + `catch_up=True`
- ✅ Автоматический сбор всех текстов из альбома
- ✅ Восстановление пропущенных updates с разных Data Centers
- ✅ Очередь публикаций с контролем интервалов
- ✅ PostgreSQL для надежного хранения
- ✅ Множественные AI-провайдеры с fallback
- ✅ Асинхронная архитектура (asyncio)

---

## Возможности

### 1. Сбор контента (Collector)
- Мониторинг неограниченного количества источников
- **Надежная** обработка альбомов: ручной буфер 3с + `catch_up=True`
- Сбор **всех** текстов из всех сообщений альбома
- Восстановление пропущенных updates (DC splits)
- Дедупликация сообщений
- Сохранение file_id для медиа (без скачивания)

### 2. Обработка текста (Processor)
- AI-рерайтинг через OpenRouter/DeepSeek/Gemini
- Ротация ключей и моделей при лимитах
- Обработка альбомов с единым caption (все тексты объединяются)
- Быстрая сборка постов (3 секунды между проверками)
- Сборка постов из обработанных сообщений

### 3. Публикация (Publisher)
- Умная публикация текста и медиа
- Разбиение длинных caption на несколько сообщений
- Поддержка альбомов до 10 медиа
- Контроль интервалов между постами
- Обработка ошибок с логированием

### 4. Управление источниками
- Добавление по ID, username, ссылкам
- Автоматическое вступление в каналы
- Поддержка приватных каналов через invite links
- Активация/деактивация источников
- Обновление метаданных (title, username)

---

## Архитектура

### Потоки данных

```
┌─────────────────┐
│ Telegram API    │
│ (Источники)     │
│ catch_up=True   │ ← Восстанавливает пропущенные updates
└────────┬────────┘
         │
         └─ events.NewMessage() ──► message_handler
                                    ├─ grouped_id? → буфер 3с → album
                                    └─ no grouped_id? → сразу
         ▼
┌─────────────────┐
│   Collector     │──► MessageQueue (PostgreSQL)
│ collect_album() │    ├─ Текст (все caption из альбома)
│ collect_message │    ├─ Медиа (file_id, access_hash)
└─────────────────┘    └─ Альбомы (grouped_id)
         │
         ▼
┌─────────────────┐
│   Processor     │
│ (4 фоновых      │
│  задачи)        │
└────┬────────────┘
     │
     ├─► Rewriter (каждые 30 сек)
     │   └─ AI рерайтинг текста
     │
     ├─► AwaitingCloser (каждые 15 сек)
     │   └─ Закрытие истекших ожиданий
     │
     └─► PostBuilder (каждые 3 сек)
         └─ Сборка постов
                       │
                       ▼
                ┌──────────────┐
                │ Post + Media │ (PostgreSQL)
                └──────┬───────┘
                       │
                       ▼
         ┌─────────────────────┐
         │    Publisher        │
         │ (каждые 15 сек)     │
         └──────────┬──────────┘
                    │
                    ▼
         ┌─────────────────────┐
         │  Целевой канал      │
         │  (публикация)       │
         └─────────────────────┘
```

### Компоненты

#### 1. MessageCollector
**Назначение:** Сбор сообщений из источников

**Обработчик:**
- Один `message_handler` для ВСЕХ сообщений
- `grouped_id` → буфер 3 секунды → `collect_album()`
- Нет `grouped_id` → `collect_message()` сразу

**Логика:**
- Текст без медиа → `rewrite_status=pending`
- Медиа с текстом (одиночное) → `rewrite_status=pending`
- **Альбом** → собирает ВСЕ тексты из всех сообщений, `rewrite_status=skipped`
- Медиа без текста (одиночное) → `awaiting_text=True`

#### 2. MessageProcessor
**Назначение:** Обработка и подготовка постов

**Фоновые задачи:**
1. **Rewriter** (30 сек)
   - Берёт `rewrite_status=pending` (только одиночные)
   - Отправляет в AI
   - Обновляет `rewritten_text`, `rewrite_status=done`

2. **AwaitingCloser** (15 сек)
   - Закрывает `awaiting_text=True` если `awaiting_until < now`

3. **PostBuilder** (3 сек)
   - Собирает альбомы (буфер уже собрал все медиа за 3с)
   - Рерайтит caption альбома через AI (все тексты объединены)
   - Создаёт Post + PostMedia
   - Собирает одиночные сообщения в посты

#### 3. PostPublisher
**Назначение:** Публикация в целевой канал

**Логика:**
- Берёт `status=scheduled` с `scheduled_at <= now`
- Текст без медиа → `send_message`
- Медиа без текста → `send_file(caption=None)`
- Медиа с текстом:
  - Если caption < 1024 → `send_file(caption=text)`
  - Если caption > 1024 → `send_file` + `send_message`
- Альбомы → `send_file([media1, media2, ...], caption=text)`
- Интервал между постами: `POST_DELAY` секунд

---

## Структура проекта

```
telegram-content-bot/
│
├── app/                          # Основной код приложения
│   ├── __init__.py
│   ├── bot_logic.py              # Главная логика бота
│   ├── config.py                 # Конфигурация из .env
│   ├── prompts.py                # Промпты для AI
│   ├── utils.py                  # Вспомогательные функции (split_text)
│   ├── ai.py                     # AI провайдеры (OpenRouter, DeepSeek, Gemini)
│   │
│   ├── models/                   # SQLAlchemy модели
│   │   ├── __init__.py
│   │   ├── base.py               # Base class
│   │   ├── source.py             # Source (источники)
│   │   ├── message.py            # MessageQueue (очередь сообщений)
│   │   ├── post.py               # Post, PostMedia (посты)
│   │   └── README.md             # Документация моделей
│   │
│   ├── database/                 # Подключение к БД
│   │   ├── __init__.py
│   │   └── engine.py             # AsyncEngine, SessionLocal
│   │
│   └── services/                 # Бизнес-логика
│       ├── __init__.py
│       ├── collector.py          # MessageCollector
│       ├── processor.py          # MessageProcessor
│       └── publisher.py          # PostPublisher
│
├── scripts/                      # Утилиты управления
│   ├── __init__.py
│   ├── fetch_channel_info.py    # Получение метаданных каналов
│   ├── add_sources_from_ids.py  # Добавление источников по ID
│   ├── add_sources_from_links.py # Добавление источников по ссылкам
│   ├── README.md                 # Документация скриптов
│   └── CHEATSHEET.md             # Быстрая шпаргалка
│
├── tests/                        # Тесты
│   ├── __init__.py
│   ├── conftest.py               # Фикстуры pytest
│   ├── unit/                     # Юнит-тесты
│   │   ├── test_collector.py
│   │   ├── test_processor.py
│   │   └── test_publisher.py
│   ├── integration/              # Интеграционные тесты
│   └── fixtures/                 # Моки и тестовые данные
│
├── alembic/                      # Миграции БД
│   ├── versions/                 # Файлы миграций
│   ├── env.py                    # Конфигурация Alembic
│   └── script.py.mako
│
├── data/                         # Данные и сессии
│   ├── userbot.session           # Telethon сессия (не коммитить!)
│   ├── sources_ids.txt.example   # Пример файла с ID
│   ├── sources_links.txt.example # Пример файла со ссылками
│   └── .gitignore
│
├── logs/                         # Логи
│   └── bot_work.log
│
├── main.py                       # Точка входа
├── requirements.txt              # Python зависимости
├── requirements-test.txt         # Зависимости для тестов
├── .env.example                  # Пример переменных окружения
├── .gitignore
├── alembic.ini                   # Конфигурация Alembic
├── pytest.ini                    # Конфигурация pytest
├── docker-compose.yml            # Docker Compose для PostgreSQL
└── README.md                     # Эта документация
```

---

## Технологии

### Backend
- **Python 3.11+** - язык программирования
- **asyncio** - асинхронное программирование
- **Telethon** - MTProto клиент для Telegram
- **SQLAlchemy 2.0** - ORM с async поддержкой
- **asyncpg** - асинхронный драйвер PostgreSQL
- **Alembic** - миграции БД

### AI Провайдеры
- **OpenRouter** - доступ к множеству моделей
- **DeepSeek** - быстрый и дешевый рерайтинг
- **Google Gemini** - альтернативный провайдер

### База данных
- **PostgreSQL 15+** - основная БД
- **SQLite** (опционально) - для локальной разработки

### Тестирование
- **pytest** - фреймворк тестирования
- **pytest-asyncio** - async тесты
- **pytest-cov** - покрытие кода

---

## Установка

### 1. Системные требования

- Python 3.11 или выше
- PostgreSQL 15+
- Git

### 2. Клонирование репозитория

```bash
git clone https://github.com/yourusername/telegram-content-bot.git
cd telegram-content-bot
```

### 3. Создание виртуального окружения

```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# или
venv\Scripts\activate    # Windows
```

### 4. Установка зависимостей

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 5. Установка PostgreSQL

#### Вариант А: Локальная установка

**Ubuntu/Debian:**
```bash
sudo apt update
sudo apt install postgresql postgresql-contrib
sudo systemctl start postgresql
sudo systemctl enable postgresql
```

**macOS (Homebrew):**
```bash
brew install postgresql@15
brew services start postgresql@15
```

**Windows:**
Скачай с https://www.postgresql.org/download/windows/

#### Вариант Б: Docker

```bash
docker-compose up -d
```

`docker-compose.yml` уже включен в проект.

### 6. Создание базы данных

```bash
# Подключись к PostgreSQL
sudo -u postgres psql

# В psql выполни:
CREATE DATABASE telegrambot;
CREATE USER botuser WITH PASSWORD 'your_secure_password';
GRANT ALL PRIVILEGES ON DATABASE telegrambot TO botuser;
\q
```

### 7. Применение миграций

```bash
alembic upgrade head
```

---

## Настройка

### 1. Создание .env файла

```bash
cp .env.example .env
nano .env
```

### 2. Заполнение переменных окружения

```env
# Telegram API (получи на https://my.telegram.org)
API_ID=12345678
API_HASH=abc123def456...
PHONE=+79991234567

# Целевой канал для публикации
DEST=-1001234567890

# PostgreSQL
DATABASE_URL=postgresql+asyncpg://botuser:your_password@localhost:5432/telegrambot

# AI Provider (openrouter | deepseek | google)
AI_PROVIDER=openrouter

# OpenRouter (если AI_PROVIDER=openrouter)
OPENROUTER_API_KEY=sk-or-v1-xxxxx
MODEL=anthropic/claude-3.5-sonnet

# DeepSeek (если AI_PROVIDER=deepseek)
DEEPSEEK_API_KEY=sk-xxxxx
DEEPSEEK_MODEL=deepseek-chat

# Google Gemini (если AI_PROVIDER=google)
GEMINI_API_KEY=AIzaSy...
GEMINI_MODEL=gemini-1.5-flash

# Настройки публикации
POST_DELAY=10              # Задержка между постами (секунды)
CAPTION_LIMIT=1024         # Лимит caption (1024 или 2048 для Premium)

# Настройки обработки
AWAIT_TEXT_TIMEOUT=20      # Таймаут ожидания текста для одиночного медиа (НЕ для альбомов)
MEDIA_ONLY_CAPTION="По всем вопросам..."  # Подпись для медиа без текста
```

### 3. Получение Telegram API

1. Открой https://my.telegram.org
2. Войди с номером телефона
3. Перейди в "API development tools"
4. Создай приложение
5. Скопируй `API_ID` и `API_HASH`

### 4. Первый запуск (авторизация)

```bash
python main.py
```

При первом запуске:
1. Бот запросит код из Telegram
2. Возможно попросит 2FA пароль
3. Сессия сохранится в `data/userbot.session`

---

## Использование

### Запуск бота

```bash
python main.py
```

**Вывод:**
```
2026-01-27 00:00:00 INFO root ========== Запуск бота ==========
2026-01-27 00:00:01 INFO app.database.engine ✅ База данных инициализирована
2026-01-27 00:00:02 INFO app.bot_logic ✅ Userbot подключён
2026-01-27 00:00:03 INFO app.bot_logic Найдено 5 активных источников
2026-01-27 00:00:04 INFO app.bot_logic 🚀 Бот запущен, ожидание сообщений...
```

### Добавление источников

#### Способ 1: По ID

```bash
# Создай файл с ID
cat > data/sources_ids.txt << EOF
-1001234567890
-1009876543210
EOF

# Добавь источники
python -m scripts.add_sources_from_ids data/sources_ids.txt
```

#### Способ 2: По ссылкам/username

```bash
# Один канал
python -m scripts.add_sources_from_links @channel_name

# Несколько каналов
python -m scripts.add_sources_from_links @ch1 @ch2 t.me/ch3

# Из файла
cat > data/sources_links.txt << EOF
@channel1
https://t.me/channel2
https://t.me/+privatelink
EOF

python -m scripts.add_sources_from_links data/sources_links.txt
```

#### Способ 3: SQL

```sql
INSERT INTO source (chat_id, username, title, join_link, is_active)
VALUES (-1001234567890, 'channel_name', 'Channel Title', 'https://t.me/channel_name', true);
```

### Просмотр источников

```bash
# Показать все каналы userbot
python -m scripts.fetch_channel_info --list

# Проверить конкретный канал
python -m scripts.fetch_channel_info -1001234567890

# Обновить метаданные в БД
python -m scripts.fetch_channel_info --all
```

### Управление источниками в БД

```sql
-- Посмотреть все источники
SELECT chat_id, username, title, is_active FROM source;

-- Отключить источник
UPDATE source SET is_active = false WHERE chat_id = -1001234567890;

-- Включить обратно
UPDATE source SET is_active = true WHERE chat_id = -1001234567890;

-- Удалить источник
DELETE FROM source WHERE chat_id = -1001234567890;
```

### Мониторинг

#### Логи

```bash
# Просмотр логов
tail -f logs/bot_work.log

# Только ошибки
grep ERROR logs/bot_work.log

# Только Processor
grep Processor logs/bot_work.log
```

#### SQL запросы

```sql
-- Статус очереди сообщений
SELECT rewrite_status, COUNT(*) 
FROM message_queue 
GROUP BY rewrite_status;

-- Ожидающие текст
SELECT source_id, message_id, awaiting_until 
FROM message_queue 
WHERE awaiting_text = true;

-- Готовые посты
SELECT id, status, scheduled_at 
FROM post 
WHERE status = 'scheduled' 
ORDER BY scheduled_at;

-- Статистика по источникам
SELECT s.chat_id, s.title, COUNT(m.id) as messages
FROM source s
LEFT JOIN message_queue m ON s.chat_id = m.source_id
GROUP BY s.chat_id, s.title;
```

---

## API компонентов

### MessageCollector

```python
from app.services.collector import MessageCollector
from app.database.engine import SessionLocal

async with SessionLocal() as session:
    collector = MessageCollector(session)
    await collector.collect_message(event)
```

**Методы:**
- `collect_album(event)` - обработка альбома через events.Album
- `collect_message(event)` - обработка одиночного сообщения
- `_handle_text_message(msg, chat_id)` - текст без медиа
- `_handle_media_with_text(msg, chat_id)` - медиа + текст (одиночное)
- `_handle_media_without_text(msg, chat_id)` - медиа без текста (одиночное)
- `_update_album_collected_at(chat_id, grouped_id)` - обновление времени при привязке текста

### MessageProcessor

```python
from app.services.processor import MessageProcessor

async with SessionLocal() as session:
    processor = MessageProcessor(session)

    # Рерайт
    await processor.process_pending_rewrites()

    # Закрытие ожиданий
    await processor.close_expired_awaiting()

    # Сборка постов
    await processor.build_posts_from_messages()
```

**Методы:**
- `process_pending_rewrites()` - рерайт одиночных сообщений
- `rewrite_message(msg)` - рерайт конкретного сообщения
- `close_expired_awaiting()` - закрытие истекших ожиданий
- `build_posts_from_messages()` - сборка постов из готовых сообщений
- `_build_album_post(messages)` - сборка альбома
- `_build_single_post(msg)` - сборка одиночного поста

### PostPublisher

```python
from app.services.publisher import PostPublisher

publisher = PostPublisher(telethon_client, session)

# Публикация всех запланированных
await publisher.publish_scheduled_posts()

# Публикация конкретного поста
await publisher.publish_post(post)
```

**Методы:**
- `publish_scheduled_posts()` - публикация всех `status=scheduled`
- `publish_post(post)` - публикация конкретного поста
- `_send_text_only(text)` - только текст
- `_send_media_only(media_items)` - только медиа
- `_send_media_and_text(media_items, text)` - медиа + текст
- `_restore_input_media(media_item)` - восстановление InputMedia

### AI модуль

```python
from app import ai

# Рерайт текста
rewritten = ai.rewrite_text("Original text", max_retries=6)

# Текущая модель
model = ai.get_current_model()

# Ротация ключа
ai.rotate_key(mark_failed=True)

# Ротация модели
ai.rotate_model(mark_failed=True)
```

---

## База данных

### Схема

```sql
-- Источники
CREATE TABLE source (
    chat_id BIGINT PRIMARY KEY,
    username VARCHAR,
    title VARCHAR,
    join_link VARCHAR,
    is_active BOOLEAN DEFAULT TRUE,
    added_at TIMESTAMP DEFAULT NOW()
);

-- Очередь сообщений
CREATE TABLE message_queue (
    id SERIAL PRIMARY KEY,
    source_id BIGINT REFERENCES source(chat_id),
    message_id BIGINT NOT NULL,
    grouped_id BIGINT,
    original_text TEXT,
    rewritten_text TEXT,
    media_type VARCHAR(20),
    media_file_id BIGINT,
    media_access_hash BIGINT,
    media_file_reference BYTEA,
    rewrite_status VARCHAR(20) DEFAULT 'pending',
    awaiting_text BOOLEAN DEFAULT FALSE,
    awaiting_until TIMESTAMP,
    ready_to_post BOOLEAN DEFAULT FALSE,
    collected_at TIMESTAMP DEFAULT NOW(),
    rewritten_at TIMESTAMP
);

-- Посты
CREATE TABLE post (
    id SERIAL PRIMARY KEY,
    grouped_id BIGINT,
    original_source_id BIGINT,
    final_text TEXT,
    status VARCHAR(20) DEFAULT 'scheduled',
    scheduled_at TIMESTAMP DEFAULT NOW(),
    posted_at TIMESTAMP,
    post_error TEXT
);

-- Медиа постов
CREATE TABLE post_media (
    id SERIAL PRIMARY KEY,
    post_id INTEGER REFERENCES post(id) ON DELETE CASCADE,
    message_queue_id INTEGER REFERENCES message_queue(id),
    media_type VARCHAR(20) NOT NULL,
    media_file_id BIGINT NOT NULL,
    media_access_hash BIGINT NOT NULL,
    media_file_reference BYTEA,
    order_num INTEGER DEFAULT 0
);
```

### Индексы

```sql
CREATE INDEX idx_message_queue_source_id ON message_queue(source_id);
CREATE INDEX idx_message_queue_grouped_id ON message_queue(grouped_id);
CREATE INDEX idx_message_queue_rewrite_status ON message_queue(rewrite_status);
CREATE INDEX idx_message_queue_awaiting_text ON message_queue(awaiting_text);
CREATE INDEX idx_message_queue_ready_to_post ON message_queue(ready_to_post);
CREATE INDEX idx_message_queue_collected_at ON message_queue(collected_at);
CREATE INDEX idx_post_status ON post(status);
CREATE INDEX idx_post_scheduled_at ON post(scheduled_at);
```

### Миграции

```bash
# Создать миграцию
alembic revision --autogenerate -m "Description"

# Применить все миграции
alembic upgrade head

# Откатить последнюю
alembic downgrade -1

# Показать текущую версию
alembic current

# История миграций
alembic history
```

---

## Скрипты управления

Подробная документация в `scripts/README.md` и `scripts/CHEATSHEET.md`.

### Быстрые команды

```bash
# Показать все каналы
python -m scripts.fetch_channel_info --list

# Добавить по username
python -m scripts.add_sources_from_links @channel

# Добавить по ID
echo "-1001234567890" > temp.txt
python -m scripts.add_sources_from_ids temp.txt

# Обновить метаданные
python -m scripts.fetch_channel_info --all
```

---

## Тестирование

### Установка зависимостей

```bash
pip install -r requirements-test.txt
```

### Запуск тестов

```bash
# Все тесты
pytest

# С покрытием
pytest --cov=app --cov-report=html

# Только юнит-тесты
pytest tests/unit/

# Конкретный файл
pytest tests/unit/test_collector.py

# Конкретный тест
pytest tests/unit/test_collector.py::TestMessageCollector::test_collect_album_with_text

# С подробным выводом
pytest -vv

# Остановиться на первой ошибке
pytest -x
```

### Покрытие кода

```bash
pytest --cov=app --cov-report=html
open htmlcov/index.html
```

### Созданные тесты

- ✅ `test_collector.py` - MessageCollector (8 тестов)
- ✅ `test_processor.py` - MessageProcessor (8 тестов)
- ✅ `test_publisher.py` - PostPublisher (7 тестов)

---

## Deployment

### Systemd Service (Linux)

Создай `/etc/systemd/system/telegram-bot.service`:

```ini
[Unit]
Description=Telegram Content Aggregator Bot
After=network.target postgresql.service

[Service]
Type=simple
User=botuser
WorkingDirectory=/home/botuser/telegram-content-bot
Environment="PATH=/home/botuser/telegram-content-bot/venv/bin"
ExecStart=/home/botuser/telegram-content-bot/venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Запуск:
```bash
sudo systemctl daemon-reload
sudo systemctl enable telegram-bot
sudo systemctl start telegram-bot
sudo systemctl status telegram-bot
```

### Docker (будущая версия)

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
CMD ["python", "main.py"]
```

### Мониторинг

#### Logrotate

```bash
/home/botuser/telegram-content-bot/logs/*.log {
    daily
    rotate 7
    compress
    delaycompress
    notifempty
    create 0640 botuser botuser
}
```

#### Алерты

Настрой мониторинг через:
- **Prometheus** + Grafana
- **Sentry** для ошибок
- **Telegram** для критических алертов

---

## Troubleshooting

### Ошибка: "Connection refused" (PostgreSQL)

**Причина:** PostgreSQL не запущен или неверный host.

**Решение:**
```bash
# Проверь статус
sudo systemctl status postgresql

# Запусти
sudo systemctl start postgresql

# Проверь DATABASE_URL в .env
```

### Ошибка: "FloodWaitError"

**Причина:** Telegram API лимит.

**Решение:**
- Уменьши количество источников
- Увеличь интервалы между запросами
- Подожди указанное время

### Ошибка: "FILE_REFERENCE_EXPIRED"

**Причина:** `file_reference` устарел (Telegram меняет каждые ~1 час).

**Решение:**
- Бот автоматически обрабатывает через re-fetch
- Если проблема - используй forward вместо send_file

### Ошибка: "AI API limit exceeded"

**Причина:** Исчерпан лимит API ключа.

**Решение:**
- Добавь несколько ключей через запятую в .env
- Используй разные модели (дешевле)
- Настрой ротацию ключей

### Посты не публикуются

**Проверь:**
```sql
-- Есть ли запланированные посты?
SELECT * FROM post WHERE status = 'scheduled';

-- Есть ли ошибки?
SELECT id, post_error FROM post WHERE status = 'failed';

-- Корректен ли DEST?
SELECT value FROM settings WHERE key = 'dest';
```

### Логи

```bash
# Все ошибки
grep ERROR logs/bot_work.log

# Последние 100 строк
tail -n 100 logs/bot_work.log

# Реальное время
tail -f logs/bot_work.log
```

---

## Полезные ссылки

- [Документация Telethon](https://docs.telethon.dev/)
- [SQLAlchemy 2.0](https://docs.sqlalchemy.org/en/20/)
- [Alembic](https://alembic.sqlalchemy.org/)
- [OpenRouter Models](https://openrouter.ai/models)
- [PostgreSQL Docs](https://www.postgresql.org/docs/)

---

## Лицензия

MIT License

---

## История изменений

### v1.3.0 (Январь 2026) - catch_up=True (Текущая)

**Проблема:** Альбомы теряли фото (3 из 6) от некоторых источников из-за DC splits.

**Решение:**
- ✅ Добавлен `catch_up=True` - восстанавливает пропущенные updates
- ✅ Упрощена архитектура: 1 handler вместо 3
- ✅ Ручной буфер 3 секунды (вместо events.Album)
- ✅ Убран `sequential_updates=True` (ускорение)
- ✅ Проверено: старый бот потерял фото, новый поймал все

**Технические детали:**
- Один `message_handler` для всех сообщений
- Debounce pattern: таймер сбрасывается при каждом фото
- `catch_up=True` запрашивает пропущенные updates у Telegram
- Удалены: background_album_completion_checker, fallback handler

### v1.1.0 (Январь 2026) - Улучшенная обработка альбомов

**Проблема:** Альбомы иногда теряли фотографии из-за проблем с таймингом.

**Решение:**
- ✅ Переход на `events.Album` - Telethon автоматически собирает все медиа
- ✅ Сбор **всех** текстов из всех сообщений альбома
- ✅ Удалены таймеры и блокировки - упрощенная архитектура
- ✅ Быстрая обработка - интервал PostBuilder 5с → 3с

---

## Контакты

Вопросы и предложения: [GitHub Issues](https://github.com/yourusername/telegram-content-bot/issues)

---

**Версия:** 1.3.0
**Дата:** Январь 2026

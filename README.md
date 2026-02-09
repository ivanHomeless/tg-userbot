# Telegram Content Aggregator Bot

Userbot для автоматического сбора контента из Telegram-каналов, рерайта текста через AI и публикации в целевой канал.

Работает как userbot на [Telethon](https://github.com/LonamiWebs/Telethon) — не требует создания бота через BotFather.

## Как это работает

```
Каналы-источники → Сбор сообщений → Очередь (PostgreSQL) → AI-рерайт → Публикация
```

1. **Сбор** — подписка на `NewMessage` из каналов-источников. Альбомы (несколько фото с одним `grouped_id`) буферизируются 3 секунды и собираются в единый пост.
2. **Рерайт** — текст переписывается через AI (OpenRouter / DeepSeek / Gemini). Для альбомов тексты объединяются и рерайтятся один раз.
3. **Публикация** — готовые посты отправляются в целевой канал с заданным интервалом.

### Особенности

- **Альбомы** — корректная сборка фото из разных Data Center Telegram (обход известных багов Telethon)
- **Orphan media** — медиа без текста ждёт подпись до 20 секунд, затем публикуется как есть
- **Caption overflow** — если текст не влезает в caption, отправляется отдельным сообщением
- **Без скачивания файлов** — хранятся только file reference от Telegram, медиа не загружается на диск
- **Мульти-провайдер AI** — ротация ключей и fallback между провайдерами
- **Graceful shutdown** — корректное завершение по SIGINT/SIGTERM

## Требования

- Python 3.11+
- PostgreSQL 15+
- Docker (для БД) или локальный PostgreSQL
- Telegram API credentials ([my.telegram.org](https://my.telegram.org))
- API-ключ AI-провайдера

## Установка

```bash
# Клонирование
git clone https://github.com/<your-username>/tg-userbot.git
cd tg-userbot

# Автоматическая настройка (venv, зависимости, .env, миграции)
bash setup.sh

# Или вручную:
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Настройка

### 1. База данных

```bash
docker-compose up -d          # PostgreSQL на порту 5433
alembic upgrade head           # Применить миграции
```

### 2. Переменные окружения

Создайте `.env` в корне проекта:

```env
# Telegram (https://my.telegram.org)
API_ID=12345678
API_HASH=your_api_hash
PHONE=+79001234567
DEST=@your_channel

# PostgreSQL
DATABASE_URL=postgresql+asyncpg://botuser:password@localhost:5433/telegram_bot

# AI-провайдер: openrouter | deepseek | gemini
AI_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-...
MODEL=tngtech/deepseek-r1t2-chimera:free

# Опционально
POST_DELAY=10           # Интервал между постами (сек)
CAPTION_LIMIT=1024      # Лимит caption (1024 / 2048 для Premium)
```

### 3. Добавление каналов-источников

```bash
python -m scripts.add_sources_from_links @channel_name
python -m scripts.add_sources_from_ids data/sources_ids.txt
python -m scripts.fetch_channel_info --list    # Просмотр списка
```

## Запуск

```bash
source venv/bin/activate
python main.py
```

При первом запуске потребуется ввести код авторизации Telegram.

## Архитектура

```
main.py                          # Точка входа, graceful shutdown
app/
├── bot_logic.py                 # NewMessage handler + 4 фоновых задачи
├── ai.py                        # Мульти-провайдер AI с ротацией ключей
├── config.py                    # Конфигурация из .env
├── prompts.py                   # Промпт для рерайта
├── services/
│   ├── collector.py             # Сбор сообщений и альбомов
│   ├── processor.py             # Рерайт, сборка постов
│   └── publisher.py             # Публикация в канал
├── models/
│   ├── source.py                # Каналы-источники
│   ├── message.py               # Очередь сообщений
│   └── post.py                  # Посты и медиа
└── database/
    └── engine.py                # AsyncEngine + SessionLocal
```

### Фоновые задачи

| Задача | Интервал | Описание |
|--------|----------|----------|
| `background_rewriter` | 30 сек | AI-рерайт одиночных сообщений |
| `background_awaiting_closer` | 15 сек | Закрытие orphan-медиа по таймауту |
| `background_post_builder` | 3 сек | Сборка постов из очереди |
| `background_publisher` | 15 сек | Отправка постов в канал |

## AI-провайдеры

| Провайдер | Переменные |
|-----------|-----------|
| OpenRouter | `OPENROUTER_API_KEY`, `MODEL` |
| DeepSeek | `DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL` |
| Google Gemini | `GEMINI_API_KEY`, `GEMINI_MODEL` |

## Полезные SQL-запросы

```sql
-- Статус очереди
SELECT rewrite_status, COUNT(*) FROM message_queue GROUP BY rewrite_status;

-- Ожидающие текст
SELECT * FROM message_queue WHERE awaiting_text = true;

-- Запланированные посты
SELECT id, status, scheduled_at FROM post WHERE status = 'scheduled' ORDER BY scheduled_at;

-- Ошибки публикации
SELECT id, post_error FROM post WHERE status = 'failed';
```

## Стек

- **Telethon** — Telegram MTProto client
- **SQLAlchemy 2.0** (async) + **asyncpg** — ORM и драйвер PostgreSQL
- **Alembic** — миграции БД
- **OpenAI SDK** / **Google GenAI** — клиенты AI-провайдеров

## Лицензия

MIT

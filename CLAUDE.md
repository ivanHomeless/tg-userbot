# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Telegram Content Aggregator Bot — collects messages from source channels, rewrites text via AI, publishes to a destination channel. Built as a userbot with Telethon.

**Pipeline:** MessageCollector → MessageQueue (DB) → MessageProcessor → Post/PostMedia (DB) → PostPublisher

## Commands

```bash
# Run
python main.py                    # Start bot (first run requires Telegram auth code)

# Database
docker-compose up -d              # Start PostgreSQL (port 5433)
alembic upgrade head              # Apply all migrations
alembic revision --autogenerate -m "description"  # New migration
alembic downgrade -1              # Rollback one migration
docker-compose exec db psql -U botuser -d telegram_bot  # Access psql

# Sources
python -m scripts.add_sources_from_links @channel_name
python -m scripts.add_sources_from_ids data/sources_ids.txt
python -m scripts.fetch_channel_info --list

# Tests (tests/ directory exists but has no tests yet)
pytest
pytest -x                         # Stop on first failure
```

## Architecture

### Data Flow

```
Source Channels → events.NewMessage() handler
  ├─ grouped_id → 3s debounce buffer → collect_album()
  └─ no grouped_id → collect_message()
  ↓
MessageQueue table (rewrite_status: pending→processing→done/failed/skipped)
  ↓ 4 background tasks
  ├─ background_rewriter()        — 30s interval, AI rewrites single messages
  ├─ background_awaiting_closer() — 15s interval, closes orphan media waits
  ├─ background_post_builder()    — 3s interval, assembles posts (asyncio.Lock)
  └─ background_publisher()       — 15s interval, sends to DEST channel
  ↓
Post + PostMedia tables → Destination Channel
```

### Key Files

- `main.py` — Entry point, signal handling, graceful shutdown
- `app/bot_logic.py` — Single NewMessage handler + 4 background tasks, album buffering logic
- `app/services/collector.py` — `collect_album()` and `collect_message()`, orphan media linking
- `app/services/processor.py` — Rewriter, awaiting closer, post builder background tasks
- `app/services/publisher.py` — Sends posts to destination, handles caption overflow and file reference expiry
- `app/ai.py` — Multi-provider AI (OpenRouter/DeepSeek/Gemini) with key rotation and fallback
- `app/models/` — SQLAlchemy models: Source, MessageQueue, Post, PostMedia
- `app/database/engine.py` — AsyncEngine + SessionLocal factory
- `app/config.py` — Loads .env variables
- `app/prompts.py` — AI rewrite prompt

### Album Handling (Critical Path)

Telegram sends album photos as separate messages with the same `grouped_id`, potentially from different Data Centers.

**Current solution (v1.3.0):** Single NewMessage handler + `catch_up=True`
- Messages with `grouped_id` buffer in `album_buffers` dict for 3 seconds (debounce — timer resets on each new photo)
- After 3s silence → `collect_album()` processes all buffered photos
- `TelegramClient(..., catch_up=True)` recovers updates missed due to DC splits
- Do NOT use `events.Album` — Telethon's AlbumHack is unreliable with DC splits (GitHub #4075, #4426, #1479)
- Duplicate protection via DB `message_id` unique constraint

### Orphan Media Pattern

Media can arrive without caption; caption may follow 10-20s later.
1. Media without text → `awaiting_text=True`, `awaiting_until=now+20s`
2. Text arrives within window → linked via `_link_orphan_media()`
3. Timeout expires → AwaitingCloser closes it with default caption

### Media Storage

No file downloads — stores Telegram file references only (`media_file_id`, `media_access_hash`, `media_file_reference`). Publisher reconstructs `InputMedia` objects. `file_reference` expires ~24h; publisher catches `FILE_REFERENCE_EXPIRED`.

### Rewrite Strategy

- **Single messages:** Rewritten by `background_rewriter` (`rewrite_status='done'`)
- **Albums:** Individual items skip rewrite (`rewrite_status='skipped'`), combined text rewritten once during post assembly

### Caption Overflow

If text > `CAPTION_LIMIT` (default 1024): send media without caption, then text as separate message (`publisher.py`).

## Configuration (.env)

Required: `API_ID`, `API_HASH`, `PHONE`, `DEST`, `DATABASE_URL`, `AI_PROVIDER`, API key for chosen provider, `MODEL`

Optional: `POST_DELAY` (10), `CAPTION_LIMIT` (1024), `AWAIT_TEXT_TIMEOUT` (20)

## Patterns

- Always use async session context managers: `async with SessionLocal() as session:`
- PostBuilder uses `asyncio.Lock()` — never remove this, prevents duplicate post creation
- Graceful shutdown (SIGINT/SIGTERM): cancels background tasks, disconnects client, disposes engine
- New DB fields require Alembic migration (`alembic revision --autogenerate`)

## Debugging

```sql
SELECT rewrite_status, COUNT(*) FROM message_queue GROUP BY rewrite_status;
SELECT * FROM message_queue WHERE awaiting_text = true;
SELECT id, status, scheduled_at FROM post WHERE status = 'scheduled' ORDER BY scheduled_at;
SELECT id, post_error FROM post WHERE status = 'failed';
```

Logs: `logs/bot_work.log` (file + console). Session: `data/userbot_session`.

## Environment

- Python 3.11+, PostgreSQL 15+ (asyncpg), Telethon 1.42.0
- Dependencies in `requirements.txt`

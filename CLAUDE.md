# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a **Telegram Content Aggregator Bot** that collects messages from multiple source channels, processes them through AI for text rewriting, and publishes to a destination channel. Built as a userbot using Telethon.

**Architecture Pattern:** Async pipeline with PostgreSQL as message queue
- MessageCollector → MessageQueue (DB) → MessageProcessor → Post/PostMedia (DB) → PostPublisher

## Documentation Strategy

⚠️ **ВАЖНО: Всегда добавляй `use context7` при работе с:**
- Telethon API (events, methods, errors)
- PostgreSQL (queries, optimization)
- SQLAlchemy 
- Redis (commands, patterns)
- Любые внешние библиотеки

### Шаблоны промптов

**Telethon:**

## Key Development Commands

### Running the Bot
```bash
python main.py                    # Start bot (first run requires Telegram auth code)
```

### Database Management
```bash
# Migrations
alembic upgrade head              # Apply all migrations
alembic revision --autogenerate -m "description"  # Create new migration
alembic downgrade -1              # Rollback one migration
alembic current                   # Show current version

# Docker PostgreSQL
docker-compose up -d              # Start PostgreSQL (port 5433)
docker-compose down               # Stop PostgreSQL
docker-compose exec db psql -U botuser -d telegram_bot  # Access psql
```

### Source Management
```bash
# Add sources by username/link
python -m scripts.add_sources_from_links @channel_name
python -m scripts.add_sources_from_links https://t.me/channel

# Add sources by ID
python -m scripts.add_sources_from_ids data/sources_ids.txt

# View channel info
python -m scripts.fetch_channel_info --list        # List all channels
python -m scripts.fetch_channel_info -1001234567890  # Check specific channel
```

### Testing
```bash
pytest                                      # Run all tests
pytest --cov=app --cov-report=html          # With coverage report
pytest tests/unit/test_collector.py        # Specific test file
pytest -x                                   # Stop on first failure
```

## Architecture Deep Dive

### Data Flow Pipeline

```
Source Channels (via Telethon events)
    ↓
events.NewMessage() → message_handler
    ├─ grouped_id? → буфер 3 секунды → collect_album()
    └─ no grouped_id? → collect_message() сразу
    ↓ Saves to DB
MessageQueue table (original_text, media_file_id, rewrite_status)
    ↓ Processed by 4 background tasks
MessageProcessor (app/services/processor.py)
    ├─ Rewriter: AI rewrites text (30s interval)
    ├─ AwaitingCloser: Closes orphan media waits (15s interval)
    └─ PostBuilder: Assembles posts from ready messages (3s interval, uses asyncio.Lock)
    ↓ Creates
Post + PostMedia tables (final_text, status='scheduled')
    ↓ Published by
PostPublisher (app/services/publisher.py)
    ↓ Sends to
Destination Channel (DEST env var)
```

### Critical Background Tasks

**4 parallel asyncio tasks:**
1. `background_rewriter()` - Every 30s, AI rewrites text for single messages (NOT albums)
2. `background_awaiting_closer()` - Every 15s, closes expired media waiting for text
3. `background_post_builder()` - Every 3s, builds posts (protected by asyncio.Lock)
4. `background_publisher()` - Every 15s, publishes scheduled posts to destination

**Important:** PostBuilder uses `asyncio.Lock()` to prevent race conditions. Fast 3s interval for quick album processing.

### Album Handling (Media Groups)

**Challenge:** Telegram sends albums as separate messages with same `grouped_id`. Updates from different Data Centers may arrive separately or get lost.

**Solution: Manual buffering + catch_up**

1. **Single NewMessage handler** catches ALL messages (bot_logic.py:188-255)
2. Messages with `grouped_id` → buffered for **3 seconds**
3. Timer resets on each new photo (debounce pattern)
4. After 3s silence → process all buffered photos as album
5. **catch_up=True** parameter recovers missed updates from DC splits

**Key configuration:**
```python
TelegramClient(
    session,
    api_id,
    api_hash,
    catch_up=True  # Recovers missed updates from different DCs
)
```

**How it works:**
- Each photo with `grouped_id` → adds to `album_buffers[grouped_id]`
- Cancels previous timer, starts new 3s timer
- Timer expires → calls `collect_album()` with all buffered photos
- Duplicate protection via DB `message_id` check

**Key files:**
- Album handler: app/bot_logic.py:188-255
- Album collection: app/services/collector.py:31-118
- Album assembly: app/services/processor.py:157-182

**Why catch_up=True solved the problem:**
- Telethon's AlbumHack has known issues with DC splits (GitHub #4075, #4426, #1479)
- Different Data Centers send updates separately
- `catch_up=True` requests missed updates from Telegram servers
- Prevents lost photos from cross-DC albums

### Orphan Media Pattern

**Problem:** Media can arrive without caption, but caption might come 10-20s later.

**Solution:**
1. Media without text: Set `awaiting_text=True`, `awaiting_until=now+20s`
2. If text arrives within window: Link via `_link_orphan_media()` (collector.py:272)
3. If timeout expires: AwaitingCloser sets `awaiting_text=False`, adds default caption

### Media Storage Strategy

**No file downloads** - stores only Telegram file references:
- `media_file_id` (BIGINT) - Telegram's file ID
- `media_access_hash` (BIGINT) - Access hash for API calls
- `media_file_reference` (BYTEA) - Reference that expires every ~1 hour

**Why:** Avoids large binary storage, leverages Telegram's CDN. Publisher reconstructs `InputMedia` objects from these fields.

**Gotcha:** `file_reference` expires - publisher catches `FILE_REFERENCE_EXPIRED` and re-fetches.

## Database Models (app/models/)

### Source (source.py)
Input channels/groups. Fields: `chat_id` (PK), `username`, `title`, `is_active`, `join_link`.

### MessageQueue (message.py)
Temporary processing queue. Key fields:
- `rewrite_status`: 'pending' → 'processing' → 'done'/'failed'/'skipped'
- `grouped_id`: Groups album items (NULL for singles)
- `awaiting_text`: Boolean flag for orphan media
- `ready_to_post`: TRUE when ready for post assembly

**Indexes:** Optimized for common queries (rewrite_status='pending', awaiting_text=True, grouped_id lookups)

### Post (post.py)
Ready-to-publish posts. Fields: `final_text`, `status` ('scheduled'→'posted'/'failed'), `scheduled_at`.

### PostMedia (post.py)
Media attached to posts. Fields: `media_type`, `media_file_id`, `media_access_hash`, `order_num` (for albums).

## AI Integration (app/ai.py)

**Multiple provider support with automatic rotation:**
- OpenRouter (default) - Access to Claude, DeepSeek, etc.
- DeepSeek - Direct API
- Google Gemini - Alternative

**Key features:**
- Multiple API keys per provider (comma-separated in .env)
- Automatic key rotation on rate limits
- Fallback to different models on failure
- Tracks failed (key, model) combinations to avoid retries

**Usage in processor:**
```python
from app import ai
rewritten = ai.rewrite_text(original_text)  # Handles retries automatically
```

## Configuration (.env)

### Required Variables
```env
# Telegram (get from https://my.telegram.org)
API_ID=12345678
API_HASH=abcdef123456
PHONE=+1234567890
DEST=-1001234567890              # Destination channel ID

# Database
DATABASE_URL=postgresql+asyncpg://botuser:password@localhost:5433/telegram_bot

# AI Provider (openrouter | deepseek | google)
AI_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-v1-xxx
MODEL=tngtech/deepseek-r1t2-chimera:free
```

### Optional Variables
```env
POST_DELAY=10                    # Seconds between posts
CAPTION_LIMIT=1024               # 2048 for Telegram Premium
AWAIT_TEXT_TIMEOUT=20            # Seconds to wait for orphan media text
```

## Important Patterns & Gotchas

### 1. Async Session Management
Always use context managers:
```python
async with SessionLocal() as session:
    collector = MessageCollector(session)
    await collector.collect_message(event)
# Session auto-closes, releases connection to pool
```

### 2. Album Processing - Manual Buffering
**Manual album collection with 3s debounce timer:**

1. Each message with `grouped_id` → added to buffer `album_buffers[grouped_id]`
2. Timer cancels previous, starts new (3 seconds) - **debounce pattern**
3. When timer expires → process all buffered messages as album
4. Creates `AlbumEvent` object for `collect_album()`

**Why 3 seconds:**
- Photos arrive simultaneously (0-100ms) when from same DC
- 3s buffer catches stragglers from different DCs
- `catch_up=True` recovers truly missed updates

**Benefits:**
- ✅ Never loses photos (catch_up recovers missed updates)
- ✅ Collects ALL captions from ALL messages
- ✅ Full control over timing
- ✅ Fast processing (3s vs old 20s)

**Critical code:** app/bot_logic.py:188-255, app/services/collector.py:31-118

### 3. File Reference Expiry
Telegram changes `file_reference` every ~24 hours. Publisher catches `FILE_REFERENCE_EXPIRED` and should re-fetch or use forwarding for old media.

### 4. Caption Length Overflow
If rewritten text > CAPTION_LIMIT (1024 chars):
- Send media without caption
- Send text as separate message
(app/services/publisher.py:135-173)

### 5. Race Condition Protection
`background_post_builder()` uses `asyncio.Lock()` to ensure only one instance runs at a time, preventing duplicate post creation during slow operations.

### 6. Rewrite Strategy
- **Single messages:** Rewritten immediately by background_rewriter
- **Albums:** Individual messages skip rewrite (`rewrite_status='skipped'`), combined text rewritten once during post assembly

### 7. Graceful Shutdown
On SIGINT/SIGTERM:
1. Cancel all background tasks (asyncio.CancelledError)
2. Disconnect Telethon client
3. Close PostgreSQL connection pool (engine.dispose())

(main.py:44-91)

## Testing Architecture

**Test files:** tests/unit/test_{collector,processor,publisher}.py

**Fixtures (tests/conftest.py):**
- `async_session` - In-memory SQLite for tests
- `mock_client` - Mocked Telethon client
- Event mocks for different message types

**Run specific test:**
```bash
pytest tests/unit/test_collector.py::TestMessageCollector::test_collect_album_with_text -vv
```

## Common Debugging Queries

```sql
-- Message queue status
SELECT rewrite_status, COUNT(*) FROM message_queue GROUP BY rewrite_status;

-- Orphan media waiting for text
SELECT source_id, message_id, awaiting_until FROM message_queue
WHERE awaiting_text = true;

-- Scheduled posts
SELECT id, status, scheduled_at FROM post
WHERE status = 'scheduled' ORDER BY scheduled_at;

-- Failed posts with errors
SELECT id, post_error, posted_at FROM post WHERE status = 'failed';

-- Album assembly status
SELECT grouped_id, COUNT(*), MAX(collected_at) FROM message_queue
WHERE grouped_id IS NOT NULL AND ready_to_post = false
GROUP BY grouped_id;
```

## Deployment Notes

**Production systemd service:** See README.md deployment section for systemd unit file.

**Critical environment:**
- Python 3.11+ required
- PostgreSQL 15+ (asyncpg driver)
- Telethon session persists in `data/userbot_session` after first auth

**Monitoring:**
- Logs: `logs/bot_work.log` (dual output: file + console)
- Format: `[%(levelname)s] %(name)s: %(message)s`
- Use `grep ERROR logs/bot_work.log` for quick debugging

## Code Organization

```
app/
├── bot_logic.py         # Orchestrator: 1 universal handler + 4 background tasks
│                        #   - @client.on(events.NewMessage()) → handles ALL messages
│                        #   - Manual album collection with album_buffers + timers
│                        #   - DEST channel ID filtering
├── services/
│   ├── collector.py     # Ingests messages: collect_album() + collect_message()
│   ├── processor.py     # 3 background tasks: rewrite, close waits, build posts
│   └── publisher.py     # Sends posts to destination channel
├── models/              # 4 SQLAlchemy models (Source, MessageQueue, Post, PostMedia)
├── database/
│   └── engine.py        # AsyncEngine + SessionLocal factory
├── ai.py                # Multi-provider AI with rotation
├── config.py            # Loads .env variables
└── prompts.py           # AI rewrite instructions (military equipment focus)
```

## When Making Changes

**Before editing:**
1. Read relevant files first (don't guess at implementation)
2. Understand the async pipeline flow
3. Check if change affects album handling (critical path)

**Common change patterns:**
- New AI provider: Add to app/ai.py, update config.py
- New message type: Update collector.py logic branches
- Change post scheduling: Modify publisher.py timing logic
- New database field: Create Alembic migration, update model

**Testing changes:**
1. Write unit test in tests/unit/
2. Test with real bot: `python main.py`
3. Monitor logs: `tail -f logs/bot_work.log`
4. Check DB state with debugging queries above

## Recent Changes (Jan 2026)

### v1.3.0 - catch_up=True Solution (Current)

**Problem:** Albums still losing photos (3 out of 6) from certain sources despite three-tier architecture.

**Root cause:** Telegram doesn't send updates from different Data Centers consistently. Telethon's `events.Album` uses "dirty hack" (AlbumHack class) that fails with DC splits.

**Solution - Simplified with catch_up:**

1. **Removed complexity:**
   - ❌ Removed `events.Album()` handler (unreliable)
   - ❌ Removed separate fallback handler (duplicated logic)
   - ❌ Removed `background_album_completion_checker()` (unnecessary)
   - ❌ Removed `sequential_updates=True` (slows down processing)

2. **Single NewMessage handler:**
   - ✅ One handler for ALL messages
   - ✅ Buffers photos with `grouped_id` for **3 seconds**
   - ✅ Debounce pattern: timer resets on each new photo
   - ✅ Processes complete album after 3s silence

3. **Added catch_up=True:**
   ```python
   TelegramClient(session, api_id, api_hash, catch_up=True)
   ```
   - ✅ Requests missed updates from Telegram servers
   - ✅ Recovers photos lost in DC splits
   - ✅ Official Telethon solution for missed updates

**Results:**
- ✅ **Old bot (without catch_up):** Lost 1 photo from test album
- ✅ **New bot (with catch_up):** Caught ALL photos from same album
- ✅ Faster processing: 3s buffer vs 20s
- ✅ Simpler code: 1 handler vs 3

**Why it works:**
- Photos arrive simultaneously (~0-100ms) from same DC
- Photos from different DCs may arrive separately or get lost
- `catch_up=True` tells Telegram to resend missed updates
- 3s buffer catches stragglers, `catch_up` catches truly lost ones

**Files changed:**
- app/bot_logic.py - Simplified to 1 handler, added catch_up=True, 3s timer
- app/services/collector.py - Updated comments
- CLAUDE.md - Rewritten architecture docs

**References:**
- Telethon GitHub #4075, #4426, #1479 - Known Album event issues
- Telethon docs: AlbumHack described as "dirty hack" for DC splits

### v1.2.0 - Three-Tier Album Handling (Deprecated)

**Problem:** Albums sometimes lost first message (9/10 photos received).

**Root cause:** Manual buffering approach had race conditions. First message caught by NewMessage → saved to DB → timer processed remaining 9 → duplicate detection rejected first message on retry.

**Solution - Three-tier hybrid:**

1. **Restored events.Album as PRIMARY:**
   - Telethon's atomic collection (registers first, fires first)
   - `sequential_updates=True` ensures deterministic handler order
   - Handles 95% of albums perfectly

2. **Added FALLBACK handler:**
   - `@client.on(events.NewMessage(func=lambda e: e.grouped_id))`
   - Only triggers if Album event missed (DC splits, timing issues)
   - 5-second buffer catches slow arrivals
   - DB check prevents duplicates

3. **Added SAFETY NET:**
   - Background completion checker (every 10s)
   - Marks albums older than 30s as ready
   - Recovers truly stuck cases

4. **Enhanced diagnostics:**
   - All handlers log message IDs and counts
   - Time span tracking shows arrival patterns
   - Race condition detection alerts
   - Duplicate warnings identify conflicts

**Benefits:**
- ✅ **Never loses first message** - Album handler fires before NewMessage
- ✅ Handles Telethon edge cases (DC splits, timing)
- ✅ Recovers stuck albums automatically
- ✅ Full diagnostic visibility

**Files changed:**
- app/bot_logic.py - Restored Album handler + fallback + checker
- app/services/collector.py - Enhanced logging
- CLAUDE.md - Updated architecture docs

### v1.1.0 - Album Processing Refactor

**Problem:** Albums sometimes lost photos due to timing issues.

**Solution:** Migrated to `events.Album` + DEST channel filtering.
- ✅ Faster processing (no artificial timeouts)
- ✅ Simpler code (removed `_album_locks`, `_album_timers`, `ALBUM_BUILD_TIMEOUT`)
- ✅ All captions collected from multi-text albums

**Files changed:**
- app/bot_logic.py - Two handlers, DEST filtering
- app/services/collector.py - New `collect_album()`, simplified album logic
- app/services/processor.py - Removed timeout waiting for albums

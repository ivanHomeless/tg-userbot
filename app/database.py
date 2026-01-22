import sqlite3
import time
from app.config import DB_PATH

def db_init():
    with sqlite3.connect(DB_PATH) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS seen (
                chat_id INTEGER, msg_id INTEGER, ts INTEGER,
                PRIMARY KEY (chat_id, msg_id)
            )
        """)

def is_seen(chat_id: int, msg_id: int) -> bool:
    with sqlite3.connect(DB_PATH) as con:
        cur = con.cursor()
        cur.execute("SELECT 1 FROM seen WHERE chat_id=? AND msg_id=? LIMIT 1", (chat_id, msg_id))
        return cur.fetchone() is not None

def mark_seen(chat_id: int, msg_id: int):
    with sqlite3.connect(DB_PATH) as con:
        con.execute("INSERT OR IGNORE INTO seen VALUES(?,?,?)", (chat_id, msg_id, int(time.time())))
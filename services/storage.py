import json
from aiogram.fsm.storage.base import BaseStorage, StorageKey
from aiogram.fsm.state import State
from database.db import get_db


class SQLiteStorage(BaseStorage):
    @staticmethod
    def key(key: StorageKey):
        return json.dumps([key.bot_id, key.chat_id, key.user_id, key.thread_id,
                           key.business_connection_id, key.destiny])

    async def set_state(self, key, state=None):
        value = state.state if isinstance(state, State) else state
        db = await get_db()
        await db.execute("INSERT INTO fsm_state(key,state) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET state=excluded.state",
                         (self.key(key), value))

    async def get_state(self, key):
        db = await get_db()
        cur = await db.execute("SELECT state FROM fsm_state WHERE key=?", (self.key(key),))
        row = await cur.fetchone()
        return row[0] if row else None

    async def set_data(self, key, data):
        db = await get_db()
        await db.execute("INSERT INTO fsm_state(key,data) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data",
                         (self.key(key), json.dumps(data)))

    async def get_data(self, key):
        db = await get_db()
        cur = await db.execute("SELECT data FROM fsm_state WHERE key=?", (self.key(key),))
        row = await cur.fetchone()
        return json.loads(row[0]) if row else {}

    async def close(self):
        pass  # application owns the shared database connection

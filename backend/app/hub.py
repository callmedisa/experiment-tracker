"""One LISTEN connection per API process. NOTIFY is only a wake-up (and carries duplicate_batch info);
streams always read the rows from the database by seq."""
import asyncio
import json
import logging
from collections import deque

import asyncpg

log = logging.getLogger("tracker.hub")
CHANNEL = "run_events"  # one fixed lower-case channel: run ids are mixed case and must not be identifiers


class Sub:
    def __init__(self):
        self.wake = asyncio.Event()
        self.dups: deque = deque(maxlen=100)  # live-only, informational; stats.duplicates_ignored is authoritative


class Hub:
    def __init__(self, database_url: str):
        self.dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")
        self.subs: dict[str, set[Sub]] = {}
        self._task: asyncio.Task | None = None
        self.conn: asyncpg.Connection | None = None
        self.reconnects = 0

    async def subscribe(self, run_id: str) -> Sub:
        if self._task is None:
            self._task = asyncio.create_task(self._run())
        sub = Sub()
        self.subs.setdefault(run_id, set()).add(sub)
        return sub

    def unsubscribe(self, run_id: str, sub: Sub) -> None:
        s = self.subs.get(run_id)
        if s:
            s.discard(sub)
            if not s:
                self.subs.pop(run_id, None)

    def _on_notify(self, _conn, _pid, _channel, payload: str) -> None:
        try:
            data = json.loads(payload)
        except ValueError:
            return
        for sub in self.subs.get(data.get("r"), ()):
            if data.get("dup"):
                sub.dups.append(data["dup"])
            sub.wake.set()

    def _wake_all(self) -> None:
        for subs in self.subs.values():
            for s in subs:
                s.wake.set()

    async def _run(self) -> None:
        while True:
            try:
                self.conn = await asyncpg.connect(self.dsn)
                await self.conn.add_listener(CHANNEL, self._on_notify)
                self._wake_all()  # anything missed while we were away is read from the DB
                while not self.conn.is_closed():
                    await asyncio.sleep(0.5)
                log.warning("listener connection lost, reconnecting")
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("listener error %s", type(e).__name__)
            finally:
                if self.conn is not None and not self.conn.is_closed():
                    try:
                        await asyncio.wait_for(self.conn.close(), 2)
                    except Exception:  # noqa: BLE001
                        pass
            self.reconnects += 1
            await asyncio.sleep(0.5)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except BaseException:  # noqa: BLE001
                pass
            self._task = None

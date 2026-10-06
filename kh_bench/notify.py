"""Event notifications: JSON POST to webhooks (Node-RED, Teams/Power Automate, etc.).

Every event has the same shape so one Node-RED flow can route all of them:

    {"event": "run.passed", "text": "Bench 1 CH3: KH-0001 PASS - 30.0% SOC",
     "station": "Bench 1", "time": "2026-10-06T14:07:11", "channel": "CH3",
     "serial": "KH-0001", "result": "PASS", "run": {...full run record...}}

Delivery runs in the background with retries; a dead webhook never slows the bench.
"""

import asyncio
import json
import logging
import urllib.request
from collections import deque
from typing import Callable, Optional

from .config import Notifications, Webhook
from .db import now_iso

log = logging.getLogger("kh_bench.notify")

RETRIES = 3
TIMEOUT_S = 5


def _post(url: str, body: bytes) -> int:
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "kh-battery-bench"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        return r.status


class Notifier:
    def __init__(self, get_settings: Callable[[], Notifications]):
        self.get_settings = get_settings
        self.history: deque = deque(maxlen=50)   # recent deliveries, shown on the Setup page
        self._tasks: set[asyncio.Task] = set()
        self._order: dict[str, asyncio.Lock] = {}   # per-URL: deliver in the order emitted
        self.sender = _post                      # swappable for tests

    def emit(self, event: str, text: str, **data) -> dict:
        """Queue an event for every matching webhook. Safe to call from the event loop."""
        cfg = self.get_settings()
        payload = {"event": event, "text": f"{cfg.station} {text}".strip(),
                   "station": cfg.station, "time": now_iso(), **data}
        log.info("%s | %s", event, payload["text"])
        for hook in cfg.webhooks:
            if hook.enabled and ("*" in hook.events or event in hook.events):
                self._spawn(self._deliver(hook, payload))
        return payload

    async def send_test(self, hook: Webhook) -> dict:
        cfg = self.get_settings()
        payload = {"event": "test", "text": f"{cfg.station} test message from KH Battery Bench",
                   "station": cfg.station, "time": now_iso()}
        return await self._deliver(hook, payload)

    def _spawn(self, coro) -> None:
        try:
            t = asyncio.get_running_loop().create_task(coro)
        except RuntimeError:
            return  # no loop (e.g. sync unit test) - nothing to deliver on
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    async def _deliver(self, hook: Webhook, payload: dict) -> dict:
        lock = self._order.setdefault(hook.url, asyncio.Lock())
        async with lock:  # asyncio.Lock is FIFO, so events arrive in emit order
            return await self._send(hook, payload)

    async def _send(self, hook: Webhook, payload: dict) -> dict:
        body = json.dumps(payload, default=str).encode()
        err: Optional[str] = None
        for attempt in range(1, RETRIES + 1):
            try:
                status = await asyncio.to_thread(self.sender, hook.url, body)
                if 200 <= status < 300:
                    err = None
                    break
                err = f"HTTP {status}"
            except Exception as e:
                err = str(e) or e.__class__.__name__
            if attempt < RETRIES:
                await asyncio.sleep(2 ** (attempt - 1))
        rec = {"time": now_iso(), "hook": hook.name, "event": payload["event"],
               "ok": err is None, "error": err}
        self.history.appendleft(rec)
        if err:
            log.warning("Webhook %s failed for %s: %s", hook.name, payload["event"], err)
        return rec

    async def drain(self, timeout: float = 10) -> None:
        if self._tasks:
            await asyncio.wait(list(self._tasks), timeout=timeout)

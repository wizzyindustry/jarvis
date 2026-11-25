"""Local WebSocket feed for the wallpaper.

- Binds to 127.0.0.1 only.
- A client must send {"type":"hello","token":...} within 3 s or it is dropped.
- One-way: nothing the page sends is acted on, apart from the hello.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
from typing import Any

from aiohttp import WSMsgType, web

from ..bus import Bus

log = logging.getLogger(__name__)

class UiServer:
    def __init__(self, bus: Bus, token: str, port: int, config_event: dict[str, Any]) -> None:
        self.bus = bus
        self.token = token
        self.port = port
        self.config_event = config_event
        self.clients: set[web.WebSocketResponse] = set()
        self._runner: web.AppRunner | None = None
        bus.subscribe(self._on_event)

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/ws", self._ws)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", self.port)
        await site.start()
        log.info("wallpaper feed on ws://127.0.0.1:%d/ws", self.port)

    async def stop(self) -> None:
        for ws in list(self.clients):
            await ws.close()
        if self._runner:
            await self._runner.cleanup()

    async def _ws(self, request: web.Request) -> web.StreamResponse:
        # Lively may serve the page from file:// or a WebView2 virtual host, so the origin can't be
        # pinned; the random token (which other web pages can't read) is the real check.
        origin = request.headers.get("Origin")
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=4096)
        await ws.prepare(request)
        try:
            msg = await asyncio.wait_for(ws.receive(), timeout=3)
            ok = False
            if msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    ok = data.get("type") == "hello" and hmac.compare_digest(str(data.get("token", "")), self.token)
                except (ValueError, AttributeError):
                    ok = False
            if not ok:
                log.warning("refused wallpaper connection (bad token) from origin %r", origin)
                await ws.close(code=4401, message=b"unauthorized")
                return ws
            log.info("wallpaper connected (origin %r)", origin)
        except asyncio.TimeoutError:
            await ws.close(code=4401, message=b"timeout")
            return ws

        self.clients.add(ws)
        try:
            await ws.send_json(self.config_event)
            await ws.send_json({"type": "feed_reset", "items": list(self.bus.feed_history)})
            await ws.send_json({"type": "state", "state": self.bus.state})
            if self.bus.last_stats:
                await ws.send_json({"type": "stats", **self.bus.last_stats})
            async for _ in ws:  # ignore anything else the page sends
                pass
        finally:
            self.clients.discard(ws)
        return ws

    def _on_event(self, event: dict[str, Any]) -> None:
        if not self.clients:
            return
        payload = json.dumps(event)
        for ws in list(self.clients):
            if ws.closed:
                self.clients.discard(ws)
                continue
            asyncio.ensure_future(self._send(ws, payload))

    async def _send(self, ws: web.WebSocketResponse, payload: str) -> None:
        try:
            await ws.send_str(payload)
        except (ConnectionError, RuntimeError):
            self.clients.discard(ws)

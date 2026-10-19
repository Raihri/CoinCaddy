"""ASGI middleware that caps request body size on selected paths before the body is parsed."""

import json
import re

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class BodySizeLimit:
    def __init__(self, app: ASGIApp, *, path_pattern: str, max_bytes: int):
        self.app, self.pattern, self.max_bytes = app, re.compile(path_pattern), max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST" or not self.pattern.fullmatch(scope["path"]):
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        declared = headers.get(b"content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > self.max_bytes):
            return await self._reject(send)

        # Buffer the (small, capped) body so a missing or false Content-Length cannot bypass the limit.
        chunks, total = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            total += len(body)
            if total > self.max_bytes:
                return await self._reject(send)
            chunks.append(body)
            if not message.get("more_body", False):
                break
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if replayed:
                return await receive()
            replayed = True
            return {"type": "http.request", "body": b"".join(chunks), "more_body": False}

        await self.app(scope, replay, send)

    async def _reject(self, send: Send) -> None:
        body = json.dumps({"detail": {"code": "too_large", "message": "The upload is too large (limit 2 MB)."}}).encode()
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})

from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from typing import Any
from urllib import error, request


@dataclass
class HttpError(Exception):
    status: int
    payload: Any
    raw_body: str


def request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | list[Any] | None = None,
    timeout_seconds: float = 10.0,
) -> Any:
    data = None
    request_headers = dict(headers or {})
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")

    req = request.Request(url, method=method.upper(), headers=request_headers, data=data)

    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8")
            if not body:
                return {}
            return json.loads(body)
    except error.HTTPError as exc:
        raw_body = exc.read().decode("utf-8")
        payload = _try_parse_json(raw_body)
        raise HttpError(status=exc.code, payload=payload, raw_body=raw_body) from exc
    except error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, socket.timeout):
            raise TimeoutError(f"Request timed out for {url}") from exc
        raise ConnectionError(f"Unable to connect to {url}: {reason}") from exc


def _try_parse_json(raw_body: str) -> Any:
    try:
        return json.loads(raw_body)
    except json.JSONDecodeError:
        return None

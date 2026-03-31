import argparse
import json
import logging
import os

import aiohttp
from aiohttp import web

logging.basicConfig(
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    level=logging.DEBUG if os.environ.get("DEBUG", "").lower() == "true" else logging.INFO,
)
logger = logging.getLogger("proxy")

TARGET_BASE_URL = os.environ.get("OPENAI_BASE_URL", "")

DROP_FIELDS = [
    f.strip()
    for f in os.environ.get(
        "DROP_FIELDS",
        "stream_options,parallel_tool_calls,service_tier",
    ).split(",")
    if f.strip()
]

SKIP_HEADERS = frozenset({
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
    "content-encoding",
})

DROP_HEADERS = frozenset({
    "originator",
    "accept",
})

SYSTEM_PROMPT = "You are a helpful coding assistant. Be precise, safe, and helpful."

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

FAILED_REQUEST_DUMP_PATH = "/tmp/last_failed_request.json"


def _has_valid_tool_calls(msg: dict) -> bool:
    for tc in msg.get("tool_calls", []):
        args = tc.get("function", {}).get("arguments")
        if isinstance(args, str):
            try:
                json.loads(args)
            except json.JSONDecodeError:
                return False
    return True


def _ensure_tool_call_ids(messages: list) -> None:
    pending_ids = []
    for msg in messages:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                if "id" not in tc:
                    tc["id"] = f"call_{id(tc)}"
                pending_ids.append(tc["id"])

    id_idx = 0
    for msg in messages:
        if msg.get("role") == "tool" and "tool_call_id" not in msg:
            if id_idx < len(pending_ids):
                msg["tool_call_id"] = pending_ids[id_idx]
                id_idx += 1
            else:
                msg["tool_call_id"] = f"call_{id(msg)}"


def _clean_messages(messages: list) -> list:
    bad_call_ids = set()
    for msg in messages:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            if not _has_valid_tool_calls(msg):
                for tc in msg.get("tool_calls", []):
                    if tc.get("id"):
                        bad_call_ids.add(tc["id"])

    if bad_call_ids:
        logger.info("Dropping %d tool calls with invalid arguments", len(bad_call_ids))

    cleaned = []
    for msg in messages:
        role = msg.get("role")

        if role == "system":
            msg["content"] = SYSTEM_PROMPT
            cleaned.append(msg)
            continue

        if role == "assistant" and not msg.get("content") and not msg.get("tool_calls"):
            continue

        if role == "assistant" and msg.get("tool_calls") and not _has_valid_tool_calls(msg):
            continue

        if role == "tool" and msg.get("tool_call_id") in bad_call_ids:
            continue

        if msg.get("content") is None:
            msg["content"] = ""

        cleaned.append(msg)

    _ensure_tool_call_ids(cleaned)
    return cleaned


def _fix_tools(tools: list) -> list:
    for tool in tools:
        if "name" in tool and "function" in tool:
            if "name" not in tool["function"]:
                tool["function"]["name"] = tool.pop("name")
            else:
                del tool["name"]
    return tools


def _clean_body(body: dict) -> dict:
    removed = []
    for field in DROP_FIELDS:
        if field in body:
            del body[field]
            removed.append(field)
    if removed:
        logger.info("Dropped fields: %s", ", ".join(removed))
    if "messages" in body:
        body["messages"] = _clean_messages(body["messages"])
    if "tools" in body:
        body["tools"] = _fix_tools(body["tools"])
    return body


def _forward_headers(request: web.Request) -> dict:
    headers = {}
    for key, value in request.headers.items():
        if key.lower() not in SKIP_HEADERS and key.lower() not in DROP_HEADERS:
            headers[key] = value
    headers["user-agent"] = USER_AGENT
    return headers


async def proxy_handler(request: web.Request) -> web.StreamResponse:
    path = request.match_info["path"]
    target_url = f"{TARGET_BASE_URL.rstrip('/')}/{path}"
    if request.query_string:
        target_url += f"?{request.query_string}"

    headers = _forward_headers(request)
    body = None
    raw_body = None

    if request.method in ("POST", "PUT", "PATCH") and request.can_read_body:
        raw_bytes = await request.read()
        try:
            body = json.loads(raw_bytes)
            body = _clean_body(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raw_body = raw_bytes

    is_stream = isinstance(body, dict) and body.get("stream", False)

    if body is not None:
        raw_body = json.dumps(body, ensure_ascii=True, separators=(",", ":")).encode("utf-8")

    try:
        session: aiohttp.ClientSession = request.app["client_session"]
        resp = await session.request(
            method=request.method,
            url=target_url,
            headers=headers,
            data=raw_body,
            timeout=aiohttp.ClientTimeout(total=300),
        )
    except aiohttp.ClientError as e:
        logger.error("Upstream error: %s", e)
        return web.json_response(
            {"error": {"message": str(e), "type": "proxy_error"}},
            status=502,
        )

    if resp.status >= 400:
        error_body = await resp.read()
        logger.warning("Upstream %s: %s", resp.status, error_body[:1000])
        if raw_body:
            with open(FAILED_REQUEST_DUMP_PATH, "wb") as f:
                f.write(raw_body)
            logger.warning(f"Saved failed request body to {FAILED_REQUEST_DUMP_PATH} (%d bytes)", len(raw_body))

        response_headers = {
            k: v for k, v in resp.headers.items()
            if k.lower() not in SKIP_HEADERS
        }
        return web.Response(
            body=error_body,
            status=resp.status,
            content_type=resp.content_type or "application/json",
            headers=response_headers,
        )

    response_headers = {
        k: v for k, v in resp.headers.items()
        if k.lower() not in SKIP_HEADERS
    }

    if is_stream:
        response = web.StreamResponse(
            status=resp.status,
            headers=response_headers,
        )
        response.content_type = resp.content_type or "text/event-stream"
        await response.prepare(request)
        try:
            async for chunk in resp.content.iter_any():
                await response.write(chunk)
            await response.write_eof()
        except ConnectionResetError:
            logger.debug("Client disconnected during stream")
        return response
    else:
        content = await resp.read()
        return web.Response(
            body=content,
            status=resp.status,
            content_type=resp.content_type or "application/json",
            headers=response_headers,
        )


async def on_startup(app: web.Application) -> None:
    app["client_session"] = aiohttp.ClientSession()


async def on_cleanup(app: web.Application) -> None:
    await app["client_session"].close()


def create_app() -> web.Application:
    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    app.router.add_route("*", "/{path:.*}", proxy_handler)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Copilot Proxy")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8779)))
    args = parser.parse_args()

    if not TARGET_BASE_URL:
        print("ERROR: OPENAI_BASE_URL is not set")
        raise SystemExit(1)

    logger.info("Copilot Proxy listening on 0.0.0.0:%d", args.port)
    logger.info("Proxying to: %s", TARGET_BASE_URL)

    web.run_app(create_app(), host="0.0.0.0", port=args.port, print=None)


if __name__ == "__main__":
    main()

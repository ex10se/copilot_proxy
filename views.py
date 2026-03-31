import json
import logging
import re
import requests
from django.conf import settings
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt

logger = logging.getLogger("proxy")

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
})

# Headers that identify automation clients
DROP_HEADERS = frozenset({
    "originator",
    "accept",
})


SYSTEM_PROMPT = "You are a helpful coding assistant. Be precise, safe, and helpful."

# Matches invalid JSON escape sequences (everything except \", \\, \/, \b, \f, \n, \r, \t, \uXXXX)
_INVALID_ESCAPE_RE = re.compile(r'\\(?!["\\/bfnrtu])')


def _fix_json_escapes(s: str) -> str:
    """Fix invalid JSON escape sequences by double-escaping the backslash."""
    return _INVALID_ESCAPE_RE.sub(r'\\\\', s)


def _has_valid_tool_calls(msg: dict) -> bool:
    """Check if all tool_calls have valid JSON arguments."""
    for tc in msg.get("tool_calls", []):
        args = tc.get("function", {}).get("arguments")
        if isinstance(args, str):
            try:
                json.loads(args)
            except json.JSONDecodeError:
                return False
    return True


def _ensure_tool_call_ids(messages: list) -> list:
    """Ensure tool_calls have IDs and tool messages have matching tool_call_id."""
    # Collect existing tool_call IDs from assistant messages
    pending_ids = []
    for msg in messages:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                if "id" not in tc:
                    tc["id"] = f"call_{id(tc)}"
                pending_ids.append(tc["id"])

    # Assign tool_call_id to tool messages that lack one
    id_idx = 0
    for msg in messages:
        if msg.get("role") == "tool" and "tool_call_id" not in msg:
            if id_idx < len(pending_ids):
                msg["tool_call_id"] = pending_ids[id_idx]
                id_idx += 1
            else:
                msg["tool_call_id"] = f"call_{id(msg)}"


def _clean_messages(messages: list) -> list:
    # First pass: collect tool_call IDs with invalid arguments
    bad_call_ids = set()
    for msg in messages:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            if not _has_valid_tool_calls(msg):
                for tc in msg.get("tool_calls", []):
                    if tc.get("id"):
                        bad_call_ids.add(tc["id"])

    if bad_call_ids:
        logger.info("Dropping %d tool calls with invalid arguments", len(bad_call_ids))

    # Second pass: build cleaned list
    cleaned = []
    for msg in messages:
        role = msg.get("role")

        # Replace system prompt
        if role == "system":
            msg["content"] = SYSTEM_PROMPT
            cleaned.append(msg)
            continue

        # Skip empty assistant messages (no content and no tool_calls)
        if role == "assistant" and not msg.get("content") and not msg.get("tool_calls"):
            continue

        # Skip assistant messages with invalid tool_calls
        if role == "assistant" and msg.get("tool_calls") and not _has_valid_tool_calls(msg):
            continue

        # Skip tool results for dropped tool_calls
        if role == "tool" and msg.get("tool_call_id") in bad_call_ids:
            continue

        # Replace null content with empty string (vLLM doesn't handle null)
        if msg.get("content") is None:
            msg["content"] = ""

        cleaned.append(msg)

    _ensure_tool_call_ids(cleaned)
    return cleaned


def _fix_tools(tools: list) -> list:
    """Transform Codex tool format to standard OpenAI format."""
    for tool in tools:
        if "name" in tool and "function" in tool:
            if "name" not in tool["function"]:
                tool["function"]["name"] = tool.pop("name")
            else:
                del tool["name"]
    return tools


def _clean_body(body: dict) -> dict:
    removed = []
    for field in settings.DROP_FIELDS:
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


def _forward_headers(request) -> dict:
    headers = {}
    for key, value in request.META.items():
        if key.startswith("HTTP_"):
            header_name = key[5:].replace("_", "-").lower()
            if header_name not in SKIP_HEADERS and header_name not in DROP_HEADERS:
                headers[header_name] = value
    if "CONTENT_TYPE" in request.META:
        headers["content-type"] = request.META["CONTENT_TYPE"]
    headers["user-agent"] = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    return headers


@csrf_exempt
def proxy_view(request, path):
    target_url = f"{settings.TARGET_BASE_URL.rstrip('/')}/{path}"
    if request.META.get("QUERY_STRING"):
        target_url += f"?{request.META['QUERY_STRING']}"

    headers = _forward_headers(request)
    body = None

    if request.method in ("POST", "PUT", "PATCH") and request.body:
        try:
            body = json.loads(request.body)
            body = _clean_body(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = None

    is_stream = isinstance(body, dict) and body.get("stream", False)

    if body is not None:
        raw_body = json.dumps(body, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    elif request.method in ("POST", "PUT", "PATCH"):
        raw_body = request.body
    else:
        raw_body = None

    try:
        resp = requests.request(
            method=request.method,
            url=target_url,
            headers=headers,
            data=raw_body,
            stream=is_stream,
            timeout=300,
        )
    except requests.RequestException as e:
        logger.error("Upstream error: %s", e)
        return JsonResponse({"error": {"message": str(e), "type": "proxy_error"}}, status=502)

    if resp.status_code >= 400:
        logger.warning("Upstream %s: %s", resp.status_code, resp.text[:1000])
        if raw_body:
            with open("/tmp/last_failed_request.json", "wb") as f:
                f.write(raw_body)
            logger.warning("Saved failed request body to /tmp/last_failed_request.json (%d bytes)", len(raw_body))

    if is_stream:
        response = StreamingHttpResponse(
            resp.iter_content(chunk_size=4096),
            status=resp.status_code,
            content_type=resp.headers.get("content-type", "text/event-stream"),
        )
    else:
        response = HttpResponse(
            content=resp.content,
            status=resp.status_code,
            content_type=resp.headers.get("content-type", "application/json"),
        )

    for key, value in resp.headers.items():
        if key.lower() not in SKIP_HEADERS and key.lower() != "content-encoding":
            response[key] = value

    return response

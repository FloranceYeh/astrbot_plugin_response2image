import json

import httpx

try:
    from .retry import RetryContext
except ImportError:
    from core.retry import RetryContext


def format_retry_notice(context: RetryContext) -> str:
    return (
        f"\u8bf7\u6c42\u5f02\u5e38\uff0c\u51c6\u5907\u5728 {context.delay_seconds:.0f} \u79d2\u540e\u91cd\u8bd5"
        f"\uff08\u7b2c {context.attempt_index + 1}/{context.retry_count} \u6b21\uff09\uff1a{context.detail}"
    )


def summarize_error_body(body: bytes) -> str:
    text = body.decode("utf-8", "ignore").strip()
    if not text:
        return "\u7a7a\u54cd\u5e94"

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text[:500]

    return (extract_error_message(payload) or text)[:500]


def extract_error_message(payload: object) -> str | None:
    if isinstance(payload, str):
        text = payload.strip()
        return text or None
    if isinstance(payload, dict):
        for key in ("error", "message", "detail"):
            value = payload.get(key)
            message = extract_error_message(value)
            if message:
                return message
        return None
    if isinstance(payload, list):
        for item in payload:
            message = extract_error_message(item)
            if message:
                return message
    return None


def should_retry_http_error(status_code: int, detail: str) -> bool:
    if status_code in {408, 409, 425, 429} or status_code >= 500:
        return True

    normalized = "".join(detail.split())
    return "\u67e5\u8be2api\u4f7f\u7528" in normalized and "\u6ca1\u6709\u672c\u6b21\u7684\u8bb0\u5f55" in normalized


def classify_retry_exception(exc: Exception) -> tuple[str, bool] | None:
    if isinstance(exc, httpx.TransportError):
        return (f"{exc}", True)
    return None

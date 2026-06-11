import json

import httpx

try:
    from .retry import RetryContext
except ImportError:
    from core.retry import RetryContext


def format_retry_notice(context: RetryContext) -> str:
    return (
        f"请求异常，准备在 {context.delay_seconds:.0f} 秒后重试"
        f"（第 {context.attempt_index + 1}/{context.retry_count} 次）：{context.detail}"
    )


def summarize_error_body(body: bytes) -> str:
    text = body.decode("utf-8", "ignore").strip()
    if not text:
        return "空响应"

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
    return "查询api使用" in normalized and "没有本次的记录" in normalized


def classify_retry_exception(exc: Exception) -> tuple[str, bool] | None:
    if isinstance(exc, httpx.TransportError):
        return (f"{exc}", True)
    return None

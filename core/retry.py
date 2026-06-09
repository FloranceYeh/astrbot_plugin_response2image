import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")


@dataclass(slots=True)
class RetryContext:
    attempt_index: int
    retry_count: int
    detail: str
    delay_seconds: float


class RetrySignalError(Exception):
    def __init__(self, detail: str, *, retryable: bool = True):
        super().__init__(detail)
        self.detail = detail
        self.retryable = retryable


def default_retry_delay(attempt_index: int) -> float:
    return float(min(attempt_index + 1, 5))


async def run_with_retries(
    operation: Callable[[], Awaitable[T]],
    *,
    retry_count: int,
    get_delay_seconds: Callable[[int], float] = default_retry_delay,
    on_retry: Callable[[RetryContext], Awaitable[None]] | None = None,
    classify_exception: Callable[[Exception], tuple[str, bool] | None] | None = None,
) -> T:
    for attempt_index in range(retry_count + 1):
        try:
            return await operation()
        except RetrySignalError as exc:
            detail = exc.detail
            retryable = exc.retryable
            source_exc: Exception = exc
        except Exception as exc:
            if classify_exception is None:
                raise
            classified = classify_exception(exc)
            if classified is None:
                raise
            detail, retryable = classified
            source_exc = exc

        if not retryable or attempt_index >= retry_count:
            raise RetrySignalError(detail, retryable=retryable) from source_exc

        delay_seconds = float(get_delay_seconds(attempt_index))
        if on_retry is not None:
            await on_retry(
                RetryContext(
                    attempt_index=attempt_index,
                    retry_count=retry_count,
                    detail=detail,
                    delay_seconds=delay_seconds,
                )
            )
        await asyncio.sleep(delay_seconds)

    raise RuntimeError("unreachable")

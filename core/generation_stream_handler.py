import json
import time
from dataclasses import dataclass

import httpx
from astrbot.api.event import AstrMessageEvent

try:
    from .generation import (
        GenerationAttemptOutcome,
        GenerationMode,
        GenerationRuntimeConfig,
        format_elapsed_seconds,
        mode_label,
    )
    from .media import ImageMediaService
    from .sender import Sender
    from .storage import GeneratedImageStore
except ImportError:
    from core.generation import (
        GenerationAttemptOutcome,
        GenerationMode,
        GenerationRuntimeConfig,
        format_elapsed_seconds,
        mode_label,
    )
    from core.media import ImageMediaService
    from core.sender import Sender
    from core.storage import GeneratedImageStore


@dataclass(slots=True)
class GenerationStreamHandler:
    media_service: ImageMediaService
    image_store: GeneratedImageStore
    sender: Sender

    async def stream_response(
        self,
        response: httpx.Response,
        client: httpx.AsyncClient,
        event: AstrMessageEvent,
        runtime_config: GenerationRuntimeConfig,
        resolved_mode: GenerationMode,
        started_at: float,
        image_error_state: dict[str, str | None],
    ) -> GenerationAttemptOutcome:
        async for line in response.aiter_lines():
            data_str = self._extract_stream_data(line)
            if data_str is None:
                continue
            if data_str == "[DONE]":
                break
            payload = self._parse_stream_payload(data_str)
            if payload is None:
                continue
            outcome = await self._build_outcome_from_payload(
                client,
                event,
                payload,
                runtime_config,
                resolved_mode,
                started_at,
                image_error_state,
            )
            if outcome is not None:
                return outcome

        return GenerationAttemptOutcome(
            response=None,
            image_error=image_error_state["value"],
        )

    def _extract_stream_data(self, line: str) -> str | None:
        normalized = line.strip()
        if not normalized or normalized.startswith("event: "):
            return None
        if not normalized.startswith("data: "):
            return None
        return normalized[6:]

    def _parse_stream_payload(self, data_str: str) -> object | None:
        try:
            return json.loads(data_str)
        except json.JSONDecodeError:
            self.sender.log("warning", f"无法解析流式响应数据: {data_str[:200]}")
            return None

    async def _build_outcome_from_payload(
        self,
        client: httpx.AsyncClient,
        event: AstrMessageEvent,
        payload: object,
        runtime_config: GenerationRuntimeConfig,
        resolved_mode: GenerationMode,
        started_at: float,
        image_error_state: dict[str, str | None],
    ) -> GenerationAttemptOutcome | None:
        image_ref = self.media_service.extract_image_ref(payload)
        if not image_ref:
            return None

        try:
            image_bytes = await self.media_service.read_image_bytes(image_ref, client)
        except ValueError as exc:
            image_error_state["value"] = str(exc)
            self.sender.log("warning", f"图片解析失败: {exc}")
            return None

        return self._build_generated_image_outcome(
            event,
            image_bytes,
            runtime_config,
            resolved_mode,
            started_at,
            image_error_state["value"],
        )

    def _build_generated_image_outcome(
        self,
        event: AstrMessageEvent,
        image_bytes: bytes,
        runtime_config: GenerationRuntimeConfig,
        resolved_mode: GenerationMode,
        started_at: float,
        image_error: str | None,
    ) -> GenerationAttemptOutcome:
        file_path = self.image_store.write_image(
            image_bytes,
            runtime_config.generated_image_keep_count,
        )
        size_str = self.media_service.format_size(len(image_bytes))
        elapsed_seconds = time.perf_counter() - started_at
        elapsed_text = format_elapsed_seconds(elapsed_seconds)
        status_text = f"{mode_label(resolved_mode)} {size_str} {elapsed_text}"
        return GenerationAttemptOutcome(
            response=self.sender.image_result(
                event,
                file_path=file_path,
                size_bytes=len(image_bytes),
                size_text=size_str,
                mode=resolved_mode,
                status_text=status_text,
                elapsed_seconds=elapsed_seconds,
                elapsed_text=elapsed_text,
                send_generated_image_in_chat=runtime_config.send_generated_image_in_chat,
            ),
            image_error=image_error,
        )

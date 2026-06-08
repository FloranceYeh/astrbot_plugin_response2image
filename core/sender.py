from pathlib import Path
from typing import Any

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from .generation import GenerationResult

PLUGIN_RESPONSE_PREFIX = "[r2i]"


class Sender:
    def prefix(self, text: str) -> str:
        return f"{PLUGIN_RESPONSE_PREFIX} {text}"

    def plain_result(self, event: AstrMessageEvent, text: str) -> Any:
        return event.plain_result(self.prefix(text))

    def text_result(
        self,
        event: AstrMessageEvent,
        text: str,
        *,
        log_level: str | None = None,
    ) -> GenerationResult:
        if log_level:
            self.log(log_level, text)
        return GenerationResult(self.plain_result(event, text), self.prefix(text))

    def image_result(
        self,
        event: AstrMessageEvent,
        *,
        file_path: Path,
        size_bytes: int,
        size_text: str,
        mode: str,
        status_text: str,
        elapsed_seconds: float,
        elapsed_text: str,
        send_generated_image_in_chat: bool,
    ) -> GenerationResult:
        resolved_path = str(file_path.resolve())
        llm_lines = [status_text, f"图片路径：{resolved_path}"]
        if send_generated_image_in_chat:
            llm_lines.append("已将生成的图片发送到当前对话。")
        image_data = {
            "type": "image",
            "path": resolved_path,
            "size_bytes": size_bytes,
            "size_text": size_text,
            "mode": mode,
            "status": status_text,
            "elapsed_seconds": round(elapsed_seconds, 3),
            "elapsed_text": elapsed_text,
        }
        chain = [
            Comp.Plain(self.prefix(status_text)),
            Comp.Image.fromFileSystem(str(file_path)),
        ]
        return GenerationResult(
            event.chain_result(chain),
            self.prefix("\n".join(llm_lines)),
            has_image=True,
            image_path=resolved_path,
            image_data=image_data,
            elapsed_seconds=elapsed_seconds,
        )

    async def log_and_send(
        self,
        event: AstrMessageEvent,
        text: str,
        *,
        log_level: str = "info",
    ) -> None:
        self.log(log_level, text)
        await self.send(event, self.plain_result(event, text))

    async def send(self, event: AstrMessageEvent, response: Any) -> None:
        try:
            await event.send(response)
        except Exception as exc:
            self.log("warning", f"发送消息失败: {exc}")

    def log(self, level: str, message: str) -> None:
        log_fn = getattr(logger, level, logger.info)
        log_fn(message)

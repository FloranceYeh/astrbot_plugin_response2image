import base64
import binascii
import json
import shlex
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools


class Response2Image(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.data_dir: Path = StarTools.get_data_dir()

    @filter.command("img", alias={"画图", "绘图", "r2i", "resp2img"})
    async def img(self, event: AstrMessageEvent, prompt: str):
        """根据提示词生成图片。用法：img <提示词> [--ref 图片URL] [--model 模型]"""
        try:
            prompt, ref_urls, model_override = self._parse_prompt(prompt)
        except ValueError as exc:
            yield event.plain_result(str(exc))
            return

        base_url = str(self._config_get("base_url", "")).strip()
        api_key = str(self._config_get("api_key", "")).strip()
        model = str(self._config_get("model", "")).strip()
        if model_override:
            model = model_override

        if not base_url:
            yield event.plain_result("请在插件配置中设置 base_url。")
            return
        if not api_key:
            yield event.plain_result("请在插件配置中设置 api_key。")
            return
        if not model:
            yield event.plain_result("请在插件配置中设置 model，或在命令中使用 --model 指定。")
            return

        try:
            normalized_base = self._normalize_base_url(base_url)
            ref_images = self._normalize_ref_images(ref_urls)
        except ValueError as exc:
            yield event.plain_result(str(exc))
            return

        payload = self._build_payload(prompt, model, ref_images)
        url = normalized_base + "/v1/responses"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
        }
        try:
            timeout_seconds = int(self._config_get("timeout_seconds", 120))
        except (TypeError, ValueError):
            yield event.plain_result("插件配置 timeout_seconds 无效。")
            return
        if timeout_seconds <= 0:
            yield event.plain_result("插件配置 timeout_seconds 必须大于 0。")
            return
        timeout = httpx.Timeout(timeout_seconds)

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", url, headers=headers, json=payload) as response:
                    if response.status_code >= 400:
                        body = await response.aread()
                        detail = body.decode("utf-8", "ignore")[:500]
                        yield event.plain_result(f"请求失败：HTTP {response.status_code} {detail}")
                        return

                    async for line in response.aiter_lines():
                        line = line.strip()
                        if not line:
                            continue
                        if line.startswith("event: "):
                            continue
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if not data_str or data_str == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                        except json.JSONDecodeError:
                            logger.warning("无法解析数据块: %s", data_str[:200])
                            continue

                        b64 = self._extract_base64(data)
                        if not b64:
                            continue
                        try:
                            image_bytes = base64.b64decode(b64)
                        except binascii.Error as exc:
                            logger.warning("无效的 base64 图像: %s", exc)
                            continue

                        file_path = self._write_image(image_bytes)
                        yield event.image_result(str(file_path))
                        return

            yield event.plain_result("未收到图片结果，请检查模型是否支持 image_generation。")
        except httpx.HTTPError as exc:
            logger.error(f"请求失败: {exc}")
            yield event.plain_result(f"请求失败：{exc}")

    def _config_get(self, key: str, default: Any) -> Any:
        if self.config is None:
            return default
        if isinstance(self.config, dict):
            return self.config.get(key, default)
        if hasattr(self.config, "get"):
            return self.config.get(key, default)
        return getattr(self.config, key, default)

    def _normalize_base_url(self, base_url: str) -> str:
        base = base_url.strip()
        if not base:
            raise ValueError("Base URL 不能为空。")
        if not base.lower().startswith(("http://", "https://")):
            raise ValueError("Base URL 必须以 http:// 或 https:// 开头。")
        base = base.rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        return base

    def _parse_prompt(self, raw: str) -> tuple[str, list[str], str | None]:
        tokens = shlex.split(raw)
        if not tokens:
            raise ValueError("请提供提示词。")

        prompt_parts: list[str] = []
        ref_urls: list[str] = []
        model_override: str | None = None

        i = 0
        while i < len(tokens):
            token = tokens[i]
            if token == "--ref":
                if i + 1 >= len(tokens):
                    raise ValueError("缺少 --ref 参数。")
                ref_urls.extend([u for u in tokens[i + 1].split(",") if u])
                i += 2
                continue
            if token == "--model":
                if i + 1 >= len(tokens):
                    raise ValueError("缺少 --model 参数。")
                model_override = tokens[i + 1]
                i += 2
                continue
            prompt_parts.append(token)
            i += 1

        prompt = " ".join(prompt_parts).strip()
        if not prompt:
            raise ValueError("请提供提示词。")
        return prompt, ref_urls, model_override

    def _normalize_ref_images(self, refs: list[str]) -> list[str]:
        normalized: list[str] = []
        for ref in refs:
            ref = ref.strip()
            if not ref:
                continue
            if ref.startswith(("http://", "https://", "data:image/")):
                normalized.append(ref)
                continue
            path = Path(ref)
            if not path.is_file():
                raise ValueError(f"参考图片不存在: {ref}")
            suffix = path.suffix.lower()
            if suffix in {".jpg", ".jpeg"}:
                mime = "image/jpeg"
            elif suffix == ".webp":
                mime = "image/webp"
            else:
                mime = "image/png"
            data = path.read_bytes()
            b64 = base64.b64encode(data).decode("ascii")
            normalized.append(f"data:{mime};base64,{b64}")
        return normalized

    def _build_payload(self, prompt: str, model: str, ref_images: list[str]) -> dict:
        if ref_images:
            edit_prompt = (
                "请根据以下要求，对我提供的参考图片进行编辑修改，直接生成修改后的新图片。要求："
                + prompt
            )
            content = [{"type": "input_image", "image_url": url} for url in ref_images]
            content.append({"type": "input_text", "text": edit_prompt})
            return {
                "model": model,
                "input": [{"role": "user", "content": content}],
                "tools": [{"type": "image_generation", "output_format": "png"}],
                "stream": True,
            }

        return {
            "model": model,
            "input": [
                {
                    "role": "system",
                    "content": (
                        "你是一个图片生成助手。用户要求你生成图片时，你必须调用 image_generation 工具来生成图片，"
                        "不要用文字描述图片内容。直接生成图片，不要多说任何话。"
                    ),
                },
                {"role": "user", "content": f"请生成以下描述的图片：{prompt}"},
            ],
            "tools": [{"type": "image_generation", "output_format": "png"}],
            "stream": True,
        }

    def _extract_base64(self, data: Any) -> str | None:
        if isinstance(data, dict):
            for key, value in data.items():
                if key in {"result", "b64_json", "image"} and isinstance(value, str):
                    if self._looks_like_base64(value):
                        return value
                found = self._extract_base64(value)
                if found:
                    return found
        elif isinstance(data, list):
            for item in data:
                found = self._extract_base64(item)
                if found:
                    return found
        return None

    def _looks_like_base64(self, value: str) -> bool:
        if value.startswith(("iVBOR", "/9j/")):
            return True
        return len(value) > 1000

    def _write_image(self, image_bytes: bytes) -> Path:
        out_dir = self.data_dir / "generated"
        out_dir.mkdir(parents=True, exist_ok=True)
        name = datetime.now().strftime("resp2img_%Y%m%d_%H%M%S.png")
        path = out_dir / name
        path.write_bytes(image_bytes)
        return path

    async def terminate(self):
        """插件被卸载时触发。"""

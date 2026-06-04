import base64
import binascii
import json
import re
import shlex
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
import astrbot.api.message_components as Comp
from astrbot.api.star import Context, Star, StarTools

UPSTREAM_IMAGE_RETRY_INSTRUCTION = (
    "重要指示：如果 image_generation 未产出图片，请在不改变核心意图的前提下改写提示词并再次调用，"
    "直到成功生成图片，否则继续重试，不得中途停止。"
)

UPSTREAM_IMAGE_SYSTEM_PROMPT = "\n".join(
    [
        "你是专业图片生成代理。目标是调用 image_generation 工具直接产出图片，不用文字描述代替图片。",
        "调用工具前，将需求整理为简洁、可执行的视觉提示词；保留硬约束，适度补足构图、主体、动作、场景、光线、色彩、材质、镜头、背景、风格和细节。如果需求中有明确的风格要求，需要遵循。",
        "不要擅自增加水印、Logo、签名、边框、乱码或无关文字。",
        UPSTREAM_IMAGE_RETRY_INSTRUCTION,
        "最终只产出图片，不解释改写过程，不输出 Markdown 或无关文本。",
    ]
)

DEFAULT_REFERENCE_PROMPT_EDIT = (
    "只修改用户明确要求修改的内容；未提及的主体身份、数量、构图、比例、姿态、背景关系和关键细节尽量保持不变。"
)
DEFAULT_REFERENCE_PROMPT_SELFIE = (
    "参考图片仅作为人物与外观依据；优先保持人物身份、脸部特征和整体一致性，根据用户要求生成自然的自拍照片效果。"
)
DEFAULT_REFERENCE_PROMPT_WHITE = (
    "该参考图为纯白占位图，仅用于稳定生成流程与强化对文本指令的遵循，不提供任何可继承的主体、构图、风格或细节信息；请忽略其视觉内容，不要把白底、空白画面、留白构图或极简白色背景当作目标效果，仍以用户原始需求为唯一主要依据完成正常文生图"
)
PLUGIN_RESPONSE_PREFIX = "[r2i]"
WHITE_REFERENCE_IMAGE_NAME = "space.jpg"


class GenerationResult:
    def __init__(
        self,
        response: Any,
        llm_text: str,
        has_image: bool = False,
        image_path: str | None = None,
        image_data: dict[str, Any] | None = None,
    ):
        self.response = response
        self.llm_text = llm_text
        self.has_image = has_image
        self.image_path = image_path
        self.image_data = image_data


class Response2Image(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.data_dir: Path = StarTools.get_data_dir()

    @filter.command_group("r2i")
    def r2i(self):
        pass

    @r2i.command("help")
    async def r2i_help(self, event: AstrMessageEvent):
        yield self._plain_result(
            event,
            "Response2Image\n"
            "• /r2i img <提示词> [--ref] [--size]\n    自动判断文生图/改图\n"
            "• /r2i aiimg <提示词> [--size]\n    文生图\n"
            "• /r2i aiedit <提示词> [--ref] [--size]\n    图生图\n"
            "• /r2i selfie <提示词> [--ref] [--size]\n    自拍\n"
            "• /r2i selfie_ref set\n    发送或引用图片后执行\n"
            "• /r2i selfie_ref list\n    查看当前参考图\n"
            "• /r2i selfie_ref clear\n    清空命令保存的参考图"
        )

    @r2i.command("img")
    async def img(
        self,
        event: AstrMessageEvent,
        prompt: str = "",
        ref: str = "",
        size: str = "",
    ):
        """自动判断文生图或改图。"""
        raw_prompt = self._resolve_command_prompt(
            event,
            "img",
            self._compose_command_fallback_prompt(prompt, ref=ref, size=size),
        )
        async for result in self._generate(event, raw_prompt, mode="auto"):
            yield result

    @r2i.command("aiimg")
    async def aiimg(self, event: AstrMessageEvent, prompt: str = "", size: str = ""):
        """文生图模式。"""
        raw_prompt = self._resolve_command_prompt(
            event,
            "aiimg",
            self._compose_command_fallback_prompt(prompt, size=size),
        )
        async for result in self._generate(event, raw_prompt, mode="text"):
            yield result

    @r2i.command("aiedit")
    async def aiedit(
        self,
        event: AstrMessageEvent,
        prompt: str = "",
        ref: str = "",
        size: str = "",
    ):
        """改图模式。"""
        raw_prompt = self._resolve_command_prompt(
            event,
            "aiedit",
            self._compose_command_fallback_prompt(prompt, ref=ref, size=size),
        )
        async for result in self._generate(event, raw_prompt, mode="edit"):
            yield result

    @r2i.command("selfie")
    async def selfie(
        self,
        event: AstrMessageEvent,
        prompt: str = "",
        ref: str = "",
        size: str = "",
    ):
        """自拍模式。"""
        raw_prompt = self._resolve_command_prompt(
            event,
            "selfie",
            self._compose_command_fallback_prompt(prompt, ref=ref, size=size),
        )
        async for result in self._generate(event, raw_prompt, mode="selfie"):
            yield result

    @r2i.command("selfie_ref")
    async def selfie_ref(self, event: AstrMessageEvent, action: str = ""):
        """自拍参考照管理：设置/查看/删除。"""
        action = (action or "").strip()
        if action in {"设置", "set"}:
            refs = self._extract_refs_from_event(event)
            if not refs:
                yield self._plain_result(event, "请发送或引用图片后再设置自拍参考照。")
                return
            try:
                timeout = self._get_timeout()
            except ValueError as exc:
                yield self._plain_result(event, str(exc))
                return
            async with httpx.AsyncClient(timeout=timeout) as client:
                try:
                    count = await self._save_selfie_refs(refs, client)
                except ValueError as exc:
                    yield self._plain_result(event, str(exc))
                    return
            yield self._plain_result(event, f"已保存自拍参考照 {count} 张。")
            return
        if action in {"查看", "list"}:
            config_refs = self._get_selfie_refs_from_config()
            saved_refs = self._list_selfie_ref_paths()
            refs = self._merge_refs(config_refs, saved_refs)
            if not refs:
                yield self._plain_result(event, "暂无自拍参考照。")
                return
            yield self._plain_result(
                event,
                f"当前共有 {len(refs)} 张自拍参考照（WebUI 配置 {len(config_refs)} 张，命令保存 {len(saved_refs)} 张）。"
            )
            return
        if action in {"删除", "清空", "clear"}:
            count = self._clear_selfie_refs()
            config_count = len(self._get_selfie_refs_from_config())
            if config_count:
                yield self._plain_result(
                    event,
                    f"已删除命令保存的自拍参考照 {count} 张。WebUI 配置中仍有 {config_count} 张参考图。"
                )
                return
            yield self._plain_result(event, f"已删除命令保存的自拍参考照 {count} 张。")
            return
        yield self._plain_result(event, "用法：自拍参考 设置/查看/删除")

    @filter.llm_tool(name="r2i_img")
    async def llm_r2i_img(
        self,
        event: AstrMessageEvent,
        prompt: str = "",
        ref: str = "",
        size: str = "",
    ):
        """
        自动判断文生图或改图。

        Args:
            prompt(string): 图片生成提示词。
            ref(string): 可选参考图；可用逗号分隔多个参考图，兼容 URL / data:image / 本地文件路径。
            size(string): 可选图片尺寸，例如 1024x1024、1536x1024。
        """
        return await self._run_llm_tool(event, prompt, mode="auto", ref=ref, size=size)

    @filter.llm_tool(name="r2i_aiimg")
    async def llm_r2i_aiimg(
        self,
        event: AstrMessageEvent,
        prompt: str = "",
        size: str = "",
    ):
        """
        文生图。

        Args:
            prompt(string): 图片生成提示词。
            size(string): 可选图片尺寸，例如 1024x1024、1536x1024。
        """
        return await self._run_llm_tool(event, prompt, mode="text", size=size)

    @filter.llm_tool(name="r2i_aiedit")
    async def llm_r2i_aiedit(
        self,
        event: AstrMessageEvent,
        prompt: str = "",
        ref: str = "",
        size: str = "",
    ):
        """
        改图。

        Args:
            prompt(string): 图片编辑提示词。
            ref(string): 可选参考图；可用逗号分隔多个参考图，兼容 URL / data:image / 本地文件路径。
            size(string): 可选图片尺寸，例如 1024x1024、1536x1024。
        """
        return await self._run_llm_tool(event, prompt, mode="edit", ref=ref, size=size)

    @filter.llm_tool(name="r2i_selfie")
    async def llm_r2i_selfie(
        self,
        event: AstrMessageEvent,
        prompt: str = "",
        ref: str = "",
        size: str = "",
    ):
        """
        自拍。

        Args:
            prompt(string): 自拍图提示词。
            ref(string): 可选参考图；可用逗号分隔多个参考图，兼容 URL / data:image / 本地文件路径。
            size(string): 可选图片尺寸，例如 1024x1024、1536x1024。
        """
        return await self._run_llm_tool(event, prompt, mode="selfie", ref=ref, size=size)

    async def _generate(
        self,
        event: AstrMessageEvent,
        raw_prompt: str,
        *,
        mode: str,
        ref: Any = None,
        size: str = "",
    ):
        try:
            prompt, ref_urls, image_size = self._resolve_generation_inputs(raw_prompt, ref, size)
        except ValueError as exc:
            text = str(exc)
            yield self._plain_result(event, text)
            return

        result = await self._generate_result(
            event,
            prompt,
            ref_urls=ref_urls,
            mode=mode,
            image_size=image_size,
        )
        yield result.response

    async def _run_llm_tool(
        self,
        event: AstrMessageEvent,
        raw_prompt: str,
        *,
        mode: str,
        ref: Any = None,
        size: str = "",
    ) -> str:
        try:
            prompt, ref_urls, image_size = self._resolve_generation_inputs(raw_prompt, ref, size)
        except ValueError as exc:
            return self._with_prefix(str(exc))

        result = await self._generate_result(
            event,
            prompt,
            ref_urls=ref_urls,
            mode=mode,
            image_size=image_size,
        )
        if result.has_image:
            if self._should_send_generated_image_in_chat():
                await event.send(result.response)
            return result.llm_text
        return result.llm_text

    async def _generate_result(
        self,
        event: AstrMessageEvent,
        prompt: str,
        *,
        mode: str,
        ref_urls: list[str] | None = None,
        image_size: str | None = None,
    ) -> GenerationResult:
        ref_urls = list(ref_urls or [])

        base_url = str(self._config_get("base_url", "")).strip()
        api_key = str(self._config_get("api_key", "")).strip()
        model = str(self._config_get("model", "")).strip()

        if not base_url:
            text = "请在插件配置中设置 base_url。"
            return GenerationResult(self._plain_result(event, text), self._with_prefix(text))
        if not api_key:
            text = "请在插件配置中设置 api_key。"
            return GenerationResult(self._plain_result(event, text), self._with_prefix(text))
        if not model:
            text = "请在插件配置中设置 model。"
            return GenerationResult(self._plain_result(event, text), self._with_prefix(text))

        try:
            normalized_base = self._normalize_base_url(base_url)
        except ValueError as exc:
            text = str(exc)
            return GenerationResult(self._plain_result(event, text), self._with_prefix(text))

        try:
            timeout = self._get_timeout()
        except ValueError as exc:
            text = str(exc)
            return GenerationResult(self._plain_result(event, text), self._with_prefix(text))
        try:
            self._get_generated_image_keep_count()
        except ValueError as exc:
            text = str(exc)
            return GenerationResult(self._plain_result(event, text), self._with_prefix(text))

        event_refs = self._extract_refs_from_event(event) if mode in {"auto", "edit", "selfie"} else []
        if mode == "selfie" and not ref_urls and not event_refs:
            ref_urls = self._get_all_selfie_refs()

        if mode == "text" and (ref_urls or event_refs):
            text = "文生图模式不使用参考图，请改用改图或自拍。"
            return GenerationResult(self._plain_result(event, text), self._with_prefix(text))
        if mode == "edit" and not (ref_urls or event_refs):
            text = "改图需要参考图片，请发送/引用图片，或通过参考图参数传入。"
            return GenerationResult(self._plain_result(event, text), self._with_prefix(text))
        if mode == "selfie" and not (ref_urls or event_refs):
            text = "未设置自拍参考照，请先使用“自拍参考 设置”。"
            return GenerationResult(self._plain_result(event, text), self._with_prefix(text))

        merged_refs = self._merge_refs(ref_urls, event_refs)
        resolved_mode = self._resolve_mode(mode, merged_refs)
        request_refs = list(merged_refs)
        if resolved_mode == "text" and self._should_use_white_reference_in_text_mode():
            request_refs.append(str(self._get_white_reference_image_path()))
        url = normalized_base + "/v1/responses"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
        }
        image_error: str | None = None

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                try:
                    ref_images = await self._normalize_ref_images(request_refs, client)
                except ValueError as exc:
                    text = str(exc)
                    return GenerationResult(self._plain_result(event, text), self._with_prefix(text))
                if resolved_mode in {"edit", "selfie"} and not ref_images:
                    text = "参考图片不可用，请检查图片是否可访问。"
                    return GenerationResult(self._plain_result(event, text), self._with_prefix(text))

                payload = self._build_payload(
                    prompt,
                    model,
                    ref_images,
                    resolved_mode,
                    image_size=image_size,
                )
                async with client.stream("POST", url, headers=headers, json=payload) as response:
                    if response.status_code >= 400:
                        body = await response.aread()
                        detail = body.decode("utf-8", "ignore")[:500]
                        text = f"请求失败：HTTP {response.status_code} {detail}"
                        return GenerationResult(self._plain_result(event, text), self._with_prefix(text))

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

                        image_ref = self._extract_image_ref(data)
                        if not image_ref:
                            continue
                        try:
                            image_bytes = await self._read_image_bytes(image_ref, client)
                        except ValueError as exc:
                            image_error = str(exc)
                            logger.warning("图片解析失败: %s", exc)
                            continue

                        file_path = self._write_image(image_bytes)
                        resolved_path = str(file_path.resolve())
                        size_str = self._format_size(len(image_bytes))
                        label = self._mode_label(resolved_mode)
                        status_text = f"{label}完成（{size_str}）"
                        llm_lines = [status_text, f"图片路径：{resolved_path}"]
                        if self._should_send_generated_image_in_chat():
                            llm_lines.append("结果已返回到当前聊天。")
                        llm_text = self._with_prefix("\n".join(llm_lines))
                        image_data = {
                            "type": "image",
                            "path": resolved_path,
                            "size_bytes": len(image_bytes),
                            "size_text": size_str,
                            "mode": resolved_mode,
                            "status": status_text,
                        }
                        chain = [
                            Comp.Plain(self._with_prefix(status_text)),
                            Comp.Image.fromFileSystem(str(file_path)),
                        ]
                        return GenerationResult(
                            event.chain_result(chain),
                            llm_text,
                            has_image=True,
                            image_path=resolved_path,
                            image_data=image_data,
                        )

            if image_error:
                return GenerationResult(self._plain_result(event, image_error), self._with_prefix(image_error))
            text = "未收到图片结果，请检查模型是否支持 image_generation。"
            return GenerationResult(self._plain_result(event, text), self._with_prefix(text))
        except httpx.HTTPError as exc:
            logger.error(f"请求失败: {exc}")
            text = f"请求失败：{exc}"
            return GenerationResult(self._plain_result(event, text), self._with_prefix(text))

    def _with_prefix(self, text: str) -> str:
        return f"{PLUGIN_RESPONSE_PREFIX} {text}"

    def _plain_result(self, event: AstrMessageEvent, text: str):
        return event.plain_result(self._with_prefix(text))

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

    def _resolve_command_prompt(
        self,
        event: AstrMessageEvent,
        command_name: str,
        fallback_prompt: str = "",
    ) -> str:
        message = (event.message_str or "").strip()
        if not message:
            return fallback_prompt.strip()

        parts = message.split(maxsplit=2)
        if len(parts) >= 2 and parts[0].lstrip("/").lower() == "r2i" and parts[1].lower() == command_name:
            return parts[2].strip() if len(parts) == 3 else ""
        if parts and parts[0].lstrip("/").lower() == command_name:
            return parts[1].strip() if len(parts) >= 2 else ""
        return fallback_prompt.strip()

    def _compose_command_fallback_prompt(
        self,
        prompt: str = "",
        *,
        ref: str = "",
        size: str = "",
    ) -> str:
        parts: list[str] = []
        if prompt.strip():
            parts.append(prompt.strip())
        if ref.strip():
            parts.extend(["--ref", ref.strip()])
        if size.strip():
            parts.extend(["--size", size.strip()])
        return " ".join(parts)

    def _resolve_generation_inputs(
        self,
        prompt: str,
        ref: Any = None,
        size: str = "",
    ) -> tuple[str, list[str], str | None]:
        normalized_prompt = (prompt or "").strip()
        explicit_refs = self._parse_ref_argument(ref)
        explicit_size = self._normalize_image_size(size)
        legacy_refs: list[str] = []
        legacy_size: str | None = None

        if "--ref" in normalized_prompt or "--size" in normalized_prompt:
            normalized_prompt, legacy_refs, legacy_size = self._parse_legacy_prompt_and_refs(
                normalized_prompt
            )

        if not normalized_prompt:
            raise ValueError("请提供提示词。")
        return (
            normalized_prompt,
            self._merge_refs(explicit_refs, legacy_refs),
            explicit_size or legacy_size or self._get_default_image_size(),
        )

    def _parse_ref_argument(self, ref: Any) -> list[str]:
        if ref is None:
            return []
        if isinstance(ref, str):
            return self._split_ref_values(ref)
        if isinstance(ref, (list, tuple, set)):
            refs: list[str] = []
            for item in ref:
                refs.extend(self._parse_ref_argument(item))
            return refs
        return self._split_ref_values(str(ref))

    def _parse_legacy_prompt_and_refs(self, raw: str) -> tuple[str, list[str], str | None]:
        tokens = shlex.split(raw)
        if not tokens:
            raise ValueError("请提供提示词。")

        prompt_parts: list[str] = []
        ref_urls: list[str] = []
        image_size: str | None = None

        i = 0
        while i < len(tokens):
            token = tokens[i]
            if token == "--ref":
                if i + 1 >= len(tokens):
                    raise ValueError("缺少 --ref 参数。")
                ref_urls.extend(self._split_ref_values(tokens[i + 1]))
                i += 2
                continue
            if token == "--size":
                if i + 1 >= len(tokens):
                    raise ValueError("缺少 --size 参数。")
                image_size = self._normalize_image_size(tokens[i + 1])
                i += 2
                continue
            prompt_parts.append(token)
            i += 1

        prompt = " ".join(prompt_parts).strip()
        if not prompt:
            raise ValueError("请提供提示词。")
        return prompt, ref_urls, image_size

    def _split_ref_values(self, raw: str) -> list[str]:
        return [item.strip() for item in raw.split(",") if item.strip()]

    def _normalize_image_size(self, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        match = re.fullmatch(r"(\d{2,5})[xX](\d{2,5})", text)
        if not match:
            raise ValueError("图片尺寸格式无效，请使用类似 1024x1024 的宽x高格式。")
        return f"{match.group(1)}x{match.group(2)}"

    def _get_default_image_size(self) -> str | None:
        return self._normalize_image_size(self._config_get("image_size", ""))

    def _get_timeout(self) -> httpx.Timeout:
        try:
            timeout_seconds = int(self._config_get("timeout_seconds", 120))
        except (TypeError, ValueError) as exc:
            raise ValueError("插件配置 timeout_seconds 无效。") from exc
        if timeout_seconds <= 0:
            raise ValueError("插件配置 timeout_seconds 必须大于 0。")
        return httpx.Timeout(timeout_seconds)

    def _get_generated_image_keep_count(self) -> int:
        try:
            keep_count = int(self._config_get("generated_image_keep_count", -1))
        except (TypeError, ValueError) as exc:
            raise ValueError("插件配置 generated_image_keep_count 无效。") from exc
        if keep_count == -1 or keep_count > 0:
            return keep_count
        raise ValueError("插件配置 generated_image_keep_count 必须为 -1 或大于 0。")

    def _resolve_mode(self, mode: str, refs: list[str]) -> str:
        if mode == "auto":
            return "edit" if refs else "text"
        return mode

    def _mode_label(self, mode: str) -> str:
        if mode == "edit":
            return "改图"
        if mode == "selfie":
            return "自拍"
        return "文生图"

    def _build_upstream_image_prompt(
        self, prompt: str, mode: str, has_reference_images: bool
    ) -> str:
        task_lines: list[str] = [
            "请直接调用 image_generation 工具完成图片任务。",
            "先整理为一条清晰、可执行的视觉提示词，再调用工具。",
            "保留用户要求的主体、动作、场景、风格、文字、比例、禁止事项等硬约束。",
            "可适度补充构图、镜头、光线、色彩、材质、空间关系和清晰度；不要引入与主题冲突的元素。",
            "如用户要求包含文字，保持文字内容一致、简洁、清晰可读。",
            UPSTREAM_IMAGE_RETRY_INSTRUCTION,
            "只输出 image_generation 工具生成的图片结果；不要输出解释、分析、Markdown 或纯文本替代答案。",
        ]

        reference_lines: list[str] = []
        if has_reference_images:
            reference_lines.extend(self._get_reference_prompt_lines(mode))

        lines = list(task_lines)
        if reference_lines:
            lines.extend(["", "参考图片处理："])
            lines.extend(f"- {line}" for line in reference_lines)
        lines.extend(["", "用户原始需求如下：", prompt])
        return "\n".join(lines)

    def _get_reference_prompt_lines(self, mode: str) -> list[str]:
        if mode == "selfie":
            key = "reference_prompt_selfie"
            default = DEFAULT_REFERENCE_PROMPT_SELFIE
        elif mode == "edit":
            key = "reference_prompt_edit"
            default = DEFAULT_REFERENCE_PROMPT_EDIT
        else:
            text = DEFAULT_REFERENCE_PROMPT_WHITE
            text = text.strip()
            return [line.strip() for line in text.splitlines() if line.strip()]

        raw = self._config_get(key, default)
        text = raw if isinstance(raw, str) else default
        text = text.strip()
        if not text:
            return []
        return [line.strip() for line in text.splitlines() if line.strip()]

    def _should_use_white_reference_in_text_mode(self) -> bool:
        return bool(self._config_get("text_mode_use_white_reference_image", False))

    def _should_send_generated_image_in_chat(self) -> bool:
        return bool(self._config_get("send_generated_image_in_chat", False))

    def _get_white_reference_image_path(self) -> Path:
        image_path = Path(__file__).resolve().parent / WHITE_REFERENCE_IMAGE_NAME
        if not image_path.is_file():
            raise ValueError(f"白图参考文件不存在: {image_path}")
        return image_path

    async def _normalize_ref_images(self, refs: list[str], client: httpx.AsyncClient) -> list[str]:
        normalized: list[str] = []
        for ref in refs:
            ref = ref.strip()
            if not ref:
                continue
            if self._looks_like_data_url(ref):
                normalized.append(ref)
                continue
            if ref.startswith(("http://", "https://")):
                data_url = await self._fetch_image_as_data_url(ref, client)
                normalized.append(data_url)
                continue
            path = Path(ref)
            if not path.is_file():
                raise ValueError(f"参考图片不存在: {ref}")
            mime = self._guess_mime_from_path(path)
            data = path.read_bytes()
            normalized.append(self._build_data_url(mime, data))
        return normalized

    def _build_payload(
        self,
        prompt: str,
        model: str,
        ref_images: list[str],
        mode: str,
        *,
        image_size: str | None = None,
    ) -> dict:
        user_prompt = self._build_upstream_image_prompt(prompt, mode, bool(ref_images))
        tool: dict[str, Any] = {"type": "image_generation", "output_format": "png"}
        if image_size:
            tool["size"] = image_size

        if ref_images:
            content = [{"type": "input_image", "image_url": url} for url in ref_images]
            content.append({"type": "input_text", "text": user_prompt})
            return {
                "model": model,
                "input": [
                        {"role": "system", "content": UPSTREAM_IMAGE_SYSTEM_PROMPT},
                        {"role": "user", "content": content}
                    ],
                "tools": [tool],
                "tool_choice": { "type": "image_generation" },
                "stream": True,
            }

        return {
            "model": model,
            "input": [
                    {"role": "system", "content": UPSTREAM_IMAGE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
            "tools": [tool],
            "tool_choice": { "type": "image_generation" },
            "stream": True,
        }

    def _extract_image_ref(self, data: Any) -> tuple[str, str] | None:
        fallback_url: str | None = None

        def walk(obj: Any) -> tuple[str, str] | None:
            nonlocal fallback_url
            if isinstance(obj, dict):
                for key, value in obj.items():
                    if isinstance(value, str):
                        if key in {"result", "b64_json", "image"} and self._looks_like_base64(value):
                            return ("base64", value)
                        if self._looks_like_data_url(value):
                            return ("data_url", value)
                        if key in {"url", "image_url", "output_url"} and value.startswith(("http://", "https://")):
                            return ("url", value)
                        if fallback_url is None and self._looks_like_image_url(value):
                            fallback_url = value
                    found = walk(value)
                    if found:
                        return found
            elif isinstance(obj, list):
                for item in obj:
                    found = walk(item)
                    if found:
                        return found
            return None

        direct = walk(data)
        if direct:
            return direct
        if fallback_url:
            return ("url", fallback_url)
        return None

    def _looks_like_base64(self, value: str) -> bool:
        if value.startswith(("iVBOR", "/9j/")):
            return True
        return len(value) > 1000

    def _looks_like_data_url(self, value: str) -> bool:
        return value.startswith("data:image/")

    def _looks_like_image_url(self, value: str) -> bool:
        if not value.startswith(("http://", "https://")):
            return False
        return self._has_image_extension(value)

    def _has_image_extension(self, value: str) -> bool:
        trimmed = value.split("?", 1)[0].split("#", 1)[0]
        suffix = Path(trimmed).suffix.lower()
        return suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}

    def _guess_mime_from_path(self, path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix in {".jpg", ".jpeg"}:
            return "image/jpeg"
        if suffix == ".webp":
            return "image/webp"
        if suffix == ".gif":
            return "image/gif"
        return "image/png"

    def _guess_mime_from_url(self, url: str) -> str | None:
        path = urlparse(url).path
        suffix = Path(path).suffix.lower()
        if suffix in {".jpg", ".jpeg"}:
            return "image/jpeg"
        if suffix == ".webp":
            return "image/webp"
        if suffix == ".gif":
            return "image/gif"
        if suffix == ".png":
            return "image/png"
        return None

    def _build_data_url(self, mime: str, data: bytes) -> str:
        b64 = base64.b64encode(data).decode("ascii")
        return f"data:{mime};base64,{b64}"

    def _parse_data_url(self, data_url: str) -> tuple[str, bytes]:
        if "," not in data_url:
            raise ValueError("返回的 data URL 无效。")
        header, b64 = data_url.split(",", 1)
        if not header.startswith("data:") or ";base64" not in header:
            raise ValueError("返回的 data URL 格式不正确。")
        mime = header[5:].split(";", 1)[0]
        if not mime:
            raise ValueError("返回的 data URL 缺少 MIME。")
        try:
            data = base64.b64decode(b64)
        except binascii.Error as exc:
            raise ValueError("返回的 data URL 无法解码。") from exc
        return mime, data

    async def _fetch_image_as_data_url(self, url: str, client: httpx.AsyncClient) -> str:
        resp = await client.get(url, follow_redirects=True)
        if resp.status_code >= 400:
            raise ValueError(f"参考图片请求失败：HTTP {resp.status_code}")
        content_type = resp.headers.get("Content-Type", "").split(";", 1)[0].strip()
        if not content_type.startswith("image/"):
            guessed = self._guess_mime_from_url(url)
            if not guessed:
                raise ValueError("参考图片 Content-Type 不是图片。")
            content_type = guessed
        return self._build_data_url(content_type, resp.content)

    def _decode_data_url(self, data_url: str) -> bytes:
        _, data = self._parse_data_url(data_url)
        return data

    def _mime_to_ext(self, mime: str) -> str:
        if mime == "image/jpeg":
            return ".jpg"
        if mime == "image/webp":
            return ".webp"
        if mime == "image/gif":
            return ".gif"
        return ".png"

    def _selfie_ref_dir(self) -> Path:
        return self.data_dir / "selfie_refs"

    def _get_selfie_refs_from_config(self) -> list[str]:
        raw = self._config_get("selfie_reference_images", [])
        refs: list[str] = []
        for item in self._extract_config_image_refs(raw):
            value = item.strip()
            if not value:
                continue
            resolved = self._resolve_config_image_ref(value)
            if resolved:
                refs.append(resolved)
        return self._merge_refs(refs, [])

    def _get_all_selfie_refs(self) -> list[str]:
        return self._merge_refs(
            self._get_selfie_refs_from_config(),
            self._list_selfie_ref_paths(),
        )

    def _extract_config_image_refs(self, raw: Any) -> list[str]:
        if not raw:
            return []
        if isinstance(raw, str):
            stripped = raw.strip()
            if stripped.startswith(("{", "[")):
                try:
                    parsed = json.loads(stripped)
                except json.JSONDecodeError:
                    return [raw]
                return self._extract_config_image_refs(parsed)
            return [raw]
        if isinstance(raw, Path):
            return [str(raw)]
        if isinstance(raw, dict):
            refs: list[str] = []
            for key in (
                "path",
                "file",
                "filepath",
                "value",
                "url",
                "image_url",
                "data",
                "data_url",
                "token",
                "attachment_id",
                "file_token",
                "local_path",
                "temp_path",
            ):
                value = raw.get(key)
                if isinstance(value, str) and value.strip():
                    refs.append(value)
            if refs:
                return refs
            for value in raw.values():
                refs.extend(self._extract_config_image_refs(value))
            return refs
        if isinstance(raw, (list, tuple, set)):
            refs: list[str] = []
            for item in raw:
                refs.extend(self._extract_config_image_refs(item))
            return refs
        for attr in (
            "path",
            "file",
            "filepath",
            "value",
            "url",
            "image_url",
            "data",
            "data_url",
            "token",
            "attachment_id",
            "file_token",
            "local_path",
            "temp_path",
        ):
            value = getattr(raw, attr, None)
            if isinstance(value, str) and value.strip():
                return [value]
        return []

    def _resolve_config_image_ref(self, value: str) -> str | None:
        if self._looks_like_data_url(value) or value.startswith(("http://", "https://")):
            return value

        normalized = value.strip()
        if normalized.startswith("/api/file/"):
            token = normalized.rsplit("/", 1)[-1].strip()
            if token:
                resolved = self._resolve_attachment_token(token)
                if resolved:
                    return resolved

        direct_path = Path(normalized)
        if direct_path.is_file():
            return str(direct_path)

        for candidate in self._candidate_local_paths(normalized):
            if candidate.is_file():
                return str(candidate)

        resolved = self._resolve_attachment_token(normalized)
        if resolved:
            return resolved

        return normalized if normalized else None

    def _candidate_local_paths(self, value: str) -> list[Path]:
        raw_path = Path(value)
        candidates: list[Path] = []
        if raw_path.is_absolute():
            return [raw_path]

        for base in self._candidate_data_roots():
            candidates.append(base / value)
            candidates.append(base / "attachments" / value)
            candidates.append(base / "temp" / value)

        candidates.append(Path.cwd() / value)
        return candidates

    def _candidate_data_roots(self) -> list[Path]:
        roots: list[Path] = []
        current = self.data_dir.resolve()
        roots.append(current)

        for parent in current.parents:
            roots.append(parent)
            if parent.name == "data":
                break

        deduped: list[Path] = []
        seen: set[str] = set()
        for path in roots:
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(path)
        return deduped

    def _resolve_attachment_token(self, token: str) -> str | None:
        cleaned = token.strip().strip("/")
        if not cleaned:
            return None

        db_path: Path | None = None
        for root in self._candidate_data_roots():
            candidate = root / "data_v4.db"
            if candidate.is_file():
                db_path = candidate
                break

        if db_path is None:
            return None

        import sqlite3

        try:
            with sqlite3.connect(db_path) as conn:
                row = conn.execute(
                    "SELECT path FROM attachments WHERE attachment_id = ? LIMIT 1",
                    (cleaned,),
                ).fetchone()
        except sqlite3.Error as exc:
            logger.warning("查询附件 token 失败: %s", exc)
            return None

        if not row or not row[0]:
            return None

        stored_path = str(row[0]).strip()
        if not stored_path:
            return None

        path = Path(stored_path)
        if path.is_file():
            return str(path)

        for root in self._candidate_data_roots():
            candidate = root / stored_path
            if candidate.is_file():
                return str(candidate)

        return stored_path

    def _list_selfie_ref_paths(self) -> list[str]:
        ref_dir = self._selfie_ref_dir()
        if not ref_dir.exists():
            return []
        paths = []
        for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp", "*.gif"):
            paths.extend(ref_dir.glob(ext))
        return [str(p) for p in sorted(paths)]

    def _clear_selfie_refs(self) -> int:
        ref_dir = self._selfie_ref_dir()
        if not ref_dir.exists():
            return 0
        count = 0
        for path in ref_dir.iterdir():
            if path.is_file():
                path.unlink()
                count += 1
        return count

    async def _save_selfie_refs(self, refs: list[str], client: httpx.AsyncClient) -> int:
        ref_images = await self._normalize_ref_images(refs, client)
        if not ref_images:
            raise ValueError("未找到可用的参考图片。")
        ref_dir = self._selfie_ref_dir()
        ref_dir.mkdir(parents=True, exist_ok=True)
        count = 0
        for idx, data_url in enumerate(ref_images):
            mime, data = self._parse_data_url(data_url)
            ext = self._mime_to_ext(mime)
            name = datetime.now().strftime("selfie_%Y%m%d_%H%M%S")
            path = ref_dir / f"{name}_{idx}{ext}"
            path.write_bytes(data)
            count += 1
        return count

    async def _read_image_bytes(
        self, image_ref: tuple[str, str], client: httpx.AsyncClient
    ) -> bytes:
        kind, value = image_ref
        if kind == "base64":
            try:
                return base64.b64decode(value)
            except binascii.Error as exc:
                raise ValueError("返回的图片 base64 无法解码。") from exc
        if kind == "data_url":
            return self._decode_data_url(value)
        if kind == "url":
            resp = await client.get(value, follow_redirects=True)
            if resp.status_code >= 400:
                raise ValueError(f"返回的图片 URL 请求失败：HTTP {resp.status_code}")
            return resp.content
        raise ValueError("未识别的图片格式。")

    def _format_size(self, size_bytes: int) -> str:
        if size_bytes >= 1024 * 1024:
            return f"{size_bytes / 1024 / 1024:.1f} MB"
        return f"{size_bytes / 1024:.1f} KB"

    def _merge_refs(self, first: list[str], second: list[str]) -> list[str]:
        seen: set[str] = set()
        merged: list[str] = []
        for item in first + second:
            if item in seen:
                continue
            seen.add(item)
            merged.append(item)
        return merged

    def _extract_refs_from_event(self, event: AstrMessageEvent) -> list[str]:
        refs: list[str] = []
        self._collect_image_refs(event.message_obj, refs)
        refs.extend(self._extract_image_refs_from_text(event.message_str))
        return refs

    def _collect_image_refs(self, obj: Any, refs: list[str]) -> None:
        if obj is None:
            return
        if isinstance(obj, (list, tuple, set)):
            for item in obj:
                self._collect_image_refs(item, refs)
            return
        if isinstance(obj, dict):
            for key, value in obj.items():
                if isinstance(value, str):
                    if self._looks_like_image_ref(value):
                        refs.append(value)
                    elif key in {"url", "image_url"} and value.startswith(("http://", "https://")):
                        refs.append(value)
                    else:
                        self._collect_image_refs(value, refs)
                else:
                    self._collect_image_refs(value, refs)
            return

        for attr in ("url", "image_url", "file", "path", "data", "data_url"):
            value = getattr(obj, attr, None)
            if isinstance(value, str):
                if self._looks_like_image_ref(value):
                    refs.append(value)
                elif attr in {"url", "image_url"} and value.startswith(("http://", "https://")):
                    refs.append(value)

        for attr in ("message_chain", "chain", "components", "content", "message", "reply", "quote", "source"):
            child = getattr(obj, attr, None)
            if child is not None and child is not obj:
                self._collect_image_refs(child, refs)

    def _extract_image_refs_from_text(self, text: str | None) -> list[str]:
        if not text:
            return []
        matches = re.findall(r"(https?://\S+)", text)
        return [m for m in matches if self._looks_like_image_ref(m)]

    def _looks_like_image_ref(self, value: str) -> bool:
        if self._looks_like_data_url(value):
            return True
        if value.startswith(("http://", "https://")):
            return self._has_image_extension(value)
        path = Path(value)
        if not path.is_file():
            return False
        suffix = path.suffix.lower()
        return suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}

    def _write_image(self, image_bytes: bytes) -> Path:
        out_dir = self.data_dir / "generated"
        out_dir.mkdir(parents=True, exist_ok=True)
        name = datetime.now().strftime("resp2img_%Y%m%d_%H%M%S.png")
        path = out_dir / name
        path.write_bytes(image_bytes)
        self._prune_generated_images(out_dir)
        return path

    def _prune_generated_images(self, out_dir: Path) -> None:
        keep_count = self._get_generated_image_keep_count()
        if keep_count < 0:
            return

        files = [path for path in out_dir.glob("resp2img_*.png") if path.is_file()]
        files.sort(key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)

        for path in files[keep_count:]:
            try:
                path.unlink()
            except OSError as exc:
                logger.warning("删除旧生成图片失败: %s", exc)

    async def terminate(self):
        """插件被卸载时触发。"""

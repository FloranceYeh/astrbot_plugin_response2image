import json
from pathlib import Path
import shlex
import time
from typing import Any

import httpx

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
import astrbot.api.message_components as Comp
from astrbot.api.star import Context, Star, StarTools
try:
    from .core.config import PluginConfigReader, normalize_base_url
    from .core.generation import (
        DEFAULT_REFERENCE_PROMPT_EDIT,
        DEFAULT_REFERENCE_PROMPT_SELFIE,
        GenerationResult,
        PLUGIN_RESPONSE_PREFIX,
        WHITE_REFERENCE_IMAGE_NAME,
        build_payload,
        compose_command_fallback_prompt,
        format_elapsed_seconds,
        get_reference_prompt_lines,
        merge_refs,
        mode_label,
        normalize_image_size,
        resolve_command_prompt,
        resolve_generation_inputs,
        resolve_mode,
    )
    from .core.media import ImageMediaService
    from .core.selfie_refs import SelfieReferenceService
    from .core.storage import GeneratedImageStore, PromptPresetStore
except ImportError:
    from core.config import PluginConfigReader, normalize_base_url
    from core.generation import (
        DEFAULT_REFERENCE_PROMPT_EDIT,
        DEFAULT_REFERENCE_PROMPT_SELFIE,
        GenerationResult,
        PLUGIN_RESPONSE_PREFIX,
        WHITE_REFERENCE_IMAGE_NAME,
        build_payload,
        compose_command_fallback_prompt,
        format_elapsed_seconds,
        get_reference_prompt_lines,
        merge_refs,
        mode_label,
        normalize_image_size,
        resolve_command_prompt,
        resolve_generation_inputs,
        resolve_mode,
    )
    from core.media import ImageMediaService
    from core.selfie_refs import SelfieReferenceService
    from core.storage import GeneratedImageStore, PromptPresetStore


class Response2Image(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.data_dir: Path = StarTools.get_data_dir()
        self.config_reader = PluginConfigReader(config)
        self.image_store = GeneratedImageStore(self.data_dir)
        self.preset_store = PromptPresetStore(self.data_dir)
        self.media_service = ImageMediaService(Path(__file__).resolve().parent, self.data_dir)
        self.selfie_ref_service = SelfieReferenceService(
            self.data_dir,
            self.config_reader,
            self.media_service,
        )

    @filter.command_group("r2i")
    def r2i(self):
        """Response2Image 相关命令。"""
        pass

    @r2i.command("help")
    async def r2i_help(self, event: AstrMessageEvent):
        yield self._plain_result(
            event,
            "Response2Image\n"
            "• /r2i img <提示词> [--preset 标题] [--ref 路径] [--size 宽x高]\n    自动判断文生图/改图\n"
            "• /r2i aiimg <提示词> [--preset 标题] [--size 宽x高]\n    文生图\n"
            "• /r2i aiedit <提示词> [--preset 标题] [--ref 路径] [--size 宽x高]\n    图生图\n"
            "• /r2i selfie <提示词> [--preset 标题] [--ref 路径] [--size 宽x高]\n    自拍\n"
            "• /r2i preset list\n    查看全部预设\n"
            "• /r2i preset show <标题>\n    查看某组预设详情\n"
            "• /r2i preset add <标题> <内容> [--ref 路径] [--size 宽x高] [--auto-size]\n    添加或覆盖预设\n"
            "• /r2i preset del <标题>\n    删除某组预设\n"
            "• /r2i selfie_ref set\n    发送或引用图片后执行\n"
            "• /r2i selfie_ref list\n    查看当前参考图\n"
            "• /r2i selfie_ref clear\n    清空命令保存的参考图"
        )

    @r2i.command("img")
    async def img(
        self,
        event: AstrMessageEvent,
        prompt: str = "",
        preset: str = "",
        ref: str = "",
        size: str = "",
    ):
        """自动判断文生图或改图。"""
        raw_prompt = resolve_command_prompt(
            event.message_str,
            "img",
            compose_command_fallback_prompt(prompt, preset=preset, ref=ref, size=size),
        )
        async for result in self._generate(event, raw_prompt, mode="auto", preset=preset, ref=ref, size=size):
            yield result

    @r2i.command("aiimg")
    async def aiimg(
        self,
        event: AstrMessageEvent,
        prompt: str = "",
        preset: str = "",
        size: str = "",
    ):
        """文生图模式。"""
        raw_prompt = resolve_command_prompt(
            event.message_str,
            "aiimg",
            compose_command_fallback_prompt(prompt, preset=preset, size=size),
        )
        async for result in self._generate(event, raw_prompt, mode="text", preset=preset, size=size):
            yield result

    @r2i.command("aiedit")
    async def aiedit(
        self,
        event: AstrMessageEvent,
        prompt: str = "",
        preset: str = "",
        ref: str = "",
        size: str = "",
    ):
        """改图模式。"""
        raw_prompt = resolve_command_prompt(
            event.message_str,
            "aiedit",
            compose_command_fallback_prompt(prompt, preset=preset, ref=ref, size=size),
        )
        async for result in self._generate(event, raw_prompt, mode="edit", preset=preset, ref=ref, size=size):
            yield result

    @r2i.command("selfie")
    async def selfie(
        self,
        event: AstrMessageEvent,
        prompt: str = "",
        preset: str = "",
        ref: str = "",
        size: str = "",
    ):
        """自拍模式。"""
        raw_prompt = resolve_command_prompt(
            event.message_str,
            "selfie",
            compose_command_fallback_prompt(prompt, preset=preset, ref=ref, size=size),
        )
        async for result in self._generate(event, raw_prompt, mode="selfie", preset=preset, ref=ref, size=size):
            yield result

    @r2i.command("preset")
    async def preset(self, event: AstrMessageEvent, action: str = ""):
        """预设提示词管理：查看/添加/删除。"""
        raw_prompt = resolve_command_prompt(event.message_str, "preset", action)
        try:
            tokens = shlex.split(raw_prompt)
        except ValueError as exc:
            yield self._plain_result(event, f"预设命令解析失败：{exc}")
            return

        if not tokens:
            yield self._plain_result(event, self._preset_usage())
            return

        action = tokens[0].strip().lower()
        if action in {"list", "ls", "查看"}:
            yield self._plain_result(event, self._format_preset_list())
            return

        if action in {"show", "get", "详情"}:
            title = " ".join(tokens[1:]).strip()
            if not title:
                yield self._plain_result(event, "请提供要查看的预设标题。")
                return
            yield self._plain_result(event, self._format_preset_detail(title))
            return

        if action in {"add", "set", "save", "添加"}:
            try:
                title, content, ref_urls, image_size, auto_size = self._parse_preset_add_tokens(tokens[1:])
                ref_urls = merge_refs(
                    ref_urls,
                    self.media_service.extract_refs_from_event(event.message_obj, event.message_str),
                )
                auto_size_note = ""
                if auto_size and not image_size:
                    if not ref_urls:
                        raise ValueError("启用 --auto-size 时需要至少一张参考图。")
                    timeout = self.config_reader.get_timeout()
                    async with httpx.AsyncClient(timeout=timeout) as client:
                        original_size, normalized_size = await self.media_service.infer_normalized_size_from_ref(
                            ref_urls[0],
                            client,
                        )
                    image_size = f"{normalized_size[0]}x{normalized_size[1]}"
                    auto_size_note = (
                        f" 已按首张参考图尺寸 {original_size[0]}x{original_size[1]} "
                        f"自动规范为 {image_size}。"
                    )
                preset = self.preset_store.save_preset(
                    title,
                    content,
                    ref_urls=ref_urls,
                    image_size=image_size,
                )
            except ValueError as exc:
                yield self._plain_result(event, str(exc))
                return
            ref_summary = f"{len(preset.ref_urls)} 张参考图" if preset.ref_urls else "无参考图"
            size_summary = preset.image_size or "默认尺寸"
            yield self._plain_result(
                event,
                f"已保存预设《{preset.title}》：{ref_summary}，{size_summary}。{auto_size_note}".strip(),
            )
            return

        if action in {"del", "delete", "remove", "删除"}:
            title = " ".join(tokens[1:]).strip()
            if not title:
                yield self._plain_result(event, "请提供要删除的预设标题。")
                return
            if not self.preset_store.delete_preset(title):
                yield self._plain_result(event, f"未找到预设《{title}》。")
                return
            yield self._plain_result(event, f"已删除预设《{title}》。")
            return

        yield self._plain_result(event, self._preset_usage())

    @r2i.command("selfie_ref")
    async def selfie_ref(self, event: AstrMessageEvent, action: str = ""):
        """自拍参考照管理：设置/查看/删除。"""
        action = (action or "").strip()
        if action in {"设置", "set"}:
            refs = self.media_service.extract_refs_from_event(event.message_obj, event.message_str)
            if not refs:
                yield self._plain_result(event, "请发送或引用图片后再设置自拍参考照。")
                return
            try:
                timeout = self.config_reader.get_timeout()
            except ValueError as exc:
                yield self._plain_result(event, str(exc))
                return
            async with httpx.AsyncClient(timeout=timeout) as client:
                try:
                    count = await self.selfie_ref_service.save_selfie_refs(refs, client)
                except ValueError as exc:
                    yield self._plain_result(event, str(exc))
                    return
            yield self._plain_result(event, f"已保存自拍参考照 {count} 张。")
            return
        if action in {"查看", "list"}:
            config_refs = self.selfie_ref_service.get_selfie_refs_from_config()
            saved_refs = self.selfie_ref_service.list_selfie_ref_paths()
            refs = merge_refs(config_refs, saved_refs)
            if not refs:
                yield self._plain_result(event, "暂无自拍参考照。")
                return
            yield self._plain_result(
                event,
                f"当前共有 {len(refs)} 张自拍参考照（WebUI 配置 {len(config_refs)} 张，命令保存 {len(saved_refs)} 张）。"
            )
            return
        if action in {"删除", "清空", "clear"}:
            count = self.selfie_ref_service.clear_selfie_refs()
            config_count = len(self.selfie_ref_service.get_selfie_refs_from_config())
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
        preset: str = "",
        ref: str = "",
        size: str = "",
    ):
        """
        自动判断文生图或改图。

        Args:
            prompt(string): 图片生成提示词。
            preset(string): 可选预设标题；会先加载预设中的内容、参考图和尺寸，再由当前参数覆盖。
            ref(string): 可选参考图；可用逗号分隔多个参考图，兼容 URL / data:image / 本地文件路径。
            size(string): 可选图片尺寸，例如 1024x1024、1536x1024。
        """
        return await self._run_llm_tool(event, prompt, mode="auto", preset=preset, ref=ref, size=size)

    @filter.llm_tool(name="r2i_aiimg")
    async def llm_r2i_aiimg(
        self,
        event: AstrMessageEvent,
        prompt: str = "",
        preset: str = "",
        size: str = "",
    ):
        """
        文生图。

        Args:
            prompt(string): 图片生成提示词。
            preset(string): 可选预设标题；会先加载预设中的内容和尺寸，再由当前参数覆盖。
            size(string): 可选图片尺寸，例如 1024x1024、1536x1024。
        """
        return await self._run_llm_tool(event, prompt, mode="text", preset=preset, size=size)

    @filter.llm_tool(name="r2i_aiedit")
    async def llm_r2i_aiedit(
        self,
        event: AstrMessageEvent,
        prompt: str = "",
        preset: str = "",
        ref: str = "",
        size: str = "",
    ):
        """
        改图。

        Args:
            prompt(string): 图片编辑提示词。
            preset(string): 可选预设标题；会先加载预设中的内容、参考图和尺寸，再由当前参数覆盖。
            ref(string): 可选参考图；可用逗号分隔多个参考图，兼容 URL / data:image / 本地文件路径。
            size(string): 可选图片尺寸，例如 1024x1024、1536x1024。
        """
        return await self._run_llm_tool(event, prompt, mode="edit", preset=preset, ref=ref, size=size)

    @filter.llm_tool(name="r2i_selfie")
    async def llm_r2i_selfie(
        self,
        event: AstrMessageEvent,
        prompt: str = "",
        preset: str = "",
        ref: str = "",
        size: str = "",
    ):
        """
        自拍。

        Args:
            prompt(string): 自拍图提示词。
            preset(string): 可选预设标题；会先加载预设中的内容、参考图和尺寸，再由当前参数覆盖。
            ref(string): 可选参考图；可用逗号分隔多个参考图，兼容 URL / data:image / 本地文件路径。
            size(string): 可选图片尺寸，例如 1024x1024、1536x1024。
        """
        return await self._run_llm_tool(event, prompt, mode="selfie", preset=preset, ref=ref, size=size)

    @filter.llm_tool(name="r2i_preset_list")
    async def llm_r2i_preset_list(self, event: AstrMessageEvent):
        """
        查看全部预设提示词。

        Returns:
            string: 当前可用的预设列表、尺寸、参考图数量和简短预览。
        """
        return self._with_prefix(self._format_preset_list())

    @filter.llm_tool(name="r2i_preset_show")
    async def llm_r2i_preset_show(self, event: AstrMessageEvent, title: str = ""):
        """
        查看某组预设提示词的详情。

        Args:
            title(string): 预设标题。

        Returns:
            string: 预设的标题、内容、尺寸和参考图详情。
        """
        title = title.strip()
        if not title:
            return self._with_prefix("请提供要查看的预设标题。")
        return self._with_prefix(self._format_preset_detail(title))

    async def _generate(
        self,
        event: AstrMessageEvent,
        raw_prompt: str,
        *,
        mode: str,
        preset: str = "",
        ref: Any = None,
        size: str = "",
    ):
        try:
            inputs = self._resolve_inputs_with_preset(raw_prompt, ref=ref, size=size, preset=preset)
        except ValueError as exc:
            text = str(exc)
            yield self._plain_result(event, text)
            return

        result = await self._generate_result(
            event,
            inputs.prompt,
            ref_urls=inputs.ref_urls,
            mode=mode,
            image_size=inputs.image_size,
        )
        yield result.response

    async def _run_llm_tool(
        self,
        event: AstrMessageEvent,
        raw_prompt: str,
        *,
        mode: str,
        preset: str = "",
        ref: Any = None,
        size: str = "",
    ) -> str:
        try:
            inputs = self._resolve_inputs_with_preset(raw_prompt, ref=ref, size=size, preset=preset)
        except ValueError as exc:
            return self._with_prefix(str(exc))

        result = await self._generate_result(
            event,
            inputs.prompt,
            ref_urls=inputs.ref_urls,
            mode=mode,
            image_size=inputs.image_size,
        )
        if result.has_image:
            if self.config_reader.get_bool("send_generated_image_in_chat", False):
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

        base_url = self.config_reader.get_str("base_url", "")
        api_key = self.config_reader.get_str("api_key", "")
        model = self.config_reader.get_str("model", "")

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
            normalized_base = normalize_base_url(base_url)
        except ValueError as exc:
            text = str(exc)
            return GenerationResult(self._plain_result(event, text), self._with_prefix(text))

        try:
            timeout = self.config_reader.get_timeout()
        except ValueError as exc:
            text = str(exc)
            return GenerationResult(self._plain_result(event, text), self._with_prefix(text))
        try:
            generated_image_keep_count = self.config_reader.get_generated_image_keep_count()
        except ValueError as exc:
            text = str(exc)
            return GenerationResult(self._plain_result(event, text), self._with_prefix(text))

        event_refs = (
            self.media_service.extract_refs_from_event(event.message_obj, event.message_str)
            if mode in {"auto", "edit", "selfie"}
            else []
        )
        if mode == "selfie" and not ref_urls and not event_refs:
            ref_urls = self.selfie_ref_service.get_all_selfie_refs()

        if mode == "text" and (ref_urls or event_refs):
            text = "文生图模式不使用参考图，请改用改图或自拍。"
            return GenerationResult(self._plain_result(event, text), self._with_prefix(text))
        if mode == "edit" and not (ref_urls or event_refs):
            text = "改图需要参考图片，请发送/引用图片，或通过参考图参数传入。"
            return GenerationResult(self._plain_result(event, text), self._with_prefix(text))
        if mode == "selfie" and not (ref_urls or event_refs):
            text = "未设置自拍参考照，请先使用“自拍参考 设置”。"
            return GenerationResult(self._plain_result(event, text), self._with_prefix(text))

        merged_refs = merge_refs(ref_urls, event_refs)
        resolved_mode = resolve_mode(mode, merged_refs)
        request_refs = list(merged_refs)
        if resolved_mode == "text" and self.config_reader.get_bool("text_mode_use_white_reference_image", False):
            request_refs.append(
                str(self.media_service.get_white_reference_image_path(WHITE_REFERENCE_IMAGE_NAME))
            )
        url = normalized_base + "/v1/responses"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
        }
        image_error: str | None = None
        started_at = time.perf_counter()

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                try:
                    ref_images = await self.media_service.normalize_ref_images(request_refs, client)
                except ValueError as exc:
                    text = str(exc)
                    return GenerationResult(self._plain_result(event, text), self._with_prefix(text))
                if resolved_mode in {"edit", "selfie"} and not ref_images:
                    text = "参考图片不可用，请检查图片是否可访问。"
                    return GenerationResult(self._plain_result(event, text), self._with_prefix(text))

                payload = build_payload(
                    prompt,
                    model,
                    ref_images,
                    image_size=image_size,
                    reference_lines=self._get_reference_prompt_lines(resolved_mode),
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

                        image_ref = self.media_service.extract_image_ref(data)
                        if not image_ref:
                            continue
                        try:
                            image_bytes = await self.media_service.read_image_bytes(image_ref, client)
                        except ValueError as exc:
                            image_error = str(exc)
                            logger.warning("图片解析失败: %s", exc)
                            continue

                        file_path = self.image_store.write_image(image_bytes, generated_image_keep_count)
                        resolved_path = str(file_path.resolve())
                        size_str = self.media_service.format_size(len(image_bytes))
                        elapsed_seconds = time.perf_counter() - started_at
                        elapsed_text = format_elapsed_seconds(elapsed_seconds)
                        label = mode_label(resolved_mode)
                        status_text = f"{label} {size_str} {elapsed_text}"
                        llm_lines = [status_text, f"图片路径：{resolved_path}"]
                        if self.config_reader.get_bool("send_generated_image_in_chat", False):
                            llm_lines.append("已将生成的图片发送到当前对话。")
                        llm_text = self._with_prefix("\n".join(llm_lines))
                        image_data = {
                            "type": "image",
                            "path": resolved_path,
                            "size_bytes": len(image_bytes),
                            "size_text": size_str,
                            "mode": resolved_mode,
                            "status": status_text,
                            "elapsed_seconds": round(elapsed_seconds, 3),
                            "elapsed_text": elapsed_text,
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
                            elapsed_seconds=elapsed_seconds,
                        )

            if image_error:
                return GenerationResult(self._plain_result(event, image_error), self._with_prefix(image_error))
            text = "未收到图片结果，请检查模型是否支持 image_generation。"
            return GenerationResult(self._plain_result(event, text), self._with_prefix(text))
        except httpx.HTTPError as exc:
            logger.error(f"请求失败: {exc}")
            text = f"请求失败：{exc}"
            return GenerationResult(self._plain_result(event, text), self._with_prefix(text))

    def _resolve_inputs_with_preset(
        self,
        raw_prompt: str,
        *,
        ref: Any = None,
        size: str = "",
        preset: str = "",
    ):
        default_image_size = normalize_image_size(self.config_reader.get_str("image_size", ""))
        inputs = resolve_generation_inputs(
            raw_prompt,
            ref,
            size,
            preset=preset,
            default_image_size=None,
        )
        if not inputs.preset_title:
            inputs.image_size = inputs.image_size or default_image_size
            return inputs

        preset_item = self.preset_store.get_preset(inputs.preset_title)
        if preset_item is None:
            raise ValueError(f"未找到预设《{inputs.preset_title}》。")

        prompt = preset_item.content.strip()
        if inputs.prompt.strip():
            prompt = f"{prompt}\n{inputs.prompt.strip()}" if prompt else inputs.prompt.strip()

        inputs.prompt = prompt
        inputs.ref_urls = merge_refs(inputs.ref_urls, preset_item.ref_urls)
        inputs.image_size = inputs.image_size or preset_item.image_size or default_image_size
        return inputs

    def _parse_preset_add_tokens(
        self,
        tokens: list[str],
    ) -> tuple[str, str, list[str], str | None, bool]:
        if not tokens:
            raise ValueError(self._preset_usage())

        title = tokens[0].strip()
        if not title:
            raise ValueError("预设标题不能为空。")

        content_parts: list[str] = []
        ref_urls: list[str] = []
        image_size: str | None = None
        auto_size = False

        i = 1
        while i < len(tokens):
            token = tokens[i]
            if token == "--ref":
                if i + 1 >= len(tokens):
                    raise ValueError("缺少 --ref 参数。")
                ref_urls.extend(resolve_generation_inputs("--placeholder", ref=tokens[i + 1]).ref_urls)
                i += 2
                continue
            if token == "--size":
                if i + 1 >= len(tokens):
                    raise ValueError("缺少 --size 参数。")
                image_size = normalize_image_size(tokens[i + 1])
                i += 2
                continue
            if token == "--auto-size":
                auto_size = True
                i += 1
                continue
            content_parts.append(token)
            i += 1

        content = " ".join(content_parts).strip()
        if not content:
            raise ValueError("预设内容不能为空。")
        return title, content, ref_urls, image_size, auto_size

    def _preset_usage(self) -> str:
        return (
            "用法：\n"
            "/r2i preset list\n"
            "/r2i preset show <标题>\n"
            "/r2i preset add <标题> <内容> [--ref ...] [--size 1024x1024] [--auto-size]\n"
            "/r2i preset del <标题>\n"
            "调用示例：/r2i img --preset 日常自拍"
        )

    def _format_preset_list(self) -> str:
        presets = self.preset_store.list_presets()
        if not presets:
            return "暂无预设提示词。"

        lines = [f"当前共有 {len(presets)} 组预设："]
        for index, item in enumerate(presets, start=1):
            preview = item.content.replace("\n", " / ")
            if len(preview) > 28:
                preview = preview[:28] + "..."
            meta: list[str] = []
            if item.image_size:
                meta.append(item.image_size)
            if item.ref_urls:
                meta.append(f"ref {len(item.ref_urls)}")
            meta_text = f" ({', '.join(meta)})" if meta else ""
            lines.append(f"{index}. 《{item.title}》{meta_text} {preview}".rstrip())
        lines.append('调用：/r2i img --preset "标题"')
        return "\n".join(lines)

    def _format_preset_detail(self, title: str) -> str:
        item = self.preset_store.get_preset(title)
        if item is None:
            return f"未找到预设《{title}》。"

        lines = [
            f"标题：{item.title}",
            f"尺寸：{item.image_size or '默认'}",
            f"参考图：{len(item.ref_urls)} 张",
            "内容：",
            item.content,
        ]
        if item.ref_urls:
            lines.append("ref：")
            lines.extend(item.ref_urls)
        return "\n".join(lines)

    def _with_prefix(self, text: str) -> str:
        return f"{PLUGIN_RESPONSE_PREFIX} {text}"

    def _plain_result(self, event: AstrMessageEvent, text: str):
        return event.plain_result(self._with_prefix(text))

    def _get_reference_prompt_lines(self, mode: str) -> list[str]:
        if mode == "selfie":
            return get_reference_prompt_lines(
                mode,
                self.config_reader.get_text("reference_prompt_selfie", DEFAULT_REFERENCE_PROMPT_SELFIE),
            )
        elif mode == "edit":
            return get_reference_prompt_lines(
                mode,
                self.config_reader.get_text("reference_prompt_edit", DEFAULT_REFERENCE_PROMPT_EDIT),
            )
        return get_reference_prompt_lines(mode)

    async def terminate(self):
        """插件被卸载时触发。"""

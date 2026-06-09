from pathlib import Path
import shlex

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools
try:
    from .core.config import PluginConfigReader
    from .core.generation import (
        GenerationMode,
        GenerationTask,
        RefInput,
        compose_command_fallback_prompt,
        resolve_command_prompt,
    )
    from .core.generation_service import GenerationService
    from .core.media import ImageMediaService
    from .core.messages import chat_image_send_failed, format_preset_command_parse_failed, preset_detail_title_required
    from .core.preset_command_service import PresetCommandService
    from .core.preset_resolver import PresetResolver
    from .core.selfie_refs import SelfieReferenceService
    from .core.selfie_ref_command_service import SelfieRefCommandService
    from .core.sender import SendMessageError, Sender
    from .core.storage import GeneratedImageStore, PromptPresetStore
except ImportError:
    from core.config import PluginConfigReader
    from core.generation import (
        GenerationMode,
        GenerationTask,
        RefInput,
        compose_command_fallback_prompt,
        resolve_command_prompt,
    )
    from core.generation_service import GenerationService
    from core.media import ImageMediaService
    from core.messages import chat_image_send_failed, format_preset_command_parse_failed, preset_detail_title_required
    from core.preset_command_service import PresetCommandService
    from core.preset_resolver import PresetResolver
    from core.selfie_refs import SelfieReferenceService
    from core.selfie_ref_command_service import SelfieRefCommandService
    from core.sender import SendMessageError, Sender
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
        self.sender = Sender()
        self.selfie_ref_service = SelfieReferenceService(
            self.data_dir,
            self.config_reader,
            self.media_service,
        )
        self.generation_service = GenerationService(
            self.config_reader,
            self.media_service,
            self.image_store,
            self.selfie_ref_service,
            self.sender,
        )
        self.preset_command_service = PresetCommandService(
            self.config_reader,
            self.preset_store,
            self.media_service,
        )
        self.selfie_ref_command_service = SelfieRefCommandService(
            self.config_reader,
            self.media_service,
            self.selfie_ref_service,
        )
        self.preset_resolver = PresetResolver(self.config_reader, self.preset_store)

    @filter.command_group("r2i")
    def r2i(self):
        """Response2Image 相关命令。"""
        pass

    @r2i.command("help")
    async def r2i_help(self, event: AstrMessageEvent):
        yield self.sender.plain_result(
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

    def _resolve_command_request(
        self,
        event: AstrMessageEvent,
        command_name: str,
        *,
        prompt: str = "",
        preset: str = "",
        ref: str = "",
        size: str = "",
    ) -> tuple[str, str, str, str]:
        message = (event.message_str or "").strip()
        parts = message.split(maxsplit=2)
        matched_raw_message = False

        if len(parts) >= 2 and parts[0].lstrip("/").lower() == "r2i" and parts[1].lower() == command_name:
            matched_raw_message = True
        elif parts and parts[0].lstrip("/").lower() == command_name:
            matched_raw_message = True

        raw_prompt = resolve_command_prompt(
            event.message_str,
            command_name,
            compose_command_fallback_prompt(prompt, preset=preset, ref=ref, size=size),
        )
        if matched_raw_message:
            return raw_prompt, "", "", ""
        return raw_prompt, preset, ref, size

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
        async for result in self._handle_generate_command(
            event,
            "img",
            mode="auto",
            prompt=prompt,
            preset=preset,
            ref=ref,
            size=size,
        ):
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
        async for result in self._handle_generate_command(
            event,
            "aiimg",
            mode="text",
            prompt=prompt,
            preset=preset,
            size=size,
        ):
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
        async for result in self._handle_generate_command(
            event,
            "aiedit",
            mode="edit",
            prompt=prompt,
            preset=preset,
            ref=ref,
            size=size,
        ):
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
        async for result in self._handle_generate_command(
            event,
            "selfie",
            mode="selfie",
            prompt=prompt,
            preset=preset,
            ref=ref,
            size=size,
        ):
            yield result

    async def _handle_generate_command(
        self,
        event: AstrMessageEvent,
        command_name: str,
        *,
        mode: GenerationMode,
        prompt: str = "",
        preset: str = "",
        ref: str = "",
        size: str = "",
    ):
        raw_prompt, preset, ref, size = self._resolve_command_request(
            event,
            command_name,
            prompt=prompt,
            preset=preset,
            ref=ref,
            size=size,
        )
        async for result in self._generate(event, raw_prompt, mode=mode, preset=preset, ref=ref, size=size):
            yield result

    @r2i.command("preset")
    async def preset(self, event: AstrMessageEvent, action: str = ""):
        """预设提示词管理：查看/添加/删除。"""
        raw_prompt = resolve_command_prompt(event.message_str, "preset", action)
        try:
            tokens = shlex.split(raw_prompt)
        except ValueError as exc:
            yield self.sender.plain_result(event, format_preset_command_parse_failed(str(exc)))
            return

        try:
            text = await self.preset_command_service.handle(event, tokens)
        except ValueError as exc:
            text = str(exc)

        yield self.sender.plain_result(event, text)

    @r2i.command("selfie_ref")
    async def selfie_ref(self, event: AstrMessageEvent, action: str = ""):
        """自拍参考照管理：设置/查看/删除。"""
        try:
            text = await self.selfie_ref_command_service.handle(event, action or "")
        except ValueError as exc:
            text = str(exc)

        yield self.sender.plain_result(event, text)

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
        return self.sender.prefix(self.preset_command_service.format_list())

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
            return self.sender.prefix(preset_detail_title_required())
        return self.sender.prefix(self.preset_command_service.format_detail(title))

    async def _generate(
        self,
        event: AstrMessageEvent,
        raw_prompt: str,
        *,
        mode: GenerationMode,
        preset: str = "",
        ref: RefInput = None,
        size: str = "",
    ):
        try:
            inputs = self.preset_resolver.resolve(raw_prompt, ref=ref, size=size, preset=preset)
        except ValueError as exc:
            text = str(exc)
            yield self.sender.plain_result(event, text)
            return

        result = await self._generate_result(
            event,
            self._build_generation_task(inputs.prompt, mode, inputs.ref_urls, inputs.image_size),
        )
        yield result.response

    async def _run_llm_tool(
        self,
        event: AstrMessageEvent,
        raw_prompt: str,
        *,
        mode: GenerationMode,
        preset: str = "",
        ref: RefInput = None,
        size: str = "",
    ) -> str:
        try:
            inputs = self.preset_resolver.resolve(raw_prompt, ref=ref, size=size, preset=preset)
        except ValueError as exc:
            return self.sender.prefix(str(exc))

        result = await self._generate_result(
            event,
            self._build_generation_task(inputs.prompt, mode, inputs.ref_urls, inputs.image_size),
        )
        if result.has_image:
            if self.config_reader.get_bool("send_generated_image_in_chat", False):
                try:
                    await self.sender.send(event, result.response)
                except SendMessageError:
                    failure_note = self.sender.prefix(chat_image_send_failed())
                    return "\n".join([result.llm_text, failure_note])
            return result.llm_text
        return result.llm_text

    def _build_generation_task(
        self,
        prompt: str,
        mode: GenerationMode,
        ref_urls: list[str],
        image_size: str | None,
    ) -> GenerationTask:
        return GenerationTask(
            prompt=prompt,
            mode=mode,
            ref_urls=ref_urls,
            image_size=image_size,
        )

    async def _generate_result(
        self,
        event: AstrMessageEvent,
        task: GenerationTask,
    ) -> GenerationResult:
        return await self.generation_service.generate_result(event, task)

    async def terminate(self):
        """插件被卸载时触发。"""

from dataclasses import dataclass

import httpx
from astrbot.api.event import AstrMessageEvent

try:
    from .config import PluginConfigReader
    from .generation import merge_refs, normalize_image_size, resolve_generation_inputs
    from .media import ImageMediaService
    from .messages import (
        missing_option_argument,
        preset_auto_size_note,
        preset_auto_size_requires_ref,
        preset_content_empty,
        preset_delete_title_required,
        preset_deleted,
        preset_detail_lines,
        preset_detail_ref_header,
        preset_detail_title_required,
        preset_list_call_hint,
        preset_list_empty,
        preset_list_header,
        preset_list_item,
        preset_not_found,
        preset_saved,
        preset_title_empty,
        preset_usage,
    )
    from .storage import PromptPresetStore
except ImportError:
    from core.config import PluginConfigReader
    from core.generation import merge_refs, normalize_image_size, resolve_generation_inputs
    from core.media import ImageMediaService
    from core.messages import (
        missing_option_argument,
        preset_auto_size_note,
        preset_auto_size_requires_ref,
        preset_content_empty,
        preset_delete_title_required,
        preset_deleted,
        preset_detail_lines,
        preset_detail_ref_header,
        preset_detail_title_required,
        preset_list_call_hint,
        preset_list_empty,
        preset_list_header,
        preset_list_item,
        preset_not_found,
        preset_saved,
        preset_title_empty,
        preset_usage,
    )
    from core.storage import PromptPresetStore


@dataclass(slots=True)
class PresetAddRequest:
    title: str
    content: str
    ref_urls: list[str]
    image_size: str | None
    auto_size: bool


class PresetCommandService:
    def __init__(
        self,
        config_reader: PluginConfigReader,
        preset_store: PromptPresetStore,
        media_service: ImageMediaService,
    ):
        self.config_reader = config_reader
        self.preset_store = preset_store
        self.media_service = media_service

    async def handle(self, event: AstrMessageEvent, tokens: list[str]) -> str:
        if not tokens:
            return self.usage()

        normalized_action = self._normalize_action(tokens[0].strip().lower())
        match normalized_action:
            case "list":
                return self.format_list()
            case "show":
                return self._build_detail_text(tokens[1:])
            case "add":
                return await self._save_from_tokens(event, tokens[1:])
            case "delete":
                return self._delete_from_tokens(tokens[1:])
            case _:
                return self.usage()

    def usage(self) -> str:
        return preset_usage()

    def format_list(self) -> str:
        presets = self.preset_store.list_presets()
        if not presets:
            return preset_list_empty()

        lines = [preset_list_header(len(presets))]
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
            lines.append(preset_list_item(index, item.title, meta_text, preview))
        lines.append(preset_list_call_hint())
        return "\n".join(lines)

    def format_detail(self, title: str) -> str:
        item = self.preset_store.get_preset(title)
        if item is None:
            return preset_not_found(title)

        lines = preset_detail_lines(item.title, item.image_size, len(item.ref_urls), item.content)
        if item.ref_urls:
            lines.append(preset_detail_ref_header())
            lines.extend(item.ref_urls)
        return "\n".join(lines)

    def _normalize_action(self, action: str) -> str:
        match action:
            case "list" | "ls" | "查看":
                return "list"
            case "show" | "get" | "详情":
                return "show"
            case "add" | "set" | "save" | "添加":
                return "add"
            case "del" | "delete" | "remove" | "删除":
                return "delete"
            case _:
                return action

    def _build_detail_text(self, tokens: list[str]) -> str:
        title = " ".join(tokens).strip()
        if not title:
            raise ValueError(preset_detail_title_required())
        return self.format_detail(title)

    async def _save_from_tokens(self, event: AstrMessageEvent, tokens: list[str]) -> str:
        preset_request = self._parse_add_tokens(tokens)
        ref_urls = merge_refs(
            preset_request.ref_urls,
            self.media_service.extract_refs_from_event(event.message_obj, event.message_str),
        )
        image_size = preset_request.image_size
        auto_size_note = ""

        if preset_request.auto_size and not image_size:
            image_size, auto_size_note = await self._infer_image_size(ref_urls)

        preset = self.preset_store.save_preset(
            preset_request.title,
            preset_request.content,
            ref_urls=ref_urls,
            image_size=image_size,
        )
        return preset_saved(preset.title, len(preset.ref_urls), preset.image_size, auto_size_note)

    async def _infer_image_size(self, ref_urls: list[str]) -> tuple[str, str]:
        if not ref_urls:
            raise ValueError(preset_auto_size_requires_ref())

        timeout = self.config_reader.get_timeout()
        async with httpx.AsyncClient(timeout=timeout) as client:
            original_size, normalized_size = await self.media_service.infer_normalized_size_from_ref(
                ref_urls[0],
                client,
            )

        image_size = f"{normalized_size[0]}x{normalized_size[1]}"
        note = preset_auto_size_note(
            original_size[0],
            original_size[1],
            normalized_size[0],
            normalized_size[1],
        )
        return image_size, note

    def _delete_from_tokens(self, tokens: list[str]) -> str:
        title = " ".join(tokens).strip()
        if not title:
            raise ValueError(preset_delete_title_required())
        if not self.preset_store.delete_preset(title):
            raise ValueError(preset_not_found(title))
        return preset_deleted(title)

    def _parse_add_tokens(self, tokens: list[str]) -> PresetAddRequest:
        if not tokens:
            raise ValueError(self.usage())

        title = tokens[0].strip()
        if not title:
            raise ValueError(preset_title_empty())

        content_parts: list[str] = []
        ref_urls: list[str] = []
        image_size: str | None = None
        auto_size = False

        index = 1
        while index < len(tokens):
            parsed_option = self._parse_option(tokens, index)
            if parsed_option is None:
                content_parts.append(tokens[index])
                index += 1
                continue

            index, parsed_refs, parsed_size, enable_auto_size = parsed_option
            ref_urls.extend(parsed_refs)
            if parsed_size is not None:
                image_size = parsed_size
            auto_size = auto_size or enable_auto_size

        content = " ".join(content_parts).strip()
        if not content:
            raise ValueError(preset_content_empty())
        return PresetAddRequest(
            title=title,
            content=content,
            ref_urls=ref_urls,
            image_size=image_size,
            auto_size=auto_size,
        )

    def _parse_option(
        self,
        tokens: list[str],
        index: int,
    ) -> tuple[int, list[str], str | None, bool] | None:
        token = tokens[index]
        if token == "--ref":
            if index + 1 >= len(tokens):
                raise ValueError(missing_option_argument("--ref"))
            return index + 2, resolve_generation_inputs("--placeholder", ref=tokens[index + 1]).ref_urls, None, False
        if token == "--size":
            if index + 1 >= len(tokens):
                raise ValueError(missing_option_argument("--size"))
            return index + 2, [], normalize_image_size(tokens[index + 1]), False
        if token == "--auto-size":
            return index + 1, [], None, True
        return None

import httpx
from astrbot.api.event import AstrMessageEvent

try:
    from .config import PluginConfigReader
    from .generation import merge_refs
    from .media import ImageMediaService
    from .messages import (
        selfie_ref_cleared,
        selfie_ref_empty,
        selfie_ref_saved,
        selfie_ref_set_requires_image,
        selfie_ref_summary,
        selfie_ref_usage,
    )
    from .selfie_refs import SelfieReferenceService
except ImportError:
    from core.config import PluginConfigReader
    from core.generation import merge_refs
    from core.media import ImageMediaService
    from core.messages import (
        selfie_ref_cleared,
        selfie_ref_empty,
        selfie_ref_saved,
        selfie_ref_set_requires_image,
        selfie_ref_summary,
        selfie_ref_usage,
    )
    from core.selfie_refs import SelfieReferenceService


class SelfieRefCommandService:
    def __init__(
        self,
        config_reader: PluginConfigReader,
        media_service: ImageMediaService,
        selfie_ref_service: SelfieReferenceService,
    ):
        self.config_reader = config_reader
        self.media_service = media_service
        self.selfie_ref_service = selfie_ref_service

    async def handle(self, event: AstrMessageEvent, action: str) -> str:
        normalized_action = self._normalize_action(action.strip())
        match normalized_action:
            case "set":
                return await self._save_refs(event)
            case "list":
                return self.build_summary()
            case "clear":
                return self.clear_refs()
            case _:
                return selfie_ref_usage()

    def build_summary(self) -> str:
        config_refs = self.selfie_ref_service.get_selfie_refs_from_config()
        saved_refs = self.selfie_ref_service.list_selfie_ref_paths()
        refs = merge_refs(config_refs, saved_refs)
        if not refs:
            return selfie_ref_empty()
        return selfie_ref_summary(len(refs), len(config_refs), len(saved_refs))

    def clear_refs(self) -> str:
        count = self.selfie_ref_service.clear_selfie_refs()
        config_count = len(self.selfie_ref_service.get_selfie_refs_from_config())
        return selfie_ref_cleared(count, config_count)

    def _normalize_action(self, action: str) -> str:
        match action:
            case "设置" | "set":
                return "set"
            case "查看" | "list":
                return "list"
            case "删除" | "清空" | "clear":
                return "clear"
            case _:
                return action

    async def _save_refs(self, event: AstrMessageEvent) -> str:
        refs = self.media_service.extract_refs_from_event(event.message_obj, event.message_str)
        if not refs:
            raise ValueError(selfie_ref_set_requires_image())

        timeout = self.config_reader.get_timeout()
        async with httpx.AsyncClient(timeout=timeout) as client:
            count = await self.selfie_ref_service.save_selfie_refs(refs, client)
        return selfie_ref_saved(count)

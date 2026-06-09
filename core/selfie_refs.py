import json
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

try:
    from .config import PluginConfigReader
    from .generation import merge_refs
    from .media import ImageMediaService
    from .path_resolver import LocalImageRefResolver
except ImportError:
    from core.config import PluginConfigReader
    from core.generation import merge_refs
    from core.media import ImageMediaService
    from core.path_resolver import LocalImageRefResolver

CONFIG_REF_FIELDS = (
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
)


class SelfieReferenceService:
    def __init__(self, data_dir: Path, config_reader: PluginConfigReader, media_service: ImageMediaService):
        self.data_dir = data_dir
        self.config_reader = config_reader
        self.media_service = media_service
        self.local_ref_resolver = LocalImageRefResolver(data_dir)

    def get_selfie_refs_from_config(self) -> list[str]:
        raw = self.config_reader.get("selfie_reference_images", [])
        refs = [
            resolved
            for item in self._extract_config_image_refs(raw)
            if (value := item.strip()) and (resolved := self._resolve_config_image_ref(value))
        ]
        return merge_refs(refs, [])

    def get_all_selfie_refs(self) -> list[str]:
        return merge_refs(self.get_selfie_refs_from_config(), self.list_selfie_ref_paths())

    def list_selfie_ref_paths(self) -> list[str]:
        ref_dir = self._selfie_ref_dir()
        if not ref_dir.exists():
            return []

        paths = [path for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp", "*.gif") for path in ref_dir.glob(ext)]
        return [str(path) for path in sorted(paths)]

    def clear_selfie_refs(self) -> int:
        ref_dir = self._selfie_ref_dir()
        if not ref_dir.exists():
            return 0

        count = 0
        for path in ref_dir.iterdir():
            if path.is_file():
                path.unlink()
                count += 1
        return count

    async def save_selfie_refs(self, refs: list[str], client: httpx.AsyncClient) -> int:
        ref_images = await self.media_service.normalize_ref_images(refs, client)
        if not ref_images:
            raise ValueError("未找到可用的参考图片。")

        ref_dir = self._selfie_ref_dir()
        ref_dir.mkdir(parents=True, exist_ok=True)
        count = 0
        for idx, data_url in enumerate(ref_images):
            mime, data = self.media_service.parse_data_url(data_url)
            ext = self.media_service.mime_to_ext(mime)
            name = datetime.now().strftime("selfie_%Y%m%d_%H%M%S")
            path = ref_dir / f"{name}_{idx}{ext}"
            path.write_bytes(data)
            count += 1
        return count

    def _selfie_ref_dir(self) -> Path:
        return self.data_dir / "selfie_refs"

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
            refs = [value for key in CONFIG_REF_FIELDS if isinstance((value := raw.get(key)), str) and value.strip()]
            if refs:
                return refs

            nested_refs: list[str] = []
            for value in raw.values():
                nested_refs.extend(self._extract_config_image_refs(value))
            return nested_refs
        if isinstance(raw, (list, tuple, set)):
            refs: list[str] = []
            for item in raw:
                refs.extend(self._extract_config_image_refs(item))
            return refs

        for attr in CONFIG_REF_FIELDS:
            value = getattr(raw, attr, None)
            if isinstance(value, str) and value.strip():
                return [value]
        return []

    def _resolve_config_image_ref(self, value: str) -> str | None:
        if self.media_service.looks_like_data_url(value) or value.startswith(("http://", "https://")):
            return value

        normalized = value.strip()
        resolved = self.local_ref_resolver.resolve_local_image_ref(normalized)
        if resolved:
            return resolved
        return normalized or None

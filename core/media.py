import base64
import binascii
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

try:
    from .messages import (
        ref_image_content_type_invalid,
        ref_image_empty,
        ref_image_not_found,
        ref_image_request_failed,
        returned_data_url_decode_failed,
        returned_data_url_format_invalid,
        returned_data_url_invalid,
        returned_data_url_missing_mime,
        returned_image_base64_invalid,
        returned_image_request_failed,
        unknown_image_format,
        white_reference_image_missing,
    )
    from .image_probe import ImageProbe
    from .image_ref_inspector import ImageRefInspector
    from .path_resolver import LocalImageRefResolver
except ImportError:
    from core.messages import (
        ref_image_content_type_invalid,
        ref_image_empty,
        ref_image_not_found,
        ref_image_request_failed,
        returned_data_url_decode_failed,
        returned_data_url_format_invalid,
        returned_data_url_invalid,
        returned_data_url_missing_mime,
        returned_image_base64_invalid,
        returned_image_request_failed,
        unknown_image_format,
        white_reference_image_missing,
    )
    from core.image_probe import ImageProbe
    from core.image_ref_inspector import ImageRefInspector
    from core.path_resolver import LocalImageRefResolver


class ImageMediaService:
    def __init__(self, plugin_dir: Path, data_dir: Path | None = None):
        self.plugin_dir = plugin_dir
        self.data_dir = data_dir.resolve() if data_dir else None
        self.image_probe = ImageProbe()
        self.ref_inspector = ImageRefInspector()
        self.local_ref_resolver = LocalImageRefResolver(self.data_dir)

    def get_white_reference_image_path(self, image_name: str) -> Path:
        image_path = self.plugin_dir / image_name
        if not image_path.is_file():
            raise ValueError(white_reference_image_missing(str(image_path)))
        return image_path

    async def normalize_ref_images(self, refs: list[str], client: httpx.AsyncClient) -> list[str]:
        normalized: list[str] = []
        for ref in refs:
            ref = ref.strip()
            if not ref:
                continue
            if self.looks_like_data_url(ref):
                normalized.append(ref)
                continue
            local_ref = self.resolve_local_image_ref(ref)
            if local_ref:
                path = Path(local_ref)
                mime = self.guess_mime_from_path(path)
                data = path.read_bytes()
                normalized.append(self.build_data_url(mime, data))
                continue
            if ref.startswith(("http://", "https://")):
                data_url = await self.fetch_image_as_data_url(ref, client)
                normalized.append(data_url)
                continue
            path = Path(ref)
            if not path.is_file():
                raise ValueError(ref_image_not_found(ref))
            mime = self.guess_mime_from_path(path)
            data = path.read_bytes()
            normalized.append(self.build_data_url(mime, data))
        return normalized

    def extract_image_ref(self, data: Any) -> tuple[str, str] | None:
        return self.ref_inspector.extract_generated_image_ref(data)

    async def read_image_bytes(self, image_ref: tuple[str, str], client: httpx.AsyncClient) -> bytes:
        kind, value = image_ref
        if kind == "base64":
            try:
                return base64.b64decode(value)
            except binascii.Error as exc:
                raise ValueError(returned_image_base64_invalid()) from exc
        if kind == "data_url":
            _, data = self.parse_data_url(value)
            return data
        if kind == "url":
            resp = await client.get(value, follow_redirects=True)
            if resp.status_code >= 400:
                raise ValueError(returned_image_request_failed(resp.status_code))
            return resp.content
        raise ValueError(unknown_image_format())

    async def read_ref_bytes(self, ref: str, client: httpx.AsyncClient) -> bytes:
        ref = ref.strip()
        if not ref:
            raise ValueError(ref_image_empty())
        if self.looks_like_data_url(ref):
            _, data = self.parse_data_url(ref)
            return data

        local_ref = self.resolve_local_image_ref(ref)
        if local_ref:
            return Path(local_ref).read_bytes()

        if ref.startswith(("http://", "https://")):
            resp = await client.get(ref, follow_redirects=True)
            if resp.status_code >= 400:
                path_hint = urlparse(ref).path or ref
                raise ValueError(ref_image_request_failed(resp.status_code, path_hint))
            content_type = resp.headers.get("Content-Type", "").split(";", 1)[0].strip()
            if not content_type.startswith("image/") and not self.guess_mime_from_url(ref):
                raise ValueError(ref_image_content_type_invalid())
            return resp.content

        path = Path(ref)
        if not path.is_file():
            raise ValueError(ref_image_not_found(ref))
        return path.read_bytes()

    async def infer_normalized_size_from_ref(
        self,
        ref: str,
        client: httpx.AsyncClient,
        *,
        divisor: int = 16,
    ) -> tuple[tuple[int, int], tuple[int, int]]:
        data = await self.read_ref_bytes(ref, client)
        original = self.image_probe.get_image_dimensions(data)
        normalized = self.image_probe.normalize_dimensions(original[0], original[1], divisor=divisor)
        return original, normalized

    def format_size(self, size_bytes: int) -> str:
        return self.image_probe.format_size(size_bytes)

    def get_image_dimensions(self, data: bytes) -> tuple[int, int]:
        return self.image_probe.get_image_dimensions(data)

    def normalize_dimensions(self, width: int, height: int, *, divisor: int = 16) -> tuple[int, int]:
        return self.image_probe.normalize_dimensions(width, height, divisor=divisor)

    def extract_refs_from_event(self, message_obj: Any, message_str: str | None) -> list[str]:
        return self.ref_inspector.extract_refs_from_event(message_obj, message_str)

    def looks_like_base64(self, value: str) -> bool:
        return self.ref_inspector.looks_like_base64(value)

    def looks_like_data_url(self, value: str) -> bool:
        return self.ref_inspector.looks_like_data_url(value)

    def looks_like_image_url(self, value: str) -> bool:
        return self.ref_inspector.looks_like_image_url(value)

    def looks_like_image_ref(self, value: str) -> bool:
        return self.ref_inspector.looks_like_image_ref(value)

    def has_image_extension(self, value: str) -> bool:
        return self.ref_inspector.has_image_extension(value)

    def guess_mime_from_path(self, path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix in {".jpg", ".jpeg"}:
            return "image/jpeg"
        if suffix == ".webp":
            return "image/webp"
        if suffix == ".gif":
            return "image/gif"
        return "image/png"

    def guess_mime_from_url(self, url: str) -> str | None:
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

    def build_data_url(self, mime: str, data: bytes) -> str:
        b64 = base64.b64encode(data).decode("ascii")
        return f"data:{mime};base64,{b64}"

    def parse_data_url(self, data_url: str) -> tuple[str, bytes]:
        if "," not in data_url:
            raise ValueError(returned_data_url_invalid())
        header, b64 = data_url.split(",", 1)
        if not header.startswith("data:") or ";base64" not in header:
            raise ValueError(returned_data_url_format_invalid())
        mime = header[5:].split(";", 1)[0]
        if not mime:
            raise ValueError(returned_data_url_missing_mime())
        try:
            data = base64.b64decode(b64)
        except binascii.Error as exc:
            raise ValueError(returned_data_url_decode_failed()) from exc
        return mime, data

    async def fetch_image_as_data_url(self, url: str, client: httpx.AsyncClient) -> str:
        local_ref = self.resolve_local_image_ref(url)
        if local_ref:
            path = Path(local_ref)
            mime = self.guess_mime_from_path(path)
            return self.build_data_url(mime, path.read_bytes())

        resp = await client.get(url, follow_redirects=True)
        if resp.status_code >= 400:
            path_hint = urlparse(url).path or url
            raise ValueError(ref_image_request_failed(resp.status_code, path_hint))
        content_type = resp.headers.get("Content-Type", "").split(";", 1)[0].strip()
        if not content_type.startswith("image/"):
            guessed = self.guess_mime_from_url(url)
            if not guessed:
                raise ValueError(ref_image_content_type_invalid())
            content_type = guessed
        return self.build_data_url(content_type, resp.content)

    def mime_to_ext(self, mime: str) -> str:
        if mime == "image/jpeg":
            return ".jpg"
        if mime == "image/webp":
            return ".webp"
        if mime == "image/gif":
            return ".gif"
        return ".png"

    def resolve_local_image_ref(self, value: str) -> str | None:
        return self.local_ref_resolver.resolve_local_image_ref(value)

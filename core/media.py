import base64
import binascii
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx


class ImageMediaService:
    def __init__(self, plugin_dir: Path):
        self.plugin_dir = plugin_dir

    def get_white_reference_image_path(self, image_name: str) -> Path:
        image_path = self.plugin_dir / image_name
        if not image_path.is_file():
            raise ValueError(f"白图参考文件不存在: {image_path}")
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
            if ref.startswith(("http://", "https://")):
                data_url = await self.fetch_image_as_data_url(ref, client)
                normalized.append(data_url)
                continue
            path = Path(ref)
            if not path.is_file():
                raise ValueError(f"参考图片不存在: {ref}")
            mime = self.guess_mime_from_path(path)
            data = path.read_bytes()
            normalized.append(self.build_data_url(mime, data))
        return normalized

    def extract_image_ref(self, data: Any) -> tuple[str, str] | None:
        fallback_url: str | None = None

        def walk(obj: Any) -> tuple[str, str] | None:
            nonlocal fallback_url
            if isinstance(obj, dict):
                for key, value in obj.items():
                    if isinstance(value, str):
                        if key in {"result", "b64_json", "image"} and self.looks_like_base64(value):
                            return ("base64", value)
                        if self.looks_like_data_url(value):
                            return ("data_url", value)
                        if key in {"url", "image_url", "output_url"} and value.startswith(
                            ("http://", "https://")
                        ):
                            return ("url", value)
                        if fallback_url is None and self.looks_like_image_url(value):
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

    async def read_image_bytes(self, image_ref: tuple[str, str], client: httpx.AsyncClient) -> bytes:
        kind, value = image_ref
        if kind == "base64":
            try:
                return base64.b64decode(value)
            except binascii.Error as exc:
                raise ValueError("返回的图片 base64 无法解码。") from exc
        if kind == "data_url":
            return self.decode_data_url(value)
        if kind == "url":
            resp = await client.get(value, follow_redirects=True)
            if resp.status_code >= 400:
                raise ValueError(f"返回的图片 URL 请求失败：HTTP {resp.status_code}")
            return resp.content
        raise ValueError("未识别的图片格式。")

    def format_size(self, size_bytes: int) -> str:
        if size_bytes >= 1024 * 1024:
            return f"{size_bytes / 1024 / 1024:.1f} MB"
        return f"{size_bytes / 1024:.1f} KB"

    def extract_refs_from_event(self, message_obj: Any, message_str: str | None) -> list[str]:
        refs: list[str] = []
        self._collect_image_refs(message_obj, refs)
        refs.extend(self._extract_image_refs_from_text(message_str))
        return refs

    def looks_like_base64(self, value: str) -> bool:
        if value.startswith(("iVBOR", "/9j/")):
            return True
        return len(value) > 1000

    def looks_like_data_url(self, value: str) -> bool:
        return value.startswith("data:image/")

    def looks_like_image_url(self, value: str) -> bool:
        if not value.startswith(("http://", "https://")):
            return False
        return self.has_image_extension(value)

    def looks_like_image_ref(self, value: str) -> bool:
        if self.looks_like_data_url(value):
            return True
        if value.startswith(("http://", "https://")):
            return self.has_image_extension(value)
        path = Path(value)
        if not path.is_file():
            return False
        suffix = path.suffix.lower()
        return suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}

    def has_image_extension(self, value: str) -> bool:
        trimmed = value.split("?", 1)[0].split("#", 1)[0]
        suffix = Path(trimmed).suffix.lower()
        return suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}

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

    async def fetch_image_as_data_url(self, url: str, client: httpx.AsyncClient) -> str:
        resp = await client.get(url, follow_redirects=True)
        if resp.status_code >= 400:
            raise ValueError(f"参考图片请求失败：HTTP {resp.status_code}")
        content_type = resp.headers.get("Content-Type", "").split(";", 1)[0].strip()
        if not content_type.startswith("image/"):
            guessed = self.guess_mime_from_url(url)
            if not guessed:
                raise ValueError("参考图片 Content-Type 不是图片。")
            content_type = guessed
        return self.build_data_url(content_type, resp.content)

    def decode_data_url(self, data_url: str) -> bytes:
        _, data = self.parse_data_url(data_url)
        return data

    def mime_to_ext(self, mime: str) -> str:
        if mime == "image/jpeg":
            return ".jpg"
        if mime == "image/webp":
            return ".webp"
        if mime == "image/gif":
            return ".gif"
        return ".png"

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
                    if self.looks_like_image_ref(value):
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
                if self.looks_like_image_ref(value):
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
        return [m for m in matches if self.looks_like_image_ref(m)]

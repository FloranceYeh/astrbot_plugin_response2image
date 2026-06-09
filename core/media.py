import base64
import binascii
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

try:
    from .path_resolver import LocalImageRefResolver
except ImportError:
    from core.path_resolver import LocalImageRefResolver


class ImageMediaService:
    def __init__(self, plugin_dir: Path, data_dir: Path | None = None):
        self.plugin_dir = plugin_dir
        self.data_dir = data_dir.resolve() if data_dir else None
        self.local_ref_resolver = LocalImageRefResolver(self.data_dir)

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
            _, data = self.parse_data_url(value)
            return data
        if kind == "url":
            resp = await client.get(value, follow_redirects=True)
            if resp.status_code >= 400:
                raise ValueError(f"返回的图片 URL 请求失败：HTTP {resp.status_code}")
            return resp.content
        raise ValueError("未识别的图片格式。")

    async def read_ref_bytes(self, ref: str, client: httpx.AsyncClient) -> bytes:
        ref = ref.strip()
        if not ref:
            raise ValueError("参考图片为空。")
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
                raise ValueError(f"参考图片请求失败：HTTP {resp.status_code} ({path_hint})")
            content_type = resp.headers.get("Content-Type", "").split(";", 1)[0].strip()
            if not content_type.startswith("image/") and not self.guess_mime_from_url(ref):
                raise ValueError("参考图片 Content-Type 不是图片。")
            return resp.content

        path = Path(ref)
        if not path.is_file():
            raise ValueError(f"参考图片不存在: {ref}")
        return path.read_bytes()

    async def infer_normalized_size_from_ref(
        self,
        ref: str,
        client: httpx.AsyncClient,
        *,
        divisor: int = 16,
    ) -> tuple[tuple[int, int], tuple[int, int]]:
        data = await self.read_ref_bytes(ref, client)
        original = self.get_image_dimensions(data)
        normalized = self.normalize_dimensions(original[0], original[1], divisor=divisor)
        return original, normalized

    def format_size(self, size_bytes: int) -> str:
        if size_bytes >= 1024 * 1024:
            return f"{size_bytes / 1024 / 1024:.1f} MB"
        return f"{size_bytes / 1024:.1f} KB"

    def get_image_dimensions(self, data: bytes) -> tuple[int, int]:
        if len(data) < 10:
            raise ValueError("图片文件过小，无法解析尺寸。")

        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            if len(data) < 24:
                raise ValueError("PNG 图片头不完整，无法解析尺寸。")
            width = int.from_bytes(data[16:20], "big")
            height = int.from_bytes(data[20:24], "big")
            return self._validate_dimensions(width, height)

        if data.startswith((b"GIF87a", b"GIF89a")):
            width = int.from_bytes(data[6:8], "little")
            height = int.from_bytes(data[8:10], "little")
            return self._validate_dimensions(width, height)

        if data.startswith(b"\xff\xd8"):
            return self._parse_jpeg_dimensions(data)

        if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            return self._parse_webp_dimensions(data)

        raise ValueError("暂不支持解析该参考图片的尺寸。")

    def normalize_dimensions(self, width: int, height: int, *, divisor: int = 16) -> tuple[int, int]:
        if divisor <= 0:
            raise ValueError("尺寸规范除数必须大于 0。")
        return (
            self._nearest_multiple(width, divisor),
            self._nearest_multiple(height, divisor),
        )

    def extract_refs_from_event(self, message_obj: Any, message_str: str | None) -> list[str]:
        refs: list[str] = []
        self._collect_image_refs(message_obj, refs)
        refs.extend(self._extract_image_refs_from_text(message_str))
        return list(dict.fromkeys(refs))

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
        local_ref = self.resolve_local_image_ref(url)
        if local_ref:
            path = Path(local_ref)
            mime = self.guess_mime_from_path(path)
            return self.build_data_url(mime, path.read_bytes())

        resp = await client.get(url, follow_redirects=True)
        if resp.status_code >= 400:
            path_hint = urlparse(url).path or url
            raise ValueError(f"参考图片请求失败：HTTP {resp.status_code} ({path_hint})")
        content_type = resp.headers.get("Content-Type", "").split(";", 1)[0].strip()
        if not content_type.startswith("image/"):
            guessed = self.guess_mime_from_url(url)
            if not guessed:
                raise ValueError("参考图片 Content-Type 不是图片。")
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

    def _parse_jpeg_dimensions(self, data: bytes) -> tuple[int, int]:
        sof_markers = {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }
        index = 2
        while index < len(data):
            while index < len(data) and data[index] != 0xFF:
                index += 1
            while index < len(data) and data[index] == 0xFF:
                index += 1
            if index >= len(data):
                break

            marker = data[index]
            index += 1
            if marker in {0xD8, 0xD9, 0x01} or 0xD0 <= marker <= 0xD7:
                continue
            if index + 2 > len(data):
                break

            segment_length = int.from_bytes(data[index:index + 2], "big")
            if segment_length < 2 or index + segment_length > len(data):
                break
            if marker in sof_markers:
                if segment_length < 7 or index + 7 > len(data):
                    break
                height = int.from_bytes(data[index + 3:index + 5], "big")
                width = int.from_bytes(data[index + 5:index + 7], "big")
                return self._validate_dimensions(width, height)
            index += segment_length

        raise ValueError("JPEG 图片头无有效尺寸信息。")

    def _parse_webp_dimensions(self, data: bytes) -> tuple[int, int]:
        if len(data) < 30:
            raise ValueError("WEBP 图片头不完整，无法解析尺寸。")

        chunk = data[12:16]
        if chunk == b"VP8X":
            width = 1 + int.from_bytes(data[24:27], "little")
            height = 1 + int.from_bytes(data[27:30], "little")
            return self._validate_dimensions(width, height)

        if chunk == b"VP8 ":
            if data[23:26] != b"\x9d\x01\x2a":
                raise ValueError("WEBP VP8 图片头无有效尺寸信息。")
            width = int.from_bytes(data[26:28], "little") & 0x3FFF
            height = int.from_bytes(data[28:30], "little") & 0x3FFF
            return self._validate_dimensions(width, height)

        if chunk == b"VP8L":
            if len(data) < 25 or data[20] != 0x2F:
                raise ValueError("WEBP VP8L 图片头无有效尺寸信息。")
            b0, b1, b2, b3 = data[21:25]
            width = 1 + (((b1 & 0x3F) << 8) | b0)
            height = 1 + (((b3 & 0x0F) << 10) | (b2 << 2) | ((b1 & 0xC0) >> 6))
            return self._validate_dimensions(width, height)

        raise ValueError("暂不支持解析该 WEBP 参考图片的尺寸。")

    def _validate_dimensions(self, width: int, height: int) -> tuple[int, int]:
        if width <= 0 or height <= 0:
            raise ValueError("图片尺寸无效。")
        return width, height

    def _nearest_multiple(self, value: int, divisor: int) -> int:
        if value <= divisor:
            return divisor
        lower = max(divisor, (value // divisor) * divisor)
        upper = max(divisor, ((value + divisor - 1) // divisor) * divisor)
        if value - lower <= upper - value:
            return lower
        return upper

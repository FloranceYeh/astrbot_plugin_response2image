import re
from pathlib import Path
from typing import Any


class ImageRefInspector:
    def extract_generated_image_ref(self, data: Any) -> tuple[str, str] | None:
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
                        if key in {"url", "image_url", "output_url"} and value.startswith(("http://", "https://")):
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
        return path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"}

    def has_image_extension(self, value: str) -> bool:
        trimmed = value.split("?", 1)[0].split("#", 1)[0]
        return Path(trimmed).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"}

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
        return [match for match in matches if self.looks_like_image_ref(match)]

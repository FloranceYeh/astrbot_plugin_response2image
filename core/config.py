from typing import Any

import httpx


class PluginConfigReader:
    def __init__(self, config: Any):
        self._config = config

    def get(self, key: str, default: Any) -> Any:
        if self._config is None:
            return default
        if isinstance(self._config, dict):
            return self._config.get(key, default)
        if hasattr(self._config, "get"):
            return self._config.get(key, default)
        return getattr(self._config, key, default)

    def get_str(self, key: str, default: str = "") -> str:
        value = self.get(key, default)
        if value is None:
            return ""
        return str(value).strip()

    def get_text(self, key: str, default: str = "") -> str:
        value = self.get(key, default)
        return value if isinstance(value, str) else default

    def get_bool(self, key: str, default: bool = False) -> bool:
        return bool(self.get(key, default))

    def get_timeout(self) -> httpx.Timeout:
        try:
            timeout_seconds = int(self.get("timeout_seconds", 120))
        except (TypeError, ValueError) as exc:
            raise ValueError("插件配置 timeout_seconds 无效。") from exc
        if timeout_seconds <= 0:
            raise ValueError("插件配置 timeout_seconds 必须大于 0。")
        return httpx.Timeout(timeout_seconds)

    def get_generated_image_keep_count(self) -> int:
        try:
            keep_count = int(self.get("generated_image_keep_count", -1))
        except (TypeError, ValueError) as exc:
            raise ValueError("插件配置 generated_image_keep_count 无效。") from exc
        if keep_count == -1 or keep_count > 0:
            return keep_count
        raise ValueError("插件配置 generated_image_keep_count 必须为 -1 或大于 0。")


def normalize_base_url(base_url: str) -> str:
    base = base_url.strip()
    if not base:
        raise ValueError("Base URL 不能为空。")
    if not base.lower().startswith(("http://", "https://")):
        raise ValueError("Base URL 必须以 http:// 或 https:// 开头。")
    base = base.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return base

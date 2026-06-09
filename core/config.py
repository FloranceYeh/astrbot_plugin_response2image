from typing import Any

import httpx

try:
    from .messages import (
        base_url_required,
        base_url_scheme_invalid,
        config_keep_count_invalid,
        config_retry_count_invalid,
        config_value_invalid,
        config_value_must_be_positive,
    )
except ImportError:
    from core.messages import (
        base_url_required,
        base_url_scheme_invalid,
        config_keep_count_invalid,
        config_retry_count_invalid,
        config_value_invalid,
        config_value_must_be_positive,
    )


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
            raise ValueError(config_value_invalid("timeout_seconds")) from exc
        if timeout_seconds <= 0:
            raise ValueError(config_value_must_be_positive("timeout_seconds"))
        return httpx.Timeout(timeout_seconds)

    def get_generation_retry_count(self) -> int:
        try:
            retry_count = int(self.get("generation_retry_count", 2))
        except (TypeError, ValueError) as exc:
            raise ValueError(config_value_invalid("generation_retry_count")) from exc
        if retry_count >= 0:
            return retry_count
        raise ValueError(config_retry_count_invalid())

    def get_generated_image_keep_count(self) -> int:
        try:
            keep_count = int(self.get("generated_image_keep_count", -1))
        except (TypeError, ValueError) as exc:
            raise ValueError(config_value_invalid("generated_image_keep_count")) from exc
        if keep_count == -1 or keep_count > 0:
            return keep_count
        raise ValueError(config_keep_count_invalid())


def normalize_base_url(base_url: str) -> str:
    base = base_url.strip()
    if not base:
        raise ValueError(base_url_required())
    if not base.lower().startswith(("http://", "https://")):
        raise ValueError(base_url_scheme_invalid())
    base = base.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return base

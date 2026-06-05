import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from astrbot.api import logger


@dataclass
class PromptPreset:
    title: str
    content: str
    ref_urls: list[str]
    image_size: str | None
    created_at: str
    updated_at: str


class GeneratedImageStore:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir

    def write_image(self, image_bytes: bytes, keep_count: int) -> Path:
        out_dir = self.data_dir / "generated"
        out_dir.mkdir(parents=True, exist_ok=True)
        name = datetime.now().strftime("resp2img_%Y%m%d_%H%M%S.png")
        path = out_dir / name
        path.write_bytes(image_bytes)
        self.prune_generated_images(out_dir, keep_count)
        return path

    def prune_generated_images(self, out_dir: Path, keep_count: int) -> None:
        if keep_count < 0:
            return

        files = [path for path in out_dir.glob("resp2img_*.png") if path.is_file()]
        files.sort(key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)

        for path in files[keep_count:]:
            try:
                path.unlink()
            except OSError as exc:
                logger.warning("删除旧生成图片失败: %s", exc)


class PromptPresetStore:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.path = data_dir / "prompt_presets.json"

    def list_presets(self) -> list[PromptPreset]:
        return self._sort_presets(self._load_presets().values())

    def get_preset(self, title: str) -> PromptPreset | None:
        normalized_title = self._normalize_title(title)
        if not normalized_title:
            return None
        return self._load_presets().get(normalized_title)

    def save_preset(
        self,
        title: str,
        content: str,
        *,
        ref_urls: list[str] | None = None,
        image_size: str | None = None,
    ) -> PromptPreset:
        normalized_title = self._normalize_title(title)
        normalized_content = str(content).strip()
        if not normalized_title:
            raise ValueError("预设标题不能为空。")
        if not normalized_content:
            raise ValueError("预设内容不能为空。")

        refs = [item.strip() for item in (ref_urls or []) if str(item).strip()]
        presets = self._load_presets()
        now = datetime.now().isoformat(timespec="seconds")
        existing = presets.get(normalized_title)
        preset = PromptPreset(
            title=normalized_title,
            content=normalized_content,
            ref_urls=refs,
            image_size=image_size,
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        presets[normalized_title] = preset
        self._write_presets(presets)
        return preset

    def delete_preset(self, title: str) -> bool:
        normalized_title = self._normalize_title(title)
        if not normalized_title:
            return False
        presets = self._load_presets()
        if normalized_title not in presets:
            return False
        del presets[normalized_title]
        self._write_presets(presets)
        return True

    def _load_presets(self) -> dict[str, PromptPreset]:
        if not self.path.is_file():
            return {}

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("读取预设提示词失败: %s", exc)
            return {}

        if not isinstance(raw, list):
            logger.warning("预设提示词文件格式无效: %s", self.path)
            return {}

        presets: dict[str, PromptPreset] = {}
        for item in raw:
            if not isinstance(item, dict):
                continue
            title = self._normalize_title(item.get("title", ""))
            content = str(item.get("content", "")).strip()
            if not title or not content:
                continue
            ref_urls = [
                str(ref).strip()
                for ref in item.get("ref_urls", [])
                if isinstance(ref, str) and ref.strip()
            ]
            image_size = item.get("image_size")
            if image_size is not None:
                image_size = str(image_size).strip() or None
            presets[title] = PromptPreset(
                title=title,
                content=content,
                ref_urls=ref_urls,
                image_size=image_size,
                created_at=str(item.get("created_at", "")).strip(),
                updated_at=str(item.get("updated_at", "")).strip(),
            )
        return presets

    def _write_presets(self, presets: dict[str, PromptPreset]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        payload = [asdict(preset) for preset in self._sort_presets(presets.values())]
        temp_path = self.path.with_suffix(".json.tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(self.path)

    def _sort_presets(self, presets) -> list[PromptPreset]:
        return sorted(
            list(presets),
            key=lambda item: (item.updated_at, item.title),
            reverse=True,
        )

    def _normalize_title(self, title: str) -> str:
        return str(title).strip()

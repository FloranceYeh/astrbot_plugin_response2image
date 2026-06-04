import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from astrbot.api import logger

try:
    from .generation import merge_refs
except ImportError:
    from core.generation import merge_refs


class SelfieReferenceService:
    def __init__(self, data_dir: Path, config_reader: Any, media_service: Any):
        self.data_dir = data_dir
        self.config_reader = config_reader
        self.media_service = media_service

    def get_selfie_refs_from_config(self) -> list[str]:
        raw = self.config_reader.get("selfie_reference_images", [])
        refs: list[str] = []
        for item in self._extract_config_image_refs(raw):
            value = item.strip()
            if not value:
                continue
            resolved = self._resolve_config_image_ref(value)
            if resolved:
                refs.append(resolved)
        return merge_refs(refs, [])

    def get_all_selfie_refs(self) -> list[str]:
        return merge_refs(self.get_selfie_refs_from_config(), self.list_selfie_ref_paths())

    def list_selfie_ref_paths(self) -> list[str]:
        ref_dir = self._selfie_ref_dir()
        if not ref_dir.exists():
            return []
        paths = []
        for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp", "*.gif"):
            paths.extend(ref_dir.glob(ext))
        return [str(p) for p in sorted(paths)]

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
            refs: list[str] = []
            for key in (
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
            ):
                value = raw.get(key)
                if isinstance(value, str) and value.strip():
                    refs.append(value)
            if refs:
                return refs
            for value in raw.values():
                refs.extend(self._extract_config_image_refs(value))
            return refs
        if isinstance(raw, (list, tuple, set)):
            refs: list[str] = []
            for item in raw:
                refs.extend(self._extract_config_image_refs(item))
            return refs
        for attr in (
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
        ):
            value = getattr(raw, attr, None)
            if isinstance(value, str) and value.strip():
                return [value]
        return []

    def _resolve_config_image_ref(self, value: str) -> str | None:
        if self.media_service.looks_like_data_url(value) or value.startswith(("http://", "https://")):
            return value

        normalized = value.strip()
        if normalized.startswith("/api/file/"):
            token = normalized.rsplit("/", 1)[-1].strip()
            if token:
                resolved = self._resolve_attachment_token(token)
                if resolved:
                    return resolved

        direct_path = Path(normalized)
        if direct_path.is_file():
            return str(direct_path)

        for candidate in self._candidate_local_paths(normalized):
            if candidate.is_file():
                return str(candidate)

        resolved = self._resolve_attachment_token(normalized)
        if resolved:
            return resolved

        return normalized if normalized else None

    def _candidate_local_paths(self, value: str) -> list[Path]:
        raw_path = Path(value)
        candidates: list[Path] = []
        if raw_path.is_absolute():
            return [raw_path]

        for base in self._candidate_data_roots():
            candidates.append(base / value)
            candidates.append(base / "attachments" / value)
            candidates.append(base / "temp" / value)

        candidates.append(Path.cwd() / value)
        return candidates

    def _candidate_data_roots(self) -> list[Path]:
        roots: list[Path] = []
        current = self.data_dir.resolve()
        roots.append(current)

        for parent in current.parents:
            roots.append(parent)
            if parent.name == "data":
                break

        deduped: list[Path] = []
        seen: set[str] = set()
        for path in roots:
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(path)
        return deduped

    def _resolve_attachment_token(self, token: str) -> str | None:
        cleaned = token.strip().strip("/")
        if not cleaned:
            return None

        db_path: Path | None = None
        for root in self._candidate_data_roots():
            candidate = root / "data_v4.db"
            if candidate.is_file():
                db_path = candidate
                break

        if db_path is None:
            return None

        try:
            with sqlite3.connect(db_path) as conn:
                row = conn.execute(
                    "SELECT path FROM attachments WHERE attachment_id = ? LIMIT 1",
                    (cleaned,),
                ).fetchone()
        except sqlite3.Error as exc:
            logger.warning("查询附件 token 失败: %s", exc)
            return None

        if not row or not row[0]:
            return None

        stored_path = str(row[0]).strip()
        if not stored_path:
            return None

        path = Path(stored_path)
        if path.is_file():
            return str(path)

        for root in self._candidate_data_roots():
            candidate = root / stored_path
            if candidate.is_file():
                return str(candidate)

        return stored_path

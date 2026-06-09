import sqlite3
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


@dataclass(slots=True)
class LocalImageRefResolver:
    data_dir: Path | None = None

    def __post_init__(self) -> None:
        if self.data_dir is not None:
            self.data_dir = self.data_dir.resolve()

    def resolve_local_image_ref(self, value: str) -> str | None:
        normalized = value.strip()
        if not normalized or self.data_dir is None:
            return None

        parsed = urlparse(normalized)
        if parsed.scheme in {"http", "https"}:
            if not parsed.path.startswith("/api/file/"):
                return None
            token = parsed.path.rsplit("/", 1)[-1].strip()
            return self.resolve_attachment_token(token) if token else None

        if normalized.startswith("/api/file/"):
            token = normalized.rsplit("/", 1)[-1].strip()
            return self.resolve_attachment_token(token) if token else None

        direct_path = Path(normalized)
        if direct_path.is_file():
            return str(direct_path)

        candidate = next((path for path in self.candidate_local_paths(normalized) if path.is_file()), None)
        if candidate is not None:
            return str(candidate)
        return self.resolve_attachment_token(normalized)

    def candidate_local_paths(self, value: str) -> list[Path]:
        raw_path = Path(value)
        if raw_path.is_absolute():
            return [raw_path]

        candidates = [
            candidate
            for base in self.candidate_data_roots()
            for candidate in (base / value, base / "attachments" / value, base / "temp" / value)
        ]
        candidates.append(Path.cwd() / value)
        return candidates

    def candidate_data_roots(self) -> list[Path]:
        if self.data_dir is None:
            return []

        roots = [self.data_dir, *self.data_dir.parents]
        data_root_index = next((index for index, path in enumerate(roots) if path.name == "data"), None)
        if data_root_index is not None:
            roots = roots[: data_root_index + 1]

        deduped: list[Path] = []
        seen: set[str] = set()
        for path in roots:
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(path)
        return deduped

    def resolve_attachment_token(self, token: str) -> str | None:
        cleaned = token.strip().strip("/")
        if not cleaned:
            return None

        db_path = next(
            (root / "data_v4.db" for root in self.candidate_data_roots() if (root / "data_v4.db").is_file()),
            None,
        )
        if db_path is None:
            return None

        try:
            with sqlite3.connect(db_path) as conn:
                row = conn.execute(
                    "SELECT path FROM attachments WHERE attachment_id = ? LIMIT 1",
                    (cleaned,),
                ).fetchone()
        except sqlite3.Error:
            return None

        if not row or not row[0]:
            return None

        stored_path = str(row[0]).strip()
        if not stored_path:
            return None

        path = Path(stored_path)
        if path.is_file():
            return str(path)

        candidate = next(
            (root / stored_path for root in self.candidate_data_roots() if (root / stored_path).is_file()),
            None,
        )
        return str(candidate) if candidate is not None else None

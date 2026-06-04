from datetime import datetime
from pathlib import Path

from astrbot.api import logger


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

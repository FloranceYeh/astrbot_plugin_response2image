import base64
import binascii
import json
import re
import shlex
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
import astrbot.api.message_components as Comp
from astrbot.api.star import Context, Star, StarTools


class Response2Image(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.data_dir: Path = StarTools.get_data_dir()

    @filter.command_group("r2i")
    def r2i():
        pass

    @r2i.command("help", alias={"帮助", "h", "?"})
    async def r2i_help(self, event: AstrMessageEvent):
        yield event.plain_result(
            "r2i 图像生成系统\n"
            "• /r2i img <提示词> [--ref 图片URL] [--model 模型]      自动判断文生图/改图\n"
            "• /r2i aiimg <提示词> [--model 模型]      文生图\n"
            "• /r2i aiedit <提示词> [--ref 图片URL] [--model 模型]      图生图\n"
            "• /r2i selfie <提示词> [--ref 图片URL] [--model 模型]      自拍\n"
            "• /r2i selfie_ref set       发送或引用图片后执行\n"
            "• /r2i selfie_ref list      查看已保存的参考图\n"
            "• /r2i selfie_ref clear     清空所有参考图\n"
        )

    @r2i.command("img", alias={"画图", "绘图"})
    async def img(self, event: AstrMessageEvent, prompt: str):
        """自动判断文生图或改图。"""
        async for result in self._generate(event, prompt, mode="auto"):
            yield result

    @r2i.command("aiimg", alias={"文生图", "生图"})
    async def aiimg(self, event: AstrMessageEvent, prompt: str):
        """文生图模式。"""
        async for result in self._generate(event, prompt, mode="text"):
            yield result

    @r2i.command("aiedit", alias={"改图", "图生图"})
    async def aiedit(self, event: AstrMessageEvent, prompt: str):
        """改图模式。"""
        async for result in self._generate(event, prompt, mode="edit"):
            yield result

    @r2i.command("selfie", alias={"自拍"})
    async def selfie(self, event: AstrMessageEvent, prompt: str):
        """自拍模式。"""
        async for result in self._generate(event, prompt, mode="selfie"):
            yield result

    @r2i.command("selfie_ref", alias={"自拍参考"})
    async def selfie_ref(self, event: AstrMessageEvent, action: str = ""):
        """自拍参考照管理：设置/查看/删除。"""
        action = (action or "").strip()
        if action in {"设置", "set"}:
            refs = self._extract_refs_from_event(event)
            if not refs:
                yield event.plain_result("请发送或引用图片后再设置自拍参考照。")
                return
            try:
                timeout = self._get_timeout()
            except ValueError as exc:
                yield event.plain_result(str(exc))
                return
            async with httpx.AsyncClient(timeout=timeout) as client:
                try:
                    count = await self._save_selfie_refs(refs, client)
                except ValueError as exc:
                    yield event.plain_result(str(exc))
                    return
            yield event.plain_result(f"已保存自拍参考照 {count} 张。")
            return
        if action in {"查看", "list"}:
            refs = self._list_selfie_ref_paths()
            if not refs:
                yield event.plain_result("暂无自拍参考照。")
                return
            yield event.plain_result(f"当前已保存 {len(refs)} 张自拍参考照。")
            return
        if action in {"删除", "清空", "clear"}:
            count = self._clear_selfie_refs()
            yield event.plain_result(f"已删除自拍参考照 {count} 张。")
            return
        yield event.plain_result("用法：自拍参考 设置/查看/删除")

    @filter.llm_tool()
    async def llm_r2i_aiimg(
        self,
        event: AstrMessageEvent,
        prompt: str = "",
    ) -> None:
        """
        文生图
        aiimg <提示词> [--model 模型]

        Args:
            prompt: 生成图片的提示词，支持使用 --model 指定模型。
        """
        async for result in self._generate(event, prompt, mode="text"):
            yield result

    @filter.llm_tool()
    async def llm_r2i_aiedit(
        self,
        event: AstrMessageEvent,
        prompt: str
    ) -> None:
        """
        改图
        aiedit <提示词> [--ref 图片URL] [--model 模型]

        Args:
            prompt: 生成图片的提示词，支持使用 --ref 指定参考图片 URL（逗号分隔多个）和 --model 指定模型。
        """
        async for result in self._generate(event, prompt, mode="edit"):
            yield result

    @filter.llm_tool(name="r2i_selfie")
    async def llm_r2i_selfie(
        self,
        event: AstrMessageEvent,
        prompt: str
    ) -> None:
        """
        自拍
        selfie <提示词> [--ref 图片URL] [--model 模型]

        Args:
            prompt: 生成图片的提示词，支持使用 --ref 指定参考图片 URL（逗号分隔多个）和 --model 指定模型。
        """
        async for result in self._generate(event, prompt, mode="selfie"):
            yield result


    async def _generate(self, event: AstrMessageEvent, raw_prompt: str, *, mode: str):
        try:
            prompt, ref_urls, model_override = self._parse_prompt(raw_prompt)
        except ValueError as exc:
            yield event.plain_result(str(exc))
            return

        base_url = str(self._config_get("base_url", "")).strip()
        api_key = str(self._config_get("api_key", "")).strip()
        model = str(self._config_get("model", "")).strip()
        if model_override:
            model = model_override

        if not base_url:
            yield event.plain_result("请在插件配置中设置 base_url。")
            return
        if not api_key:
            yield event.plain_result("请在插件配置中设置 api_key。")
            return
        if not model:
            yield event.plain_result("请在插件配置中设置 model，或在命令中使用 --model 指定。")
            return

        try:
            normalized_base = self._normalize_base_url(base_url)
        except ValueError as exc:
            yield event.plain_result(str(exc))
            return

        try:
            timeout = self._get_timeout()
        except ValueError as exc:
            yield event.plain_result(str(exc))
            return

        event_refs = self._extract_refs_from_event(event) if mode in {"auto", "edit", "selfie"} else []
        config_refs = self._get_selfie_refs_from_config() if mode == "selfie" else []
        if mode == "selfie" and not ref_urls and not event_refs:
            ref_urls = config_refs or self._list_selfie_ref_paths()

        if mode == "text" and (ref_urls or event_refs):
            yield event.plain_result("文生图模式不使用参考图，请改用改图或自拍。")
            return
        if mode == "edit" and not (ref_urls or event_refs):
            yield event.plain_result("改图需要参考图片，请发送/引用图片或使用 --ref。")
            return
        if mode == "selfie" and not (ref_urls or event_refs):
            yield event.plain_result("未设置自拍参考照，请先使用“自拍参考 设置”。")
            return

        merged_refs = self._merge_refs(ref_urls, event_refs)
        resolved_mode = self._resolve_mode(mode, merged_refs)
        url = normalized_base + "/v1/responses"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
        }
        image_error: str | None = None

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                try:
                    ref_images = await self._normalize_ref_images(merged_refs, client)
                except ValueError as exc:
                    yield event.plain_result(str(exc))
                    return
                if resolved_mode in {"edit", "selfie"} and not ref_images:
                    yield event.plain_result("参考图片不可用，请检查图片是否可访问。")
                    return

                payload = self._build_payload(prompt, model, ref_images, resolved_mode)
                async with client.stream("POST", url, headers=headers, json=payload) as response:
                    if response.status_code >= 400:
                        body = await response.aread()
                        detail = body.decode("utf-8", "ignore")[:500]
                        yield event.plain_result(f"请求失败：HTTP {response.status_code} {detail}")
                        return

                    async for line in response.aiter_lines():
                        line = line.strip()
                        if not line:
                            continue
                        if line.startswith("event: "):
                            continue
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if not data_str or data_str == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                        except json.JSONDecodeError:
                            logger.warning("无法解析数据块: %s", data_str[:200])
                            continue

                        image_ref = self._extract_image_ref(data)
                        if not image_ref:
                            continue
                        try:
                            image_bytes = await self._read_image_bytes(image_ref, client)
                        except ValueError as exc:
                            image_error = str(exc)
                            logger.warning("图片解析失败: %s", exc)
                            continue

                        file_path = self._write_image(image_bytes)
                        size_str = self._format_size(len(image_bytes))
                        label = self._mode_label(resolved_mode)
                        chain = [
                            Comp.Plain(f"{label}完成（{size_str}）"),
                            Comp.Image.fromFileSystem(str(file_path)),
                        ]
                        yield event.chain_result(chain)
                        return

            if image_error:
                yield event.plain_result(image_error)
                return
            yield event.plain_result("未收到图片结果，请检查模型是否支持 image_generation。")
        except httpx.HTTPError as exc:
            logger.error(f"请求失败: {exc}")
            yield event.plain_result(f"请求失败：{exc}")

    def _config_get(self, key: str, default: Any) -> Any:
        if self.config is None:
            return default
        if isinstance(self.config, dict):
            return self.config.get(key, default)
        if hasattr(self.config, "get"):
            return self.config.get(key, default)
        return getattr(self.config, key, default)

    def _normalize_base_url(self, base_url: str) -> str:
        base = base_url.strip()
        if not base:
            raise ValueError("Base URL 不能为空。")
        if not base.lower().startswith(("http://", "https://")):
            raise ValueError("Base URL 必须以 http:// 或 https:// 开头。")
        base = base.rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        return base

    def _parse_prompt(self, raw: str) -> tuple[str, list[str], str | None]:
        tokens = shlex.split(raw)
        if not tokens:
            raise ValueError("请提供提示词。")

        prompt_parts: list[str] = []
        ref_urls: list[str] = []
        model_override: str | None = None

        i = 0
        while i < len(tokens):
            token = tokens[i]
            if token == "--ref":
                if i + 1 >= len(tokens):
                    raise ValueError("缺少 --ref 参数。")
                ref_urls.extend([u for u in tokens[i + 1].split(",") if u])
                i += 2
                continue
            if token == "--model":
                if i + 1 >= len(tokens):
                    raise ValueError("缺少 --model 参数。")
                model_override = tokens[i + 1]
                i += 2
                continue
            prompt_parts.append(token)
            i += 1

        prompt = " ".join(prompt_parts).strip()
        if not prompt:
            raise ValueError("请提供提示词。")
        return prompt, ref_urls, model_override

    def _get_timeout(self) -> httpx.Timeout:
        try:
            timeout_seconds = int(self._config_get("timeout_seconds", 120))
        except (TypeError, ValueError) as exc:
            raise ValueError("插件配置 timeout_seconds 无效。") from exc
        if timeout_seconds <= 0:
            raise ValueError("插件配置 timeout_seconds 必须大于 0。")
        return httpx.Timeout(timeout_seconds)

    def _resolve_mode(self, mode: str, refs: list[str]) -> str:
        if mode == "auto":
            return "edit" if refs else "text"
        return mode

    def _mode_label(self, mode: str) -> str:
        if mode == "edit":
            return "改图"
        if mode == "selfie":
            return "自拍"
        return "文生图"

    def _build_edit_prompt(self, prompt: str, mode: str) -> str:
        if mode == "selfie":
            return (
                "请根据提供的人像参考生成自拍风格图片，保持人物一致。要求："
                + prompt
            )
        return (
            "请根据以下要求，对我提供的参考图片进行编辑修改，直接生成修改后的新图片。要求："
            + prompt
        )

    async def _normalize_ref_images(self, refs: list[str], client: httpx.AsyncClient) -> list[str]:
        normalized: list[str] = []
        for ref in refs:
            ref = ref.strip()
            if not ref:
                continue
            if self._looks_like_data_url(ref):
                normalized.append(ref)
                continue
            if ref.startswith(("http://", "https://")):
                data_url = await self._fetch_image_as_data_url(ref, client)
                normalized.append(data_url)
                continue
            path = Path(ref)
            if not path.is_file():
                raise ValueError(f"参考图片不存在: {ref}")
            mime = self._guess_mime_from_path(path)
            data = path.read_bytes()
            normalized.append(self._build_data_url(mime, data))
        return normalized

    def _build_payload(self, prompt: str, model: str, ref_images: list[str], mode: str) -> dict:
        if mode in {"edit", "selfie"}:
            edit_prompt = self._build_edit_prompt(prompt, mode)
            content = [{"type": "input_image", "image_url": url} for url in ref_images]
            content.append({"type": "input_text", "text": edit_prompt})
            return {
                "model": model,
                "input": [{"role": "user", "content": content}],
                "tools": [{"type": "image_generation", "output_format": "png"}],
                "stream": True,
            }

        return {
            "model": model,
            "input": [
                {
                    "role": "system",
                    "content": (
                        "你是一个图片生成助手。用户要求你生成图片时，你必须调用 image_generation 工具来生成图片，"
                        "不要用文字描述图片内容。直接生成图片，不要多说任何话。"
                    ),
                },
                {"role": "user", "content": f"请生成以下描述的图片：{prompt}"},
            ],
            "tools": [{"type": "image_generation", "output_format": "png"}],
            "stream": True,
        }

    def _extract_image_ref(self, data: Any) -> tuple[str, str] | None:
        fallback_url: str | None = None

        def walk(obj: Any) -> tuple[str, str] | None:
            nonlocal fallback_url
            if isinstance(obj, dict):
                for key, value in obj.items():
                    if isinstance(value, str):
                        if key in {"result", "b64_json", "image"} and self._looks_like_base64(value):
                            return ("base64", value)
                        if self._looks_like_data_url(value):
                            return ("data_url", value)
                        if key in {"url", "image_url", "output_url"} and value.startswith(("http://", "https://")):
                            return ("url", value)
                        if fallback_url is None and self._looks_like_image_url(value):
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

    def _looks_like_base64(self, value: str) -> bool:
        if value.startswith(("iVBOR", "/9j/")):
            return True
        return len(value) > 1000

    def _looks_like_data_url(self, value: str) -> bool:
        return value.startswith("data:image/")

    def _looks_like_image_url(self, value: str) -> bool:
        if not value.startswith(("http://", "https://")):
            return False
        return self._has_image_extension(value)

    def _has_image_extension(self, value: str) -> bool:
        trimmed = value.split("?", 1)[0].split("#", 1)[0]
        suffix = Path(trimmed).suffix.lower()
        return suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}

    def _guess_mime_from_path(self, path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix in {".jpg", ".jpeg"}:
            return "image/jpeg"
        if suffix == ".webp":
            return "image/webp"
        if suffix == ".gif":
            return "image/gif"
        return "image/png"

    def _guess_mime_from_url(self, url: str) -> str | None:
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

    def _build_data_url(self, mime: str, data: bytes) -> str:
        b64 = base64.b64encode(data).decode("ascii")
        return f"data:{mime};base64,{b64}"

    def _parse_data_url(self, data_url: str) -> tuple[str, bytes]:
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

    async def _fetch_image_as_data_url(self, url: str, client: httpx.AsyncClient) -> str:
        resp = await client.get(url, follow_redirects=True)
        if resp.status_code >= 400:
            raise ValueError(f"参考图片请求失败：HTTP {resp.status_code}")
        content_type = resp.headers.get("Content-Type", "").split(";", 1)[0].strip()
        if not content_type.startswith("image/"):
            guessed = self._guess_mime_from_url(url)
            if not guessed:
                raise ValueError("参考图片 Content-Type 不是图片。")
            content_type = guessed
        return self._build_data_url(content_type, resp.content)

    def _decode_data_url(self, data_url: str) -> bytes:
        _, data = self._parse_data_url(data_url)
        return data

    def _mime_to_ext(self, mime: str) -> str:
        if mime == "image/jpeg":
            return ".jpg"
        if mime == "image/webp":
            return ".webp"
        if mime == "image/gif":
            return ".gif"
        return ".png"

    def _selfie_ref_dir(self) -> Path:
        return self.data_dir / "selfie_refs"

    def _get_selfie_refs_from_config(self) -> list[str]:
        raw = self._config_get("selfie_reference_images", [])
        refs: list[str] = []
        for item in self._extract_config_image_refs(raw):
            value = item.strip()
            if not value:
                continue
            resolved = self._resolve_config_image_ref(value)
            if resolved:
                refs.append(resolved)
        return self._merge_refs(refs, [])

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
        if self._looks_like_data_url(value) or value.startswith(("http://", "https://")):
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

        import sqlite3

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

    def _list_selfie_ref_paths(self) -> list[str]:
        ref_dir = self._selfie_ref_dir()
        if not ref_dir.exists():
            return []
        paths = []
        for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp", "*.gif"):
            paths.extend(ref_dir.glob(ext))
        return [str(p) for p in sorted(paths)]

    def _clear_selfie_refs(self) -> int:
        ref_dir = self._selfie_ref_dir()
        if not ref_dir.exists():
            return 0
        count = 0
        for path in ref_dir.iterdir():
            if path.is_file():
                path.unlink()
                count += 1
        return count

    async def _save_selfie_refs(self, refs: list[str], client: httpx.AsyncClient) -> int:
        ref_images = await self._normalize_ref_images(refs, client)
        if not ref_images:
            raise ValueError("未找到可用的参考图片。")
        ref_dir = self._selfie_ref_dir()
        ref_dir.mkdir(parents=True, exist_ok=True)
        count = 0
        for idx, data_url in enumerate(ref_images):
            mime, data = self._parse_data_url(data_url)
            ext = self._mime_to_ext(mime)
            name = datetime.now().strftime("selfie_%Y%m%d_%H%M%S")
            path = ref_dir / f"{name}_{idx}{ext}"
            path.write_bytes(data)
            count += 1
        return count

    async def _read_image_bytes(
        self, image_ref: tuple[str, str], client: httpx.AsyncClient
    ) -> bytes:
        kind, value = image_ref
        if kind == "base64":
            try:
                return base64.b64decode(value)
            except binascii.Error as exc:
                raise ValueError("返回的图片 base64 无法解码。") from exc
        if kind == "data_url":
            return self._decode_data_url(value)
        if kind == "url":
            resp = await client.get(value, follow_redirects=True)
            if resp.status_code >= 400:
                raise ValueError(f"返回的图片 URL 请求失败：HTTP {resp.status_code}")
            return resp.content
        raise ValueError("未识别的图片格式。")

    def _format_size(self, size_bytes: int) -> str:
        if size_bytes >= 1024 * 1024:
            return f"{size_bytes / 1024 / 1024:.1f} MB"
        return f"{size_bytes / 1024:.1f} KB"

    def _merge_refs(self, first: list[str], second: list[str]) -> list[str]:
        seen: set[str] = set()
        merged: list[str] = []
        for item in first + second:
            if item in seen:
                continue
            seen.add(item)
            merged.append(item)
        return merged

    def _extract_refs_from_event(self, event: AstrMessageEvent) -> list[str]:
        refs: list[str] = []
        self._collect_image_refs(event.message_obj, refs)
        refs.extend(self._extract_image_refs_from_text(event.message_str))
        return refs

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
                    if self._looks_like_image_ref(value):
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
                if self._looks_like_image_ref(value):
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
        return [m for m in matches if self._looks_like_image_ref(m)]

    def _looks_like_image_ref(self, value: str) -> bool:
        if self._looks_like_data_url(value):
            return True
        if value.startswith(("http://", "https://")):
            return self._has_image_extension(value)
        path = Path(value)
        if not path.is_file():
            return False
        suffix = path.suffix.lower()
        return suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}

    def _write_image(self, image_bytes: bytes) -> Path:
        out_dir = self.data_dir / "generated"
        out_dir.mkdir(parents=True, exist_ok=True)
        name = datetime.now().strftime("resp2img_%Y%m%d_%H%M%S.png")
        path = out_dir / name
        path.write_bytes(image_bytes)
        return path

    async def terminate(self):
        """插件被卸载时触发。"""

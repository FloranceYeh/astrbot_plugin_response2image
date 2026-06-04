import re
import shlex
from dataclasses import dataclass
from typing import Any

UPSTREAM_IMAGE_RETRY_INSTRUCTION = (
    "重要指示：如果 image_generation 未产出图片，请在不改变核心意图的前提下改写提示词并再次调用，"
    "直到成功生成图片，否则继续重试，不得中途停止。"
)

UPSTREAM_IMAGE_SYSTEM_PROMPT = "\n".join(
    [
        "你是专业图片生成代理。目标是调用 image_generation 工具直接产出图片，不用文字描述代替图片。",
        "调用工具前，将需求整理为简洁、可执行的视觉提示词；保留硬约束，适度补足构图、主体、动作、场景、光线、色彩、材质、镜头、背景、风格和细节。如果需求中有明确的风格要求，需要遵循。",
        "不要擅自增加水印、Logo、签名、边框、乱码或无关文字。",
        UPSTREAM_IMAGE_RETRY_INSTRUCTION,
        "最终只产出图片，不解释改写过程，不输出 Markdown 或无关文本。",
    ]
)

DEFAULT_REFERENCE_PROMPT_EDIT = (
    "只修改用户明确要求修改的内容；未提及的主体身份、数量、构图、比例、姿态、背景关系和关键细节尽量保持不变。"
)
DEFAULT_REFERENCE_PROMPT_SELFIE = (
    "参考图片仅作为人物与外观依据；优先保持人物身份、脸部特征和整体一致性，根据用户要求生成自然的自拍照片效果。"
)
DEFAULT_REFERENCE_PROMPT_WHITE = (
    "该参考图为纯白占位图，仅用于稳定生成流程与强化对文本指令的遵循，不提供任何可继承的主体、构图、风格或细节信息；请忽略其视觉内容，不要把白底、空白画面、留白构图或极简白色背景当作目标效果，仍以用户原始需求为唯一主要依据完成正常文生图"
)
PLUGIN_RESPONSE_PREFIX = "[r2i]"
WHITE_REFERENCE_IMAGE_NAME = "space.jpg"


@dataclass
class GenerationResult:
    response: Any
    llm_text: str
    has_image: bool = False
    image_path: str | None = None
    image_data: dict[str, Any] | None = None


@dataclass
class GenerationInputs:
    prompt: str
    ref_urls: list[str]
    image_size: str | None


def resolve_command_prompt(
    message: str | None,
    command_name: str,
    fallback_prompt: str = "",
) -> str:
    message = (message or "").strip()
    if not message:
        return fallback_prompt.strip()

    parts = message.split(maxsplit=2)
    if len(parts) >= 2 and parts[0].lstrip("/").lower() == "r2i" and parts[1].lower() == command_name:
        return parts[2].strip() if len(parts) == 3 else ""
    if parts and parts[0].lstrip("/").lower() == command_name:
        return parts[1].strip() if len(parts) >= 2 else ""
    return fallback_prompt.strip()


def compose_command_fallback_prompt(
    prompt: str = "",
    *,
    ref: str = "",
    size: str = "",
) -> str:
    parts: list[str] = []
    if prompt.strip():
        parts.append(prompt.strip())
    if ref.strip():
        parts.extend(["--ref", ref.strip()])
    if size.strip():
        parts.extend(["--size", size.strip()])
    return " ".join(parts)


def resolve_generation_inputs(
    prompt: str,
    ref: Any = None,
    size: str = "",
    *,
    default_image_size: str | None = None,
) -> GenerationInputs:
    normalized_prompt = (prompt or "").strip()
    explicit_refs = _parse_ref_argument(ref)
    explicit_size = normalize_image_size(size)
    legacy_refs: list[str] = []
    legacy_size: str | None = None

    if "--ref" in normalized_prompt or "--size" in normalized_prompt:
        normalized_prompt, legacy_refs, legacy_size = _parse_legacy_prompt_and_refs(normalized_prompt)

    if not normalized_prompt:
        raise ValueError("请提供提示词。")
    return GenerationInputs(
        prompt=normalized_prompt,
        ref_urls=merge_refs(explicit_refs, legacy_refs),
        image_size=explicit_size or legacy_size or default_image_size,
    )


def normalize_image_size(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    match = re.fullmatch(r"(\d{2,5})[xX](\d{2,5})", text)
    if not match:
        raise ValueError("图片尺寸格式无效，请使用类似 1024x1024 的宽x高格式。")
    return f"{match.group(1)}x{match.group(2)}"


def resolve_mode(mode: str, refs: list[str]) -> str:
    if mode == "auto":
        return "edit" if refs else "text"
    return mode


def mode_label(mode: str) -> str:
    if mode == "edit":
        return "改图"
    if mode == "selfie":
        return "自拍"
    return "文生图"


def get_reference_prompt_lines(mode: str, override_text: str | None = None) -> list[str]:
    if mode == "selfie":
        text = DEFAULT_REFERENCE_PROMPT_SELFIE if override_text is None else override_text
    elif mode == "edit":
        text = DEFAULT_REFERENCE_PROMPT_EDIT if override_text is None else override_text
    else:
        text = DEFAULT_REFERENCE_PROMPT_WHITE

    text = text.strip()
    if not text:
        return []
    return [line.strip() for line in text.splitlines() if line.strip()]


def build_upstream_image_prompt(prompt: str, reference_lines: list[str]) -> str:
    task_lines: list[str] = [
        "请直接调用 image_generation 工具完成图片任务。",
        "先整理为一条清晰、可执行的视觉提示词，再调用工具。",
        "保留用户要求的主体、动作、场景、风格、文字、比例、禁止事项等硬约束。",
        "可适度补充构图、镜头、光线、色彩、材质、空间关系和清晰度；不要引入与主题冲突的元素。",
        "如用户要求包含文字，保持文字内容一致、简洁、清晰可读。",
        UPSTREAM_IMAGE_RETRY_INSTRUCTION,
        "只输出 image_generation 工具生成的图片结果；不要输出解释、分析、Markdown 或纯文本替代答案。",
    ]

    lines = list(task_lines)
    if reference_lines:
        lines.extend(["", "参考图片处理："])
        lines.extend(f"- {line}" for line in reference_lines)
    lines.extend(["", "用户原始需求如下：", prompt])
    return "\n".join(lines)


def build_payload(
    prompt: str,
    model: str,
    ref_images: list[str],
    *,
    image_size: str | None = None,
    reference_lines: list[str] | None = None,
) -> dict[str, Any]:
    user_prompt = build_upstream_image_prompt(prompt, reference_lines or [])
    tool: dict[str, Any] = {"type": "image_generation", "output_format": "png"}
    if image_size:
        tool["size"] = image_size

    if ref_images:
        content = [{"type": "input_image", "image_url": url} for url in ref_images]
        content.append({"type": "input_text", "text": user_prompt})
        return {
            "model": model,
            "input": [
                {"role": "system", "content": UPSTREAM_IMAGE_SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            "tools": [tool],
            "tool_choice": {"type": "image_generation"},
            "stream": True,
        }

    return {
        "model": model,
        "input": [
            {"role": "system", "content": UPSTREAM_IMAGE_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "tools": [tool],
        "tool_choice": {"type": "image_generation"},
        "stream": True,
    }


def merge_refs(first: list[str], second: list[str]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for item in first + second:
        if item in seen:
            continue
        seen.add(item)
        merged.append(item)
    return merged


def _parse_ref_argument(ref: Any) -> list[str]:
    if ref is None:
        return []
    if isinstance(ref, str):
        return _split_ref_values(ref)
    if isinstance(ref, (list, tuple, set)):
        refs: list[str] = []
        for item in ref:
            refs.extend(_parse_ref_argument(item))
        return refs
    return _split_ref_values(str(ref))


def _parse_legacy_prompt_and_refs(raw: str) -> tuple[str, list[str], str | None]:
    tokens = shlex.split(raw)
    if not tokens:
        raise ValueError("请提供提示词。")

    prompt_parts: list[str] = []
    ref_urls: list[str] = []
    image_size: str | None = None

    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--ref":
            if i + 1 >= len(tokens):
                raise ValueError("缺少 --ref 参数。")
            ref_urls.extend(_split_ref_values(tokens[i + 1]))
            i += 2
            continue
        if token == "--size":
            if i + 1 >= len(tokens):
                raise ValueError("缺少 --size 参数。")
            image_size = normalize_image_size(tokens[i + 1])
            i += 2
            continue
        prompt_parts.append(token)
        i += 1

    prompt = " ".join(prompt_parts).strip()
    if not prompt:
        raise ValueError("请提供提示词。")
    return prompt, ref_urls, image_size


def _split_ref_values(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]

def format_preset_command_parse_failed(detail: str) -> str:
    return f"预设命令解析失败：{detail}"


def preset_usage() -> str:
    return (
        "用法：\n"
        "/r2i preset list\n"
        "/r2i preset show <标题>\n"
        "/r2i preset add <标题> <内容> [--ref ...] [--size 1024x1024] [--auto-size]\n"
        "/r2i preset del <标题>\n"
        "调用示例：/r2i img --preset 日常自拍"
    )


def preset_list_empty() -> str:
    return "暂无预设提示词。"


def preset_list_header(count: int) -> str:
    return f"当前共有 {count} 组预设："


def preset_list_item(index: int, title: str, meta_text: str, preview: str) -> str:
    return f"{index}. 《{title}》{meta_text} {preview}".rstrip()


def preset_list_call_hint() -> str:
    return '调用：/r2i img --preset "标题"'


def preset_not_found(title: str) -> str:
    return f"未找到预设《{title}》。"


def preset_detail_title_required() -> str:
    return "请提供要查看的预设标题。"


def preset_detail_lines(title: str, image_size: str | None, ref_count: int, content: str) -> list[str]:
    return [
        f"标题：{title}",
        f"尺寸：{image_size or '默认'}",
        f"参考图：{ref_count} 张",
        "内容：",
        content,
    ]


def preset_detail_ref_header() -> str:
    return "ref："


def preset_saved(title: str, ref_count: int, image_size: str | None, auto_size_note: str) -> str:
    ref_summary = f"{ref_count} 张参考图" if ref_count else "无参考图"
    size_summary = image_size or "默认尺寸"
    return f"已保存预设《{title}》：{ref_summary}，{size_summary}。{auto_size_note}".strip()


def preset_auto_size_requires_ref() -> str:
    return "启用 --auto-size 时需要至少一张参考图。"


def preset_auto_size_note(
    original_width: int,
    original_height: int,
    normalized_width: int,
    normalized_height: int,
) -> str:
    return (
        f" 已按首张参考图尺寸 {original_width}x{original_height} "
        f"自动规范为 {normalized_width}x{normalized_height}。"
    )


def preset_delete_title_required() -> str:
    return "请提供要删除的预设标题。"


def preset_deleted(title: str) -> str:
    return f"已删除预设《{title}》。"


def preset_title_empty() -> str:
    return "预设标题不能为空。"


def preset_content_empty() -> str:
    return "预设内容不能为空。"


def missing_option_argument(option_name: str) -> str:
    return f"缺少 {option_name} 参数。"


def selfie_ref_usage() -> str:
    return "用法：自拍参考 设置/查看/删除"


def selfie_ref_empty() -> str:
    return "暂无自拍参考照。"


def selfie_ref_summary(total_count: int, config_count: int, saved_count: int) -> str:
    return f"当前共有 {total_count} 张自拍参考照（WebUI 配置 {config_count} 张，命令保存 {saved_count} 张）。"


def selfie_ref_cleared(count: int, config_count: int) -> str:
    if config_count:
        return f"已删除命令保存的自拍参考照 {count} 张。WebUI 配置中仍有 {config_count} 张参考图。"
    return f"已删除命令保存的自拍参考照 {count} 张。"


def selfie_ref_set_requires_image() -> str:
    return "请发送或引用图片后再设置自拍参考照。"


def selfie_ref_saved(count: int) -> str:
    return f"已保存自拍参考照 {count} 张。"


def chat_image_send_failed() -> str:
    return "图片已生成，但发送到当前对话失败，请改用上面的图片路径。"


def prompt_required() -> str:
    return "请提供提示词。"


def image_size_invalid() -> str:
    return "图片尺寸格式无效，请使用类似 1024x1024 的 宽x高 格式。"


def config_value_invalid(key: str) -> str:
    return f"插件配置 {key} 无效。"


def config_value_must_be_positive(key: str) -> str:
    return f"插件配置 {key} 必须大于 0。"


def config_retry_count_invalid() -> str:
    return "插件配置 generation_retry_count 必须大于或等于 0。"


def config_keep_count_invalid() -> str:
    return "插件配置 generated_image_keep_count 必须为 -1 或大于 0。"


def base_url_required() -> str:
    return "Base URL 不能为空。"


def base_url_scheme_invalid() -> str:
    return "Base URL 必须以 http:// 或 https:// 开头。"


def plugin_config_required(key: str) -> str:
    return f"请在插件配置中设置 {key}。"


def text_mode_rejects_refs() -> str:
    return "文生图模式不使用参考图，请改用改图或自拍。"


def edit_mode_requires_ref() -> str:
    return "改图需要参考图片，请发送/引用图片，或通过参考图参数传入。"


def selfie_mode_requires_ref() -> str:
    return "未设置自拍参考照，请先使用“自拍参考 设置”。"


def ref_image_unavailable() -> str:
    return "参考图片不可用，请检查图片是否可访问。"


def generation_request_failed(detail: str) -> str:
    return f"请求失败：{detail}"


def no_generated_image_result() -> str:
    return "未收到图片结果，请检查模型是否支持 image_generation。"

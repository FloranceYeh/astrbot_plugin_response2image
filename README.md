# Response2Image

基于 Responses API 的 AstrBot 图像生成插件，支持文生图、改图和自拍三种模式。

插件通过 `/v1/responses` 的 `image_generation` tool 发起请求，实现生图不是按张计费的 images endpoint，而是按请求计费的 responses endpoint。

需要模型支持 `image_generation` 工具。

## 功能

- 支持文生图、改图、自拍三种生成模式
- 支持命令调用和 `llm_tool` 调用
- 支持 `--ref` / `ref` 传入参考图
- 支持按标题保存多组预设提示词，并在调用时复用
- 支持自动从消息内容、引用内容中提取图片作为参考图
- 支持在文生图模式下垫一张白图，以缓解上游模型稀释
- 支持限制本地生成图片的保留张数
- 支持通过 WebUI 配置自拍参考图，也支持命令保存自拍参考图

## 安装

可通过以下任一方式安装：

1. 在 AstrBot WebUI 中使用仓库地址安装：

`https://github.com/FloranceYeh/astrbot_plugin_response2image`

2. 或将本仓库放入：

`AstrBot/data/plugins/astrbot_plugin_response2image`

然后在 WebUI 插件管理中启用并完成配置。

## 配置

在 WebUI 中配置以下字段：

- `base_url`：接口基础地址，例如 `https://api.openai.com`
- `api_key`：图像生成服务的 API Key
- `model`：模型 ID，必须支持 `image_generation`
- `timeout_seconds`：请求超时时间，单位秒
- `image_size`：默认图片尺寸，例如 `1024x1024`
- `generated_image_keep_count`：本地生成图片保留张数，`-1` 表示全部保留
- `reference_prompt_edit`：改图模式参考图补充指令，支持多行
- `reference_prompt_selfie`：自拍模式参考图补充指令，支持多行
- `text_mode_use_white_reference_image`：是否在文生图模式附带内置白图参考
- `send_generated_image_in_chat`：启用后，LLM tool 生成成功时会把图片直接发回当前聊天
- `selfie_reference_images`：在 WebUI 上传的自拍参考图

## 使用

### 命令

命令组为 `r2i`。

可用子命令：

- `r2i help`
- `r2i img <提示词> [--preset 标题] [--ref 路径] [--size 宽x高]`
- `r2i aiimg <提示词> [--preset 标题] [--size 宽x高]`
- `r2i aiedit <提示词> [--preset 标题] [--ref 路径] [--size 宽x高]`
- `r2i selfie <提示词> [--preset 标题] [--ref 路径] [--size 宽x高]`
- `r2i preset list`
- `r2i preset show <标题>`
- `r2i preset add <标题> <内容> [--ref 路径] [--size 宽x高] [--auto-size]`
- `r2i preset del <标题>`
- `r2i selfie_ref set`
- `r2i selfie_ref list`
- `r2i selfie_ref clear`

示例：

```text
r2i img 一只在雨中奔跑的柴犬，电影感，动态抓拍
r2i preset add 日常自拍 窗边自然光，微笑，真实肤色 --size 1024x1536
r2i preset add 半身人像 保留构图和主体比例 --auto-size
r2i img --preset 日常自拍
r2i aiedit 保留人物姿势，改成海边日落 --preset 日常自拍 --ref https://example.com/input.png
r2i aiimg 夜色霓虹城市，赛博朋克街景
r2i aiedit 把参考图改成水彩风格 --ref https://example.com/input.png --size 1024x1024
r2i aiedit 把参考图加上蓝色天空 --ref C:\Images\input.jpg
r2i selfie_ref set
r2i selfie 日常自拍，微笑，窗边自然光
```

### LLM Tool

- `r2i_img <提示词> [参考图]`，自动模式，有参考图时优先走改图
- `r2i_aiimg <提示词>`，文生图
- `r2i_aiedit <提示词> <参考图>`，改图
- `r2i_selfie <提示词> [参考图]`，自拍
- `r2i_preset_list`，查看当前全部预设，便于模型先挑选可用预设
- `r2i_preset_show <标题>`，查看某组预设的完整内容、尺寸和参考图信息

## 行为说明

- 命令中的 `--ref` 支持多个参考图，使用英文逗号分隔，支持 `URL / data:image / 本地路径` 三种格式
- 命令中的 `--size` 格式为 `宽x高`，例如 `1024x1024`，如果不指定则使用配置中的默认值；若配置中也没有默认值，则由上游模型自动决定尺寸
- 命令中的 `--preset` 用于加载已保存预设；预设里的内容会作为基础提示词，当前命令里追加的提示词会拼接在后面，当前命令里的 `ref` / `size` 会优先覆盖预设
- `r2i preset add` 支持把 `ref` 和 `size` 一起保存到预设里，后续调用时可直接复用
- `r2i preset add --auto-size` 会读取第一张参考图的原始尺寸，并将宽高分别规范到最接近的 16 的倍数后保存；如果同时提供 `--size`，则仍以显式 `--size` 为准
- LLM tool 更推荐通过独立 `ref` 参数传入参考图
- 若消息中本身包含图片或引用图片，插件会自动尝试提取并作为参考图
- 自拍模式优先使用命令或消息中的参考图；如果没有，则回退到 WebUI 上传和 `selfie_ref set` 保存的参考图
- 生成图片默认保存到 `data/plugin_data/astrbot_plugin_response2image/generated`
- 文件名格式为 `resp2img_%Y%m%d_%H%M%S.png`
- 当 `send_generated_image_in_chat` 启用时，LLM tool 除返回状态文本外，也会把图片直接发送到当前聊天

## 鸣谢

- [CodeBoy](https://github.com/CodeBoy2006)
- 灵感来源：[CodeBoy2006/responses-images-proxy](https://github.com/CodeBoy2006/responses-images-proxy)

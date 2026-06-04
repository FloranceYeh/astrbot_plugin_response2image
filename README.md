# Response2Image

基于 Responses API 的 AstrBot 图像生成插件，支持文生图、改图和自拍三种模式。

插件通过 `/v1/responses` 的 `image_generation` tool 发起请求，实现生图不是按张计费的 images endpoint，而是按请求计费的 responses endpoint。

需要模型支持 `image_generation` 工具。

## 功能

- 支持文生图、改图、自拍三种生成模式
- 支持命令调用和 `llm_tool` 调用
- 支持 `--ref` / `ref` 传入参考图
- 支持自动从消息内容、引用内容中提取图片作为参考图
- 支持在文生图模式下垫一张白图，以缓解上游模型稀释
- 支持限制本地生成图片的保留张数
- 支持通过 WebUI 配置自拍参考图，也支持命令保存自拍参考图

## 项目结构

当前插件已按职责拆分为以下结构：

```text
astrbot_plugin_response2image/
├── main.py
├── core/
│   ├── __init__.py
│   ├── config.py
│   ├── generation.py
│   ├── media.py
│   ├── selfie_refs.py
│   └── storage.py
├── _conf_schema.json
├── metadata.yaml
├── requirements.txt
├── README.md
└── space.jpg
```

职责说明：

- `main.py`：AstrBot 入口、命令注册、LLM tool 注册、生成主流程编排
- `core/config.py`：插件配置读取与校验
- `core/generation.py`：提示词解析、模式判断、payload 构建
- `core/media.py`：图片引用提取、data URL 编解码、远程图片读取
- `core/selfie_refs.py`：自拍参考图解析、保存、清理与附件 token 解析
- `core/storage.py`：生成图片写入与历史图片清理

## 安装

可通过以下任一方式安装：

1. 在 AstrBot WebUI 中使用仓库地址安装：

```text
https://github.com/FloranceYeh/astrbot_plugin_response2image
```

2. 或将本仓库放入：

```text
AstrBot/data/plugins/astrbot_plugin_response2image
```

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
- `r2i img <提示词> [--ref] [--size]`
- `r2i aiimg <提示词> [--size]`
- `r2i aiedit <提示词> [--ref] [--size]`
- `r2i selfie <提示词> [--ref] [--size]`
- `r2i selfie_ref set`
- `r2i selfie_ref list`
- `r2i selfie_ref clear`

示例：

```text
r2i img 一只在雨中奔跑的柴犬，电影感，动态抓拍
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

## 行为说明

- 命令中的 `--ref` 支持多个参考图，使用英文逗号分隔，支持 `URL / data:image / 本地路径` 三种格式
- 命令中的 `--size` 格式为 `宽x高`，例如 `1024x1024`，如果不指定则使用配置中的默认值,若配置中也没有默认值，则由上游模型自动决定尺寸
- LLM tool 更推荐通过独立 `ref` 参数传入参考图
- 若消息中本身包含图片或引用图片，插件会自动尝试提取并作为参考图
- 自拍模式优先使用命令或消息中的参考图；如果没有，则回退到 WebUI 上传和 `selfie_ref set` 保存的参考图
- 生成图片默认保存到 `data/plugin_data/astrbot_plugin_response2image/generated`
- 文件名格式为 `resp2img_%Y%m%d_%H%M%S.png`
- 当 `send_generated_image_in_chat` 启用时，LLM tool 除返回状态文本外，也会把图片直接发送到当前聊天

## 鸣谢

- [CodeBoy](https://github.com/CodeBoy2006)
- 灵感来源：[CodeBoy2006/responses-images-proxy](https://github.com/CodeBoy2006/responses-images-proxy)

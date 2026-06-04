# Response2Image

基于 Responses API 的 AstrBot 图像生成插件，支持文生图、改图、自拍三种模式，自动保存生成图片并返回

通过 responses 的 image_generation tool 处理生图请求，实现生图不是按张计费的 images endpoint，而是按请求计费的 responses endpoint

**需要模型支持 image_generation tool**

## 功能

- 通过 `/v1/responses` 流式获取图片结果
- 文生图 / 改图 / 自拍三种模式
- 支持参考图传入（LLM tool 推荐独立 `ref` 参数；命令兼容 `--ref`，支持 URL / data:image / 本地文件路径）
- 自动从消息/引用里提取图片作为参考图
- 可在 WebUI 配置参考图指令，针对不同模式提供额外提示
- 可配置是否在文生图模式默认添加一张内置白图作为参考，以减少上游模型稀释
- 生成图片保存到 `data/plugin_data/astrbot_plugin_response2image/generated`
  - 文件名格式：`resp2img_%Y%m%d_%H%M%S.png`

## 安装

在WebUI中使用`https://github.com/FloranceYeh/astrbot_plugin_response2image`git地址安装插件，或将仓库放入 `AstrBot/data/plugins/` 目录后，在 WebUI 插件管理中启用并配置

## 配置

在 WebUI 配置以下字段：

- `base_url`: 例如 `https://api.openai.com`（无需包含 `/v1`）
- `api_key`: 接口密钥
- `model`: 模型 ID（需支持 `image_generation` 工具）
- `timeout_seconds`: 请求超时（秒）
- `reference_prompt_edit`: 仅在改图模式或自动模式且包含参考图时使用，支持多行，每行非空行作为一条指令
- `reference_prompt_selfie`: 仅在自拍模式使用，支持多行，每行非空行作为一条指令
- `text_mode_use_white_reference_image`: 启用后，文生图模式会默默添加一张内置白图作为参考，以减少上游模型稀释
- `send_generated_image_in_chat`: 启用后，LLM tool 生成成功会直接把图片发送到当前对话；关闭时仅返回状态文本/路径信息
- `selfie_reference_images`: 自拍参考图（可在 WebUI 上传；会与 `selfie_ref set` 保存的参考图合并使用）

## 使用

### 命令：
- `r2i` 命令组
- 子命令：
  - `help` 显示帮助信息
  - `img <提示词> [--ref] [--size]` 自动模式（有图则改图，无图则文生图）
  - `aiimg <提示词> [--size]` 文生图模式
  - `aiedit <提示词> [--ref] [--size]` 改图模式
  - `selfie <提示词> [--ref] [--size]` 自拍模式
  - `selfie_ref set/list/clear` 自拍参考照管理

示例：

```
r2i img 一只在雨中奔跑的柴犬，皮克斯动画风格
r2i aiimg 夜色霓虹城市
r2i aiedit 把参考图改成水彩风格 --ref https://example.com/input.png --size 1024x1024
r2i aiedit 把参考图加上蓝色天空 --ref C:\Images\input.jpg
r2i selfie_ref 设置 C:\Images\input.jpg
r2i selfie 日常自拍照，微笑，窗边自然光
```

### LLM_Tool:

- `r2i_img <提示词> [<参考图>] [size]` 自动模式（有图则改图，无图则文生图）
- `r2i_aiimg <提示词> [size]` 文生图模式
- `r2i_aiedit <提示词> <参考图> [size]` 改图模式
- `r2i_selfie <提示词> [参考图] [size]` 自拍模式（使用配置的参考图）

## 说明

- 使用`r2i`命令组内的命令时，要加上`r2i`前缀，例如`r2i img`，而不是直接使用`img`
- 命令里的 `--ref` 仍支持多个图片，用英文逗号分隔；LLM tool 建议改用独立 `ref` 参数传递
- 若消息或引用中包含图片，会自动作为参考图参与图生图请求
- 自拍模式优先使用命令或消息中的参考图，否则合并使用 WebUI 配置上传和 `selfie_ref set` 保存的参考照
- 若未收到图片，通常是模型未调用 `image_generation` 工具或模型不支持该能力


## 鸣谢
- [CodeBoy](https://github.com/CodeBoy2006) 提供了宝贵的建议和指导
- 项目灵感来源：[CodeBoy2006/responses-images-proxy](https://github.com/CodeBoy2006/responses-images-proxy)
- 此外，部分代码参考了 Codeboy 提供的静态纯前端版

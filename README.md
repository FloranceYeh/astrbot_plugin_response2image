# Response2Image

基于 Responses API 的 AstrBot 图像生成插件，支持文生图、改图、自拍三种模式，自动保存生成图片并返回。

## 功能

- 通过 `/v1/responses` 流式获取图片结果
- 文生图 / 改图 / 自拍三种模式
- 支持 `--ref` 参考图（URL / data:image / 本地文件路径）
- 自动从消息/引用里提取图片作为参考图
- 生成图片保存到 `data/plugin_data/astrbot_plugin_response2image/generated`

## 安装

在WebUI中使用`https://github.com/FloranceYeh/astrbot_plugin_response2image`git地址安装插件，或将仓库放入 `AstrBot/data/plugins/` 目录后，在 WebUI 插件管理中启用并配置。

## 配置

在 WebUI 配置以下字段：

- `base_url`: 例如 `https://api.openai.com`（无需包含 `/v1`）
- `api_key`: 接口密钥
- `model`: 模型 ID（需支持 `image_generation` 工具）
- `timeout_seconds`: 请求超时（秒）
- `selfie_reference_images`: 自拍参考图（可在 WebUI 上传；会与 `selfie_ref set` 保存的参考图合并使用）

## 使用

### 命令：

- `img <提示词> [--ref 图片URL]` 自动模式（有图则改图，无图则文生图）
  - 别名：`画图` `绘图` `r2i` `resp2img`、
- `aiimg <提示词>` 文生图模式
  - 别名：`文生图`、`生图`
- `aiedit <提示词> [--ref 图片URL]` 改图模式
  - 别名：`改图`、`图生图`
- `selfie <提示词> [--ref 图片URL]` 自拍模式
  - 别名：`自拍`
- `selfie_ref set/list/clear` 自拍参考照管理
  - 别名：`自拍参考 设置/查看/删除`

示例：

```
r2i img 一只在雨中奔跑的柴犬，皮克斯动画风格
r2i aiimg 夜色霓虹城市
r2i aiedit 把参考图改成水彩风格 --ref https://example.com/input.png
r2i aiedit 把参考图加上蓝色天空 --ref C:\Images\input.jpg
r2i selfie_ref 设置 C:\Images\input.jpg
r2i selfie 日常自拍照，微笑，窗边自然光
```

### LLM_Tool:

- `r2i_aiimg` 文生图
- `r2i_aiedit` 改图
- `r2i_selfie` 自拍

## 说明

- `--ref` 支持多个图片，用英文逗号分隔。
- 若消息或引用中包含图片，会自动作为参考图参与图生图请求。
- 自拍模式优先使用命令或消息中的参考图，否则合并使用 WebUI 配置上传和 `selfie_ref set` 保存的参考照。
- 若未收到图片，通常是模型未调用 `image_generation` 工具或模型不支持该能力。

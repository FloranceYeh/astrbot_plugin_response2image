# Response2Image

基于 Responses API 的 AstrBot 图像生成插件，支持文生图与参考图编辑，自动保存生成图片并返回。

## 功能

- 通过 `/v1/responses` 流式获取图片结果
- 支持 `--ref` 参考图（URL / data:image / 本地文件路径）
- 自动从消息/引用里提取图片作为参考图
- 支持 `--model` 覆盖配置模型
- 生成图片保存到 `data/plugin_data/astrbot_plugin_response2image/generated`

## 安装

将仓库放入 `AstrBot/data/plugins/` 目录后，在 WebUI 插件管理中启用并配置。

## 配置

在 WebUI 配置以下字段：

- `base_url`: 例如 `https://api.openai.com`（无需包含 `/v1`）
- `api_key`: 接口密钥
- `model`: 模型 ID（需支持 `image_generation` 工具）
- `timeout_seconds`: 请求超时（秒）

## 使用

命令：`img <提示词> [--ref 图片URL] [--model 模型]`

别名：`画图` `绘图` `r2i` `resp2img`

示例：

```
img 一只在雨中奔跑的柴犬，皮克斯动画风格
img 夜色霓虹城市 --model gpt-4o-mini
img 把参考图改成水彩风格 --ref https://example.com/input.png
img 把参考图加上蓝色天空 --ref C:\Images\input.jpg
```

## 说明

- `--ref` 支持多个图片，用英文逗号分隔。
- 若消息或引用中包含图片，会自动作为参考图参与图生图请求。
- 若未收到图片，通常是模型未调用 `image_generation` 工具或模型不支持该能力。

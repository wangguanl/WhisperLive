# WhisperLive 适配网关

把 `voxtral-app-api.md` 契约桥接到 WhisperLive STT 引擎的中间层。**不修改 WhisperLive 引擎代码，前端也无需改动。**

## 架构

```
前端（按契约）  ⇄  网关 Gateway  ⇄  WhisperLive 引擎（47831）
  ws://47833        ws://47833         ws://47831
  http://47834       转换层
```

## 启动

前置：先启动 WhisperLive 引擎（`run_server.py --port 47831`，沿用 `E:\Pro2\.env` 约定）。

```bash
# 默认端口 47833/47834（沿 E:\Pro2\.env 的 WHISPERLIVE_GW_WS_PORT / WHISPERLIVE_GW_HTTP_PORT）
python run_gateway.py

# 自定义端口/上游
GW_WS_PORT=47833 GW_HTTP_PORT=47834 GW_UPSTREAM_PORT=47831 python run_gateway.py
```

也可用 `config.json` 覆盖配置（字段见 `gateway/config.py`，默认值已对齐 `.env`）。

## 对外开放接口

| 协议 | 地址 |
|---|---|
| WebSocket 流式转写 | `ws://127.0.0.1:47833/v1/stream/transcriptions` |
| HTTP 模型管理 | `http://127.0.0.1:47834` |

### WebSocket 用法（契约 §2）

- 控制消息（文本帧）：`{"type":"start","lang":"zh"}` / `{"type":"config","lang":"en"}` / `{"type":"stop"}`
- 音频（二进制帧）：16k / mono / s16le，建议 480ms = 7680 字节
- 返回（文本帧）：`session_started` / `partial` / `transcript` / `session_ended` / `error`

### REST 用法（契约 §3）

| 端点 | 说明 |
|---|---|
| `GET /health` | 健康检查，返回当前模型与会话数 |
| `GET /v1/models` | 模型列表 + 状态 |
| `GET /v1/models/{id}/progress` | 下载进度 |
| `POST /v1/models/select` | 选择/切换模型（有会话进行时 409）|
| `POST /v1/models/download` | 下载模型 |

统一响应外壳：成功 `{"ok":true,...}`，失败 `{"ok":false,"error":"..."}`。
错误码：`400` 参数错误、`404` 未知模型、`409` 状态冲突。

## 模型登记

`gateway/config.py` 的 `MODELS_REGISTRY` 是数据驱动的登记表。新增模型只需追加一条：
```python
{
    "id": "faster-whisper-small",        # 前端引用的逻辑 ID
    "name": "Faster Whisper Small",
    "repo_id": "Systran/faster-whisper-small",
    "family": "asr",
    "size_hint": "约 464 MB (int8)",
    "local_dir": r"E:\huggingface_cache\hub\models--Systran--faster-whisper-small",
    "engine_model": "small",             # 传给引擎的模型名
}
```

## 关键实现点

- 一个前端连接 = 一个上游 WhisperLive 会话，断开即结束。
- 音频透传（16k/mono/int16），不做重采样。
- 定稿段去重：上游每次返回全部已定稿段，网关只对新增定稿发 `transcript`，避免重复。
- 契约约定不启用应用层 keepalive，网关已遵守（`ping_interval=None`）。
- 引擎会话中途不支持改语言，`config` 记录新语言并在后续生效。

## 文件结构

```
gateway/
  __init__.py
  config.py            # 配置 + 模型登记表
  models_registry.py   # 模型状态/下载/选中管理
  ws_gateway.py        # 47833 WebSocket 流式转写适配
  rest_api.py          # 47834 REST 模型管理
run_gateway.py         # 启动入口
```

## 复用说明

网关对外契约固定，内部引擎可替换。接入新后端（如 Voxtral.c）时，新增一个"后端适配器"实现与 WhisperLive 适配器相同的内部接口即可，前端无需改动。
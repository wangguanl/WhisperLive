# 实时转写服务 接口契约文档

> 版本：v1.0 · 更新：2026-08-09 · 适用后端：Voxtral.c 参考实现
> 本文档是**前端对接的唯一契约**。前端是通用客户端，可对接任意实现了本文「统一约定」的后端；Voxtral.c 只是当前后端之一。

## 1. 总览

服务端同时开放两类接口，运行在同一进程、不同端口：

| 协议 | 用途 | 默认地址 |
|---|---|---|
| WebSocket | 实时音频推流 + 转写结果回传 | `ws://127.0.0.1:8765` |
| HTTP REST | 健康检查 / 模型管理 / 下载进度 | `http://127.0.0.1:8766` |

两个地址均可通过服务端 `config.json` 或启动参数覆盖（`--port`、`--http-port`、`--host`、`--http-host` 等）。前端应把地址做成可配置项（配置文件 + 界面输入），不要写死。

### 1.1 通用约定

- 所有 HTTP 响应均为 JSON，`Content-Type: application/json`，中文不转义（`ensure_ascii=False`）。
- HTTP 统一响应外壳：
  - 成功：`200`，`{"ok": true, ...}`
  - 失败：`400 / 404 / 409`，`{"ok": false, "error": "<原因>"}`
- 模型状态 `status` 三态：`downloaded`（已下载）、`not_downloaded`（未下载）、`downloading`（下载中）。
- 语言码使用 Voxtral 语音码：`zh / en / ja / ko / es / fr / de / it / nl / pt / hi / ar / ru`。

---

## 2. WebSocket 流式转写接口

### 2.1 端点

```
ws://<host>:<port>/v1/stream/transcriptions
```

连接即代表一个转写会话。**一个连接 = 一个会话**，断开即结束。

### 2.2 音频格式（客户端 → 服务端，二进制帧）

| 项 | 值 |
|---|---|
| 编码 | 原始 PCM |
| 采样率 | 16 kHz |
| 声道 | 单声道（mono） |
| 位深/字节序 | s16le（16 位有符号，小端） |
| 分块大小 | 建议 480ms，即 7680 字节 |

客户端负责把任何来源的音频统一重采样成该格式；服务端零转换直接喂模型。小于一个分块的残留音频在 `stop` 时会由服务端一并处理。

### 2.3 控制消息（客户端 → 服务端，JSON 文本帧）

| type | 字段 | 说明 |
|---|---|---|
| `start` | `lang`（可选，默认 `zh`） | 开始会话，重置缓冲区与文本 |
| `config` | `lang` | 会话进行中切换识别语言 |
| `stop` | — | 结束会话，服务端冲刷残留音频并返回最终定稿，随后发 `session_ended` |

示例：

```json
{"type": "start", "lang": "zh"}
{"type": "config", "lang": "en"}
{"type": "stop"}
```

### 2.4 返回消息（服务端 → 客户端，JSON 文本帧）

| type | 字段 | 说明 |
|---|---|---|
| `session_started` | `session_id` | 会话建立成功，`start` 后返回 |
| `partial` | `text, lang, isFinal, seq` | 中间结果（尚未定稿） |
| `transcript` | `text, lang, isFinal, seq` | 已定稿文本片段，`isFinal: true` 表示该段不再变化 |
| `session_ended` | `session_id` | 会话正常结束 |
| `error` | `message` | 错误信息 |

字段说明：

- `text`：文本内容（累计到句末尾；`partial` 为尚在累积的中间文本，`transcript` 为已定稿的一段）。
- `lang`：当前会话识别语言（回显最近一次 `start`/`config` 指定的值）。
- `isFinal`：布尔，`true` 表示定稿可落盘；`false` 表示中间结果仅用于实时预览。
- `seq`：整数序号，单调递增，用于前端排序/去重。

示例：

```json
{"type": "session_started", "session_id": "a1b2c3d4"}
{"type": "partial", "text": "今天天气很", "lang": "zh", "isFinal": false, "seq": 1}
{"type": "transcript", "text": "今天天气很好。", "lang": "zh", "isFinal": true, "seq": 2}
{"type": "session_ended", "session_id": "a1b2c3d4"}
{"type": "error", "message": "invalid JSON control message"}
```

### 2.5 交互流程（时序）

1. 客户端建立 WebSocket 连接。
2. 发送 `{"type":"start","lang":"zh"}`。
3. 服务端回 `session_started`。
4. 客户端持续发送二进制音频帧（16k/mono/s16le，480ms 一帧）。
5. 服务端持续回 `partial`（实时预览）与 `transcript`（定稿）。
6. 客户端发送 `{"type":"stop"}`。
7. 服务端冲刷残留音频，补发最后一次 `transcript`（若有余文），再发 `session_ended`。
8. 客户端收到 `session_ended` 后关闭连接。

### 2.6 前端注意事项

- `partial` 用于实时预览，可覆盖显示；`transcript`（`isFinal=true`）写入正式日志/落盘。
- 服务端**不启用应用层 keepalive**（ping/pong 已关闭），这是有意为之，避免长时间推流被误判为超时断开；前端无需依赖服务端 ping，按自身心跳或 TCP 层处理即可。
- 二进制帧与 JSON 文本帧混在同一连接：前端发音频用二进制帧，发控制用文本帧；接收时按帧类型区分（文本帧解析 JSON，二进制帧直接忽略或丢弃）。

---

## 3. HTTP REST 模型管理接口

### 3.1 GET /health — 健康检查

无参数。返回当前模型与进行中会话数。

```json
{
  "ok": true,
  "status": "running",
  "current_model": "voxtral-mini-4b-realtime",
  "active_sessions": 0
}
```

前端可在启动时调用，用于确认服务可达、显示当前模型。

### 3.2 GET /v1/models — 模型列表

无参数。返回全部登记模型（含下载状态）+ 当前选中 + 会话数。

```json
{
  "ok": true,
  "current": "voxtral-mini-4b-realtime",
  "active_sessions": 0,
  "models": [
    {
      "id": "voxtral-mini-4b-realtime",
      "name": "Voxtral Mini 4B Realtime",
      "repo_id": "mistralai/Voxtral-Mini-4B-Realtime-2602",
      "description": "Mistral 官方实时语音识别基线模型，4B 参数，流式低延迟，支持 13 种语言自动识别",
      "family": "realtime",
      "size_hint": "约 8.5 GB（bf16）",
      "local_dir": "E:\\huggingface_cache\\Voxtral-Mini-4B-Realtime-2602",
      "status": "downloaded"
    }
  ]
}
```

字段说明：

- `current`：当前选中模型的 `id`；未加载时为 `null`。
- `models[].id`：逻辑 ID，**前端切换/下载模型时用此字段引用**。
- `models[].status`：`downloaded` / `not_downloaded` / `downloading`。
- `models[].download`：仅当 `status == "downloading"` 时出现，内含实时进度对象（结构见 3.3）。

> 提示：模型列表是数据驱动的，`models` 数组会随服务端登记模型动态增减；前端应通用渲染，不要假设只有一条。

### 3.3 GET /v1/models/{id}/progress — 实时下载进度

路径参数 `{id}` 为模型逻辑 ID（如 `voxtral-mini-4b-realtime`）。

返回该模型的体积/下载进度/速率/ETA。未下载且未在下载时首次调用会联网查询远程体积（可能略有延迟）。

```json
{
  "ok": true,
  "model_id": "voxtral-mini-4b-realtime",
  "name": "Voxtral Mini 4B Realtime",
  "size_hint": "约 8.5 GB（bf16）",
  "status": "downloading",
  "total_bytes": 9122611200,
  "downloaded_bytes": 3512345600,
  "progress": 0.385,
  "rate_bps": 52428800,
  "eta_seconds": 107,
  "current_file": "consolidated.safetensors.part",
  "elapsed_seconds": 67.0
}
```

字段说明：

- `status`：`downloading` / `downloaded` / `not_downloaded`。
- `progress`：0.0 ~ 1.0 的小数进度（前端可乘 100 得百分比）。
- `rate_bps`：瞬时下载速率，字节/秒（静止时为 0）。
- `eta_seconds`：预计剩余秒数；无法估算时为 `null`。
- `current_file`：当前正在下载的文件名（`downloading` 时有效）。
- `total_bytes`：远程总字节数；离线且未缓存时可能为 `null`。

错误：模型不存在返回 `404 {"ok":false,"error":"未知模型: ..."}`。

### 3.4 POST /v1/models/select — 选择/切换模型

请求体：`{"model_id": "<id>"}`。

行为：若目标模型未下载则**自动下载**；下载完成后卸载旧引擎、加载新引擎（热切换，无需重启服务）。

成功（已激活）：

```json
{"ok": true, "id": "voxtral-mini-4b-realtime", "status": "active", "changed": false}
```

- `changed: false` 表示目标即当前模型，未做操作；`changed: true` 表示已切换。

错误：

```json
{"ok": false, "error": "有 2 个进行中的转写会话，请先停止后再切换模型"}
```

- `400`：缺少 `model_id`。
- `404`：未知模型。
- `409`：**有进行中的转写会话时禁止切换**（防止旧引擎推理中被卸载导致崩溃）。前端应先向用户确认停止会话再切换。

### 3.5 POST /v1/models/download — 下载模型

请求体：`{"model_id": "<id>"}`。

行为：下载目标模型到本地（已下载则为 no-op 直接返回）。下载过程通过 `GET /v1/models/{id}/progress` 或 `GET /v1/models`（含 `models[].download`）轮询进度。

成功：

```json
{"ok": true, "id": "voxtral-mini-4b-realtime", "status": "downloaded", "downloaded": true}
```

错误：

- `400`：缺少 `model_id`。
- `404`：未知模型。
- `409`：`{"ok":false,"error":"模型已在下载中: <id>"}`（该模型正在下载时重复触发）。

---

## 4. 前端推荐对接流程

1. 启动时 `GET /health` 确认服务可达，读 `current_model` 用于界面展示。
2. `GET /v1/models` 拉取模型列表，按 `status` 渲染「已下载 / 未下载 / 下载中」。
3. 用户选择模型：
   - 若 `status == "downloaded"`，`POST /v1/models/select` 切换。
   - 若 `status == "not_downloaded"`，提示用户，`POST /v1/models/download` 触发下载，同时轮询 `GET /v1/models/{id}/progress`（或 `GET /v1/models` 的 `download` 字段）刷新进度条；下载完成后再 `select`。
   - 若当前有进行中会话，`select` 会返回 `409`，前端先停止会话再重试。
4. 转写：建立 WebSocket → `start` → 推音频 → 收 `partial`/`transcript` → `stop` → `session_ended` → 关闭。

## 5. 错误码汇总

| HTTP 状态 | 含义 | 常见场景 |
|---|---|---|
| `200` | 成功 | — |
| `400` | 参数错误 | 缺少 `model_id` |
| `404` | 资源不存在 | 未知 `model_id` 或路径 |
| `409` | 状态冲突 | 会话进行中切换模型 / 模型已在下载 |

WebSocket 层错误通过 `{"type":"error","message":...}` 返回，例如非法 JSON 控制消息、未知消息类型。
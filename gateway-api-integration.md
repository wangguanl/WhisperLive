# WhisperLive 网关 · 前端接口联调文档

> 版本：v1.1 · 更新：2026-08-11
> 用途：这是**前端对接网关的唯一契约**。本文内所有字段、JSON 示例、状态流转均与当前代码实现一致，可直接作为 AI 前端代理的联调依据。
> 后端进程：`run_gateway.py`（对外 WS + HTTP 网关），上游为 `run_server.py`（WhisperLive 引擎）。

---

## 1. 总览与架构

前端只对接**网关**，网关在内部把请求映射到 WhisperLive 引擎。前端不感知引擎的存在，因此更换后端引擎时前端无需改动。

```
┌────────────┐   WS 音频/控制      ┌──────────────────┐   WS 音频/options   ┌──────────────────┐
│   前端      │ ──────────────────▶ │   网关(gateway)   │ ──────────────────▶ │  引擎(run_server)│
│ (Web/App)  │ ◀────────────────── │ 47833 WS / 47834  │ ◀────────────────── │  47831 WS        │
└────────────┘   JSON 结果/回调     └──────────────────┘   SERVER_READY/seg  └──────────────────┘
   HTTP 模型管理 ──────────────────▶   HTTP 47834
```

| 协议 | 用途 | 前端默认地址 |
|---|---|---|
| WebSocket | 实时音频推流 + 转写结果回传 | `ws://127.0.0.1:47833/v1/stream/transcriptions` |
| HTTP REST | 健康检查 / 模型管理 / 参数预设 | `http://127.0.0.1:47834` |

> 地址建议做成前端可配置项（配置文件 + 界面输入），不要写死。

### 1.1 通用约定（前端必须遵守）

- 所有 HTTP 响应均为 JSON，`Content-Type: application/json`，中文不转义。
- HTTP 统一响应外壳：
  - 成功：`200`，`{"ok": true, ...}`
  - 失败：`400 / 404 / 409`，`{"ok": false, "error": "<原因>"}`
- 模型状态 `status` 三态：`downloaded`（已下载）、`not_downloaded`（未下载）、`downloading`（下载中）；`select` 成功响应中状态为 `active`。
- 语言码（Voxtral 语音码）：`zh / en / ja / ko / es / fr / de / it / nl / pt / hi / ar / ru`。
- 模型引用一律用逻辑 `id`（如 `whisper-small`），不要用 `repo_id`。

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

客户端负责把任何来源的音频统一重采样成该格式；服务端零转换直接喂模型。小于一个分块的残留音频在 `stop` 时由服务端一并处理。

### 2.3 控制消息（客户端 → 服务端，JSON 文本帧）

| type | 字段 | 说明 |
|---|---|---|
| `start` | `lang`（可选，默认 `zh`） | 开始会话，重置缓冲区与文本 |
| `config` | `lang` | 会话进行中切换识别语言（引擎不支持会话中途切换，记录后下次 `start` 生效） |
| `stop` | — | 结束会话，服务端发 `session_ended` |

```json
{"type": "start", "lang": "zh"}
{"type": "config", "lang": "en"}
{"type": "stop"}
```

### 2.4 返回消息（服务端 → 客户端，JSON 文本帧）

| type | 字段 | 说明 |
|---|---|---|
| `session_started` | `session_id` | 会话建立成功，`start` 后返回 |
| `partial` | `text, lang, isFinal, seq` | 中间结果（未定稿，实时预览用） |
| `transcript` | `text, lang, isFinal, seq` | 已定稿文本片段，`isFinal: true` 表示该段不再变化 |
| `session_ended` | `session_id` | 会话正常结束 |
| `error` | `message` | 错误信息 |

字段说明：
- `text`：文本内容。`partial` 为尚在累积的中间文本，`transcript` 为已定稿的一段。
- `lang`：当前会话识别语言（回显最近一次 `start`/`config` 指定的值）。
- `isFinal`：布尔，`true` 表示定稿可落盘；`false` 表示中间结果仅用于实时预览。
- `seq`：整数序号，单调递增，用于前端排序/去重。

```json
{"type": "session_started", "session_id": "a1b2c3d4"}
{"type": "partial", "text": "今天天气很", "lang": "zh", "isFinal": false, "seq": 1}
{"type": "transcript", "text": "今天天气很好。", "lang": "zh", "isFinal": true, "seq": 2}
{"type": "session_ended", "session_id": "a1b2c3d4"}
{"type": "error", "message": "invalid JSON control message"}
```

### 2.5 交互流程（时序）

1. 建立 WebSocket 连接。
2. 发送 `{"type":"start","lang":"zh"}`。
3. 服务端回 `session_started`。
4. 持续发送二进制音频帧（16k/mono/s16le，480ms 一帧）。
5. 服务端持续回 `partial`（实时预览）与 `transcript`（定稿）。
6. 发送 `{"type":"stop"}`。
7. 服务端回 `session_ended`。
8. 收到 `session_ended` 后关闭连接。

### 2.6 前端注意事项

- `partial` 用于实时预览，可覆盖显示；`transcript`（`isFinal=true`）写入正式日志/落盘。
- 服务端**不启用应用层 keepalive**（ping/pong 已关闭），前端无需依赖服务端 ping，按自身心跳或 TCP 层处理即可。
- 二进制帧与 JSON 文本帧混在同一连接：发送音频用二进制帧、发送控制用文本帧；接收时按帧类型区分（文本帧解析 JSON，二进制帧忽略）。
- 若 `start` 时上游引擎不可用，会回 `{"type":"error","message":"upstream engine unavailable"}`，前端应提示并重试。

---

## 3. HTTP REST 接口

统一前缀：`http://127.0.0.1:47834`。以下给出每个接口的**请求、成功响应、失败响应的完整 JSON**。

### 3.1 GET /health — 健康检查

无参数。用于启动时确认服务可达、显示当前模型。

```json
// 200
{
  "ok": true,
  "status": "running",
  "current_model": "whisper-small",
  "active_sessions": 0
}
```

- `current_model`：当前选中模型 `id`；未加载时为 `null`。
- `active_sessions`：当前进行中转写会话数（当前实现固定为 0）。

### 3.2 GET /v1/models — 模型列表 + 当前选中状态

无参数。返回全部登记模型（含下载状态）+ 当前选中模型及其状态。

```json
// 200
{
  "ok": true,
  "current": "whisper-small",
  "current_status": "downloaded",
  "current_downloaded": true,
  "active_sessions": 0,
  "models": [
    {
      "id": "whisper-tiny",
      "name": "Whisper Tiny",
      "repo_id": "Systran/faster-whisper-tiny",
      "family": "asr",
      "size_hint": "约 151 MB (float16)",
      "status": "downloaded"
    },
    {
      "id": "whisper-large-v3",
      "name": "Whisper Large v3",
      "repo_id": "Systran/faster-whisper-large-v3",
      "family": "asr",
      "size_hint": "约 6.17 GB (float16)",
      "status": "downloading",
      "download": {
        "model_id": "whisper-large-v3",
        "name": "Whisper Large v3",
        "size_hint": "约 6.17 GB (float16)",
        "status": "downloading",
        "total_bytes": 6620000000,
        "downloaded_bytes": 3100000000,
        "progress": 0.468,
        "rate_bps": 52428800,
        "eta_seconds": 67,
        "current_file": "model.bin.part",
        "elapsed_seconds": 59.1
      }
    }
  ]
}
```

字段说明：
- `current`：当前选中模型 `id`；未选中时为 `null`。
- `current_status`：当前选中模型的状态；`null` / `downloaded` / `not_downloaded` / `downloading`。
- `current_downloaded`：布尔，当前选中模型是否已下载。
- `models[]`：全部登记模型，数据驱动，前端应通用渲染、不要假设只有一条。
- `models[].id`：逻辑 ID，**所有模型操作（下载/选择）都用它引用**。
- `models[].status`：`downloaded` / `not_downloaded` / `downloading`。
- `models[].download`：仅当 `status == "downloading"` 时出现，内含实时进度对象（结构见下）。

### 3.3 GET /v1/models/{id}/progress — 实时下载进度

路径参数 `{id}` 为模型逻辑 ID。返回该模型的体积/下载进度/速率/ETA。

```json
// 200，下载中
{
  "ok": true,
  "model_id": "whisper-large-v3",
  "name": "Whisper Large v3",
  "size_hint": "约 6.17 GB (float16)",
  "status": "downloading",
  "total_bytes": 6620000000,
  "downloaded_bytes": 3100000000,
  "progress": 0.468,
  "rate_bps": 52428800,
  "eta_seconds": 67,
  "current_file": "model.bin.part",
  "elapsed_seconds": 59.1
}

// 200，已下载
{
  "ok": true,
  "model_id": "whisper-small",
  "name": "Whisper Small",
  "size_hint": "约 970 MB (float16)",
  "status": "downloaded",
  "total_bytes": null,
  "downloaded_bytes": null,
  "progress": 1.0,
  "rate_bps": 0,
  "eta_seconds": null,
  "current_file": null,
  "elapsed_seconds": 0.0
}

// 404，未知模型
{"ok": false, "error": "未知模型: xxx"}
```

字段说明：
- `status`：`downloading` / `downloaded` / `not_downloaded`。
- `progress`：0.0 ~ 1.0 小数进度（前端乘 100 得百分比）。
- `rate_bps`：瞬时下载速率，字节/秒（静止时为 0）。
- `eta_seconds`：预计剩余秒数；无法估算时为 `null`。
- `current_file`：当前下载中的文件名（`downloading` 时有效）。
- `total_bytes`：远程总字节数；离线 / 非下载中时可能为 `null`。

### 3.4 POST /v1/models/download — 触发下载

请求体：`{"model_id": "<id>"}`。

行为：下载目标模型到本地（已下载则为 no-op 直接返回）。下载过程通过 `GET /v1/models/{id}/progress` 或 `GET /v1/models` 的 `download` 字段轮询进度。

```json
// 请求
{"model_id": "whisper-large-v3"}

// 200，已触发下载
{"ok": true, "id": "whisper-large-v3", "status": "downloading", "downloaded": false}

// 200，已下载（no-op）
{"ok": true, "id": "whisper-tiny", "status": "downloaded", "downloaded": true}

// 400，缺 model_id
{"ok": false, "error": "缺少 model_id"}

// 404，未知模型
{"ok": false, "error": "未知模型: xxx"}

// 409，已在下载中
{"ok": false, "error": "模型已在下载中: whisper-large-v3"}
```

### 3.5 POST /v1/models/select — 选择/切换模型

请求体：`{"model_id": "<id>"}`。

行为：切到目标模型（热切换，无需重启）。

```json
// 请求
{"model_id": "whisper-small"}

// 200，已切换
{"ok": true, "id": "whisper-small", "status": "active", "changed": true}

// 200，目标即当前（未做操作）
{"ok": true, "id": "whisper-small", "status": "active", "changed": false}

// 400，缺 model_id
{"ok": false, "error": "缺少 model_id"}

// 404，未知模型
{"ok": false, "error": "未知模型: xxx"}

// 409，模型未下载
{"ok": false, "error": "模型未下载，请先下载: whisper-large-v3"}

// 409，模型下载中
{"ok": false, "error": "模型正在下载中: whisper-large-v3"}
```

- `changed: false` 表示目标即当前模型，未做操作；`changed: true` 表示已切换。
- 注意：**当前实现里 `select` 不会自动触发下载**，目标未下载时返回 `409`。前端应先确认模型已下载再 `select`。

### 3.6 GET /v1/stream/parameters — 转写参数预设（按模型实测能力）

无参数。对**当前选中模型**实测实时率（RTF），据此返回推荐的分块时长与缓冲时长。用于让前端在建立会话前拿到与模型能力匹配的参数。

```json
// 200
{
  "ok": true,
  "chunk_ms": 300,
  "buffer_secs": 2.0,
  "chunk_ms_range": [200, 800],
  "buffer_secs_range": [0.5, 60],
  "mode": "local",
  "rtf": {
    "model": "Systran/faster-whisper-small",
    "audio_secs": 5.0,
    "elapsed_secs": 0.25,
    "rtf": 0.05
  },
  "rationale": "实测模型实时率 RTF=0.05，local网络下取 300ms 分块 + 2s 缓冲"
}

// 404，未选择模型
{"ok": false, "error": "未选择模型"}

// 409，当前模型未下载
{"ok": false, "error": "当前模型未下载，请先下载后再测速"}
```

字段说明：
- `chunk_ms`：推荐分块时长（毫秒），前端切音频时按其切块。
- `buffer_secs`：推荐缓冲时长（秒）。
- `chunk_ms_range`：分块可调范围 `[min, max]`。
- `buffer_secs_range`：缓冲可调范围 `[0.5, 60]`。
- `mode`：`local`（回环/本机）或 `remote`（公网）。
- `rtf`：实测实时率对象；RTF < 1 表示比实时快。首次调用会加载模型跑一次短推理（约几秒），结果按模型缓存。
- `rationale`：服务端给出此预设的依据说明（可直接展示给用户）。

> 参数映射规则（供理解）：RTF ≤ 0.3 → 300ms/2s；≤ 0.6 → 400ms/3s；≤ 1.0 → 500ms/5s；> 1.0 → 800ms/8s。`remote` 模式下分块不低于 500ms、缓冲不低于 5s。

---

## 4. 前端推荐对接流程

### 4.1 启动与模型管理

1. 启动时 `GET /health` 确认服务可达，读 `current_model` 用于界面展示。
2. `GET /v1/models` 拉取模型列表，按 `status` 渲染「已下载 / 未下载 / 下载中」三个状态。
3. 用户选择模型：
   - 若 `status == "downloaded"`：`POST /v1/models/select` 切换。
   - 若 `status == "not_downloaded"`：提示用户，`POST /v1/models/download` 触发下载，随后轮询 `GET /v1/models/{id}/progress`（建议 500ms~1s 一次）刷新进度条；`progress` 到 1.0 或 `status` 变 `downloaded` 后，再 `select`。
   - 若当前模型 `downloading`，不触发重复下载（服务端返回 409）。

### 4.2 转写会话

1. 建立 WebSocket 连接。
2. 发送 `{"type":"start","lang":"zh"}`。
3. 收到 `session_started` 后开始推音频。
4. 持续发二进制帧（16k/mono/s16le，480ms=7680 字节）。
5. 收 `partial` 实时预览、`transcript` 落盘。
6. 发送 `{"type":"stop"}`，收到 `session_ended` 后关闭连接。

### 4.3 参数预设（可选，推荐）

用户开始转写前，调用 `GET /v1/stream/parameters` 拿到当前模型的推荐 `chunk_ms` 与 `buffer_secs`，用它们来决定音频切块大小，可获得更好的实时性。该接口仅读当前选中模型，切换模型后需重新调用。

---

## 5. 错误码汇总

| HTTP 状态 | 含义 | 常见场景 |
|---|---|---|
| `200` | 成功 | — |
| `400` | 参数错误 | 缺少 `model_id` |
| `404` | 资源不存在 | 未知 `model_id` 或路径 |
| `409` | 状态冲突 | 模型未下载 / 模型已在下载中 |

WebSocket 层错误通过 `{"type":"error","message":...}` 返回，例如非法 JSON 控制消息、上游引擎不可用。

---

## 6. 当前已知限制（联调时需知晓）

- `start` 控制消息目前**仅支持 `lang`**。设计上预留了 `chunk_ms` / `buffer_secs` 可选字段（前端把实际切块/缓冲传回后端），但**后端尚未透传到引擎**，本期前端可不传，仅用 `GET /v1/stream/parameters` 的返回值指导切块。
- `config` 切换语言在当前引擎下不会真正生效，服务端仅记录，下次 `start` 生效；前端可提示用户切换语言需重启会话。
- `active_sessions` 当前固定为 0，前端不应依赖它做并发控制。
- 模型选择热切换只影响新建立的会话；正在进行的会话不受影响。
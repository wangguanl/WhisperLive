# WhisperLive 适配网关 设计方案

> 版本：v1.0 · 更新：2026-08-09
> 目标：在不修改 WhisperLive 项目代码的前提下，让通用前端按 `voxtral-app-api.md` 契约对接 WhisperLive 的 STT 引擎。

---

## 1. 背景与目标

现有前端是**通用客户端**，只认 `voxtral-app-api.md` 定义的接口契约（WebSocket 8765 + HTTP 8766），理论上可对接任意实现了该契约的后端。当前已有一个 WhisperLive 的 STT 服务器运行在 9090 端口，能力足够（实时转写、16k 采样、int16 音频、多语言），但它的原生协议与契约不兼容。

**方案核心**：新建一个**适配网关**作为中间层，对外提供契约要求的 8765/8766 接口，对内连接 WhisperLive 的 9090 引擎。**WhisperLive 代码零改动**，前端代码零改动，两者通过网关打通。

## 2. 架构

```
前端（通用客户端，按契约）                 WhisperLive STT 引擎（原样不变）
        │      ▲                                    │      ▲
        │ 契约  │                                    │ 原协议│
        ▼      │                                    ▼      │
   ┌───────────────────────┐                 ┌────────────────────┐
   │     适配网关 Gateway   │ ──── 连接 ────▶ │  ws://127.0.0.1:9090 │
   │  WS :8765 /v1/stream/  │                 │  (faster_whisper)   │
   │  HTTP:8766 模型管理    │                 └────────────────────┘
   └───────────────────────┘
```

- 网关对外地址：`ws://127.0.0.1:8765`、`http://127.0.0.1:8766`（与契约默认一致，可配置）
- 网关对内地址：`ws://127.0.0.1:9090`（WhisperLive 现有服务，可配置）
- 协议转换全部发生在网关内部，引擎与前端互不感知。

## 3. 网关职责（协议翻译映射）

### 3.1 WebSocket 流式转写（契约 §2 → WhisperLive）

| 前端契约（8765） | 网关处理的转换 | WhisperLive（9090） |
|---|---|---|
| `{"type":"start","lang":"zh"}` | 建立对 9090 的连接，发送包含 `uid/language/task/audio_format=int16` 的 JSON options | 连接后首帧 JSON options |
| 二进制音频帧（16k/mono/s16le） | 透传为 int16 音频帧 | 二进制音频帧 |
| `{"type":"config","lang":"en"}` | 更新会话语言参数 | 会话级语言切换 |
| `{"type":"stop"}` | 冲刷残留、结束上游会话、清理 | 断开连接 |
| — | 上游 `SERVER_READY` → 回 `session_started` | 返回消息 |
| — | 上游 `partial_transcript` → 回 `partial`（`isFinal:false`） | 返回消息 |
| — | 上游 `final_transcript` → 回 `transcript`（`isFinal:true`） | 返回消息 |
| — | 上游连接关闭 → 回 `session_ended` | 返回消息 |

**关键映射约定**
- 一个前端连接 = 一个上游 WhisperLive 会话，断开即结束。
- `lang` 使用契约语言码（zh/en/ja/ko/es/fr/de/it/nl/pt/hi/ar/ru），WhisperLive 语音码兼容，直接透传。
- 音频格式：契约/WhisperLive 均为 16k/mono/s16le，网关需在初始化时向 9090 请求 `audio_format="int16"`。
- 控制类消息（`start/config/stop`）走文本帧，音频走二进制帧，网关按帧类型区分路由。

### 3.2 HTTP REST 模型管理（契约 §3 → 网关本地实现）

网关维护一张**模型登记表**（可配置/数据驱动），把 WhisperLive 可用的模型登记进来，并实现契约要求的 REST 接口：

| 契约端点 | 网关行为 |
|---|---|
| `GET /health` | 检测 9090 是否可达，回 `current_model`、`active_sessions` |
| `GET /v1/models` | 返回登记模型列表 + 下载状态 + 当前选中 |
| `GET /v1/models/{id}/progress` | 返回体积/进度/速率/ETA（未下载时准实时查询）|
| `POST /v1/models/select` | 切换模型（未下载先下载）；有进行中会话返回 409 |
| `POST /v1/models/download` | 触发模型下载，轮询进度 |

**模型登记示例**（当前已具备的模型）
```json
{
  "id": "faster-whisper-small",
  "name": "Faster Whisper Small",
  "repo_id": "Systran/faster-whisper-small",
  "family": "asr",
  "status": "downloaded",
  "local_dir": "E:\\huggingface_cache\\hub\\models--Systran--faster-whisper-small"
}
```

### 3.3 统一响应外壳（契约 §1.1）
- 成功：`200 {"ok": true, ...}`
- 失败：`400/404/409 {"ok": false, "error": "<原因>"}`
- 中文不转义（`ensure_ascii=False`）；`Content-Type: application/json`。

## 4. 模型状态机（契约 §1.1）

```
not_downloaded ── download ──▶ downloading ── 完成 ──▶ downloaded
                                        └── select ──▶ 引擎切换（active）
downloaded ── select ──▶ active（changed:true/false）
有会话进行中时 select/download 均禁止（409）
```

## 5. 关键实现要点

1. **协议转换**：核心是 `start/config/stop` ↔ `JSON options` 与 `partial_transcript/final_transcript` ↔ `partial/transcript` 的双向映射，逻辑集中在网关。
2. **音频透传**：契约与引擎音频格式一致（16k/mono/s16le），网关做透传，不做重采样，避免额外开销。
3. **会话生命周期**：每个前端连接独立对应一个上游会话，`stop` 后正确冲刷残留并回收资源。
4. **模型热切换**：`select` 时若引擎需要换模型，遵循"先停会话再切换"（409 保护），避免卸载中使用。
5. **配置化地址**：8765/8766/9090 及 host 均可通过配置文件或启动参数覆盖，不写死。
6. **保活策略**：契约约定服务端不启用应用层 keepalive，网关遵从此约定，避免长推流被误判超时。

## 6. 目录与文件规划（建议）

```
E:\Pro2\WhisperLive\
  gateway\
    __init__.py
    config.py          # 端口/模型登记表/上游地址配置
    ws_gateway.py      # 8765 WebSocket 流式转写适配
    rest_api.py        # 8766 REST 模型管理
    models_registry.py # 模型登记/下载/进度管理
  run_gateway.py       # 网关启动入口
```

> 说明：`gateway/` 为独立新增模块，不修改 `whisper_live/` 下任何引擎代码。

## 7. 验证方式

1. 启动 WhisperLive 9090 服务（现有命令）。
2. 启动网关 `run_gateway.py`。
3. `GET http://127.0.0.1:8766/health` 返回 `ok:true`。
4. 用契约示例音频走 WebSocket 完整流程：`start` → 推音频 → 收 `partial/transcript` → `stop` → `session_ended`。
5. 前端直连 8765/8766 验证端到端。

## 8. 复用说明

- 本方案**不绑定 WhisperLive**：网关对外的 8765/8766 契约固定不变，内部引擎可替换为任何实现、任何后端（如 Voxtral.c）。
- 接入新后端时，仅需新增一个"后端适配器"，实现与 WhisperLive 适配器相同的内部接口，前端无需改动。
- 模型登记表为数据驱动，新增模型只需在配置追加条目。

## 9. 待办清单

- [ ] 搭建 `gateway/` 模块骨架与配置
- [ ] 实现 WS 流式转写适配（协议映射 + 音频透传 + 会话管理）
- [ ] 实现 REST 模型管理（健康/列表/进度/选择/下载）
- [ ] 实现统一响应外壳与错误语义（400/404/409）
- [ ] 模型状态机与热切换保护
- [ ] 端到端联调（9090 + 网关 + 前端契约示例）
- [ ] 编写网关自身 README 与启动说明
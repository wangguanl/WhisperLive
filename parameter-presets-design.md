# 参数预设与获取参数接口设计

> 状态：设计记录，待开发落地。本文记录参数预设功能的目标接口、测速时机决策与当前现状，后续开发按此补充实现。

## 目标

前端与后端解耦的前提下，解决两个问题：

1. 后端按当前模型的实际能力给出一套预设值（分块时长、缓冲时长），前端可据此设置会话参数。
2. 前端把实际使用的分块时长、缓冲时长传回后端，使会话参数可被后端感知。

## 测速时机决策

测速对象分两类，时机不同：

| 测速对象 | 属性 | 时机 |
|---|---|---|
| 模型实际推理能力（实时率 RTF） | 服务端/模型固有，稳定 | 启动服务或模型加载/选定时测量一次，缓存 |
| 前端到后端的网络延迟/抖动 | 每个客户端不同 | 前端调用接口时可选做轻量探测，属后置增强项 |

结论：**基础预设由服务启动时测量模型能力并缓存**，`GET /v1/stream/parameters` 直接读缓存返回，不重复测速。这是主方案；按客户端网络细化分块/缓冲作为可选增强，暂不纳入首版。

## 现状

当前 `gateway/rest_api.py` 的 `recommend_parameters()` 没有做任何真实测速，是静态启发式：

- 依据 `upstream_host` 是否回环（`127.0.0.1`/`localhost`/`::1`/`0.0.0.0`）判定本地或远程；
- 依当前选中模型名是否含 `large`/`medium` 判定重载；
- 按上述两档返回固定 `chunk_ms`/`buffer_secs`（本地 `300ms/2s` 或 `400ms/3s`，远程 `500ms/5s` 或 `800ms/8s`）。

该返回仅为经验推荐，未反映模型真实处理速度，也未覆盖单个客户端的网络差异。

## 目标接口设计

### 获取参数接口

现有 `GET /v1/stream/parameters` 保留，作为前端获取预设值的入口。首版输入无请求体，读取启动时缓存的预设；后续如需按客户端网络细化，可增加可选入参（如客户端侧测得的 RTT）。

返回体（沿用现有统一外壳）：

```json
{
  "ok": true,
  "chunk_ms": 300,
  "buffer_secs": 2.0,
  "chunk_ms_range": [200, 800],
  "buffer_secs_range": [0.5, 60],
  "mode": "local",
  "rationale": "本地回环网络，300ms 分块 + 2s 缓冲"
}
```

### 前端传参通道

前端把实际分块/缓冲传回后端，落在 WebSocket `start` 消息上。当前 `start` 仅支持 `lang`，需扩展：

```json
{"type": "start", "lang": "zh", "chunk_ms": 300, "buffer_secs": 2.0}
```

网关解析后透传到上游引擎 options（`gateway/ws_gateway.py` 的 `connect_upstream()`），未传时使用启动缓存或默认值。

## 待开发清单

- [x] 网关登记 Whisper 全系列模型（`gateway/config.py`，16 个）
- [x] `GET /v1/models` 增加当前模型下载状态字段（`current_status`/`current_downloaded`）
- [x] `POST /v1/models/download` 真实 HF 下载（`huggingface_hub.snapshot_download`）
- [x] `GET /v1/models/{id}/progress` 实时返回下载速度/大小/进度
- [x] `GET /v1/stream/parameters` 实测选中模型实时率 RTF 并据其返回预设（`gateway/rtf_probe.py`）
- [ ] `start` 消息增加可选 `chunk_ms`/`buffer_secs` 字段并透传到上游引擎 options
- [ ] 可选增强：接口调用时按客户端 RTT 细化分块/缓冲

> 说明：RTF 实测在首次调用 `/v1/stream/parameters` 时加载模型跑一次短推理，结果按模型缓存；模型未下载时返回 409。测速 e2e 实测 `whisper-small` RTF≈0.05，返回 `chunk_ms=300`、`buffer_secs=2.0`。
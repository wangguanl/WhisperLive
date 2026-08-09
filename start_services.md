# WhisperLive 服务启动脚本说明

## 概述

`start_services.ps1` 是 WhisperLive 项目的一键启动脚本，负责拉起并管理两个后台服务：

| 服务 | 说明 | 脚本 |
|------|------|------|
| WhisperLive 引擎 | 实时语音转写后端（GPU / faster_whisper） | `run_server.py` |
| 适配网关 | 把引擎协议桥接为前端契约（WS + REST） | `run_gateway.py` |

脚本是**幂等**的：无论服务是否已经在运行，执行一次都会得到一份全新干净的服务（先停掉旧的，再启动新的）。

## 端口约定

端口严格遵循 `E:\Pro2\.env`：

| `.env` 变量 | 端口 | 用途 |
|------|------|------|
| `WHISPERLIVE_WS_PORT` | `47831` | 引擎 WebSocket |
| `WHISPERLIVE_REST_PORT` | `47832` | 引擎 REST（需 `--enable_rest` 才开启） |
| `WHISPERLIVE_GW_WS_PORT` | `47833` | 网关 WebSocket 流式转写 |
| `WHISPERLIVE_GW_HTTP_PORT` | `47834` | 网关 HTTP 模型管理 |

前端对接地址：

- WS：`ws://127.0.0.1:47833/v1/stream/transcriptions`
- HTTP：`http://127.0.0.1:47834`

## 使用方法

### 方式一：PowerShell 直接执行

```powershell
powershell -ExecutionPolicy Bypass -File E:\Pro2\WhisperLive\start_services.ps1
```

### 方式二：在已打开的 PowerShell 里执行

```powershell
cd E:\Pro2\WhisperLive
.\start_services.ps1
```

> 若提示执行策略限制，加 `-ExecutionPolicy Bypass` 或先执行 `Set-ExecutionPolicy -Scope Process Bypass`。

## 脚本做了什么

1. **创建日志目录**：`E:\Pro2\WhisperLive\logs`（不存在则创建）。
2. **启动引擎**：检查端口 `47831` 是否被占用，若被占用先停掉占用进程，再用 `.venv` 的 Python 启动 `run_server.py --port 47831 --rest_port 47832 --backend faster_whisper`，并自动配置 GPU 运行所需环境。
3. **启动网关**：检查端口 `47834` 是否被占用，若被占用先停掉占用进程，再用 `.venv` 的 Python 启动 `run_gateway.py`（默认上游为引擎 `47831`）。
4. **等待就绪**：轮询等待引擎（最长 90 秒）和网关（最长 30 秒）开始监听端口。
5. **健康检查**：请求 `http://127.0.0.1:47834/health`，输出服务状态汇总。

### GPU 运行环境（引擎自动注入）

引擎启动时会自动设置以下环境变量与 DLL 搜索路径，确保在 RTX 4080 上使用 GPU 推理：

- `HF_HUB_DISABLE_XET=1`：避免 Hugging Face 下载模型时 401 错误
- `HF_HOME=E:\huggingface_cache`：模型缓存目录
- 将以下 nvidia 动态库目录加入 `PATH`：
  - `.venv\Lib\site-packages\nvidia\cublas\bin`
  - `.venv\Lib\site-packages\nvidia\cuda_nvrtc\bin`
  - `.venv\Lib\site-packages\nvidia\curand\bin`

## 日志

服务输出写入 `logs` 目录，便于排查：

| 文件 | 内容 |
|------|------|
| `logs\engine.log` | 引擎标准输出 |
| `logs\engine.err.log` | 引擎错误输出 |
| `logs\gateway.log` | 网关标准输出 |
| `logs\gateway.err.log` | 网关错误输出 |

## 验证

脚本执行完成后会打印服务状态，正常时输出类似：

```
==== Service status ====
  Engine WS   :  ws://127.0.0.1:47831   [OK]
  Gateway WS  :  ws://127.0.0.1:47833/v1/stream/transcriptions   [OK]
  Gateway HTTP:  http://127.0.0.1:47834   [OK]
  Health      :  {"ok":true,"status":"running","current_model":"faster-whisper-small","active_sessions":0}
```

也可手动验证：

```powershell
# 健康检查
Invoke-WebRequest -Uri 'http://127.0.0.1:47834/health' -UseBasicParsing

# 模型列表
Invoke-WebRequest -Uri 'http://127.0.0.1:47834/v1/models' -UseBasicParsing
```

## 常见问题排查

| 现象 | 可能原因 | 处理 |
|------|----------|------|
| 引擎 `start timeout` | 模型首次加载较慢或 GPU 环境异常 | 查看 `logs\engine.err.log` |
| 网关 `start timeout` | 依赖缺失 | 查看 `logs\gateway.err.log`，确认 `.venv` 已安装 `websockets` |
| 前端连不上 `47834` | 网关未启动或端口被占用 | 重跑脚本，检查 `logs\gateway.log` |
| WS 连接报 `upstream engine unavailable` | 引擎未就绪或上游端口不对 | 确认引擎监听 `47831`，重跑脚本 |
| 中文异常/脚本报编码错误 | 用系统 PowerShell 5 直接执行含中文脚本 | 本脚本已用纯 ASCII 编写，规避此问题 |
# 运行命令

- 项目：WhisperLive（Collabora 近实时 Whisper）
- 生成时间：2026-09-09
- 运行方式：直接运行（`.venv` + faster_whisper）；可选 Docker
- 硬件评估：**满足**（small/medium/large-v3 float16 均远低于 16GB）

## 环境准备

```powershell
$env:Path = "E:\Programs\ffmpeg-master-latest-win64-gpl\bin;" + $env:Path
$env:HF_ENDPOINT = "https://hf-mirror.com"
$env:HF_HOME = "E:\huggingface_cache"
# 已有 .venv；ctranslate2 可见 CUDA device；推理走 faster_whisper
```

## 启动

- 推荐：`pwsh -NoProfile -File .\start.ps1`（仓库根目录）
- 说明：菜单可选 **server**（默认）/ **gateway** / **client**；可多选但 server+其它占卡时会确认
- 等价手动命令：
  - server：`python run_server.py --port 47831 --rest_port 47832 --backend faster_whisper`
  - gateway：先起 server，再 `python run_gateway.py`（`GW_WS_PORT`/`GW_HTTP_PORT`/`GW_UPSTREAM_PORT`）
  - client：`python run_client.py --server localhost --port 47831 --files jfk_resampled.wav --model small`

## 验证

- server：WS 端口监听；client 对样例音频出字幕
- gateway：HTTP 健康检查与 WS 流式路径可用
- 日志无 CUDA OOM

## 备注

- 镜像：HF=`hf-mirror.com`
- `torch` 当前为 CPU 轮子（`2.13.0+cpu`），但 **ctranslate2 CUDA 可用**（`get_cuda_device_count()==1`），日常推理不依赖 torch CUDA
- 旧 `start_services.ps1` 会同时拉起 engine+gateway；新 `start.ps1` 交互按需选择
- 本次未长时间启动 GPU 服务

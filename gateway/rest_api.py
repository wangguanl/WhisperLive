"""47834 REST 模型管理：health / models / progress / select / download。

统一响应外壳（契约 §1.1）：
  成功 200 {"ok": true, ...}；失败 400/404/409 {"ok": false, "error": "..."}
中文不转义（ensure_ascii=False）。
"""
import asyncio
import logging

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from gateway import config
from gateway.models_registry import get_registry
from gateway.ws_gateway import WsGateway  # noqa: F401  (复用上游可达性概念)

log = logging.getLogger("gateway.rest")

# 模型规模分档：family 或 size 关键字 → (小分块, 大分块) 负载系数
# local: 回环低延迟；remote: 公网需抗抖动。返回 (chunk_ms, buffer_secs, range, mode, rationale)
def _is_loopback(host: str) -> bool:
    return host in ("127.0.0.1", "localhost", "::1", "0.0.0.0")


def recommend_parameters(cfg: dict) -> dict:
    """经验映射法：按 本地/远程 + 模型规模 返回推荐分块与缓冲（契约 §3 方案2）。"""
    mode = "local" if _is_loopback(cfg.get("upstream_host", "127.0.0.1")) else "remote"
    engine = get_registry().current_engine_model()

    # 模型规模：以型号名粗判（large/medium 偏大，small/base 偏小）
    size = next((k for k in ("large", "medium", "small", "base", "tiny") if k in engine.lower()), "small")
    heavy = size in ("large", "medium")

    if mode == "local":
        chunk_ms, buf = (400, 3.0) if heavy else (300, 2.0)
        rationale = (
            "本地回环网络延迟极低、无公网抖动，{}分块 + {}s 缓冲可在实时性与稳定性间取得平衡"
            .format(chunk_ms, int(buf))
        )
    else:
        chunk_ms, buf = (800, 8.0) if heavy else (500, 5.0)
        rationale = (
            "远程/公网环境需抗网络抖动与丢包，{}ms 分块 + {}s 缓冲优先保证稳定，牺牲少量实时性"
            .format(chunk_ms, int(buf))
        )

    return {
        "chunk_ms": chunk_ms,
        "buffer_secs": buf,
        "chunk_ms_range": [200, 800] if mode == "local" else [400, 1200],
        "buffer_secs_range": [0.5, 60],
        "mode": mode,
        "rationale": rationale,
    }


def build_app(cfg: dict) -> FastAPI:
    app = FastAPI(title="WhisperLive Gateway API")

    def _ok(data: dict) -> JSONResponse:
        return JSONResponse({"ok": True, **data}, status_code=200)

    def _err(status: int, message: str) -> JSONResponse:
        return JSONResponse({"ok": False, "error": message}, status_code=status)

    registry = get_registry()

    @app.get("/health")
    async def health():
        meta = registry.current_model_meta()
        return _ok({
            "status": "running",
            "current_model": meta["id"] if meta else None,
            "active_sessions": 0,
        })

    @app.get("/v1/models")
    async def list_models():
        return _ok({
            "current": registry.current(),
            "active_sessions": 0,
            "models": registry.list_models(),
        })

    @app.get("/v1/models/{model_id}/progress")
    async def model_progress(model_id: str):
        res = registry.progress(model_id)
        if not res.get("ok"):
            return _err(res.get("status", 404), res["error"])
        return _ok({k: v for k, v in res.items() if k != "ok"})

    @app.post("/v1/models/select")
    async def select_model(body: dict):
        model_id = body.get("model_id")
        if not model_id:
            return _err(400, "缺少 model_id")
        res = registry.select(model_id)
        if not res.get("ok"):
            return _err(res.get("status", 409), res["error"])
        return _ok({k: v for k, v in res.items() if k != "ok"})

    @app.post("/v1/models/download")
    async def download_model(body: dict):
        model_id = body.get("model_id")
        if not model_id:
            return _err(400, "缺少 model_id")
        res = registry.start_download(model_id)
        if not res.get("ok"):
            return _err(res.get("status", 409), res["error"])
        return _ok({k: v for k, v in res.items() if k != "ok"})

    @app.get("/v1/stream/parameters")
    async def stream_parameters():
        """转写参数建议：分块时长 + 缓冲（契约《参数推荐接口需求》§2.1）。"""
        return _ok(recommend_parameters(cfg))

    return app


async def serve_rest(cfg: dict):
    import uvicorn

    log.info(f"HTTP 网关监听 http://{cfg['http_host']}:{cfg['http_port']}")
    app = build_app(cfg)
    server = uvicorn.Server(uvicorn.Config(
        app,
        host=cfg["http_host"],
        port=cfg["http_port"],
        log_level="info",
    ))
    await server.serve()
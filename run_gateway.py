"""WhisperLive 适配网关启动入口。

同时启动：
  - WebSocket 流式转写   ws://127.0.0.1:47833/v1/stream/transcriptions
  - HTTP 模型管理        http://127.0.0.1:47834

端口沿用 E:\AI\local-voice\.env 分配约定（WHISPERLIVE_GW_WS_PORT=47833、WHISPERLIVE_GW_HTTP_PORT=47834）。
前置：先启动 WhisperLive 引擎（run_server.py --port 47831，即 WHISPERLIVE_WS_PORT）。

用法：
  python run_gateway.py
  # 自定义端口/上游
  GW_WS_PORT=47833 GW_HTTP_PORT=47834 GW_UPSTREAM_PORT=47831 python run_gateway.py
  # 或用 config.json 覆盖（见 gateway/config.py，默认值已对齐 .env）
"""
import asyncio
import logging
import sys

from gateway import config
from gateway.ws_gateway import serve_ws
from gateway.rest_api import serve_rest


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    cfg = config.load_config()
    log = logging.getLogger("gateway")

    log.info("=== WhisperLive 适配网关 ===")
    log.info(f"对外 WS : ws://{cfg['ws_host']}:{cfg['ws_port']}/v1/stream/transcriptions")
    log.info(f"对外 HTTP: http://{cfg['http_host']}:{cfg['http_port']}")
    log.info(f"对内引擎: ws://{cfg['upstream_host']}:{cfg['upstream_port']}")

    async def _run():
        await asyncio.gather(
            serve_ws(cfg),
            serve_rest(cfg),
        )

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        log.info("网关已停止")
    except Exception as e:  # noqa: BLE001
        log.error(f"网关异常退出: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
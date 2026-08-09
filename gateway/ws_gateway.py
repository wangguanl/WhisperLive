"""47833 WebSocket 流式转写适配：把 voxtral-app-api 契约映射到 WhisperLive 引擎。

对外（前端）：ws://127.0.0.1:47833/v1/stream/transcriptions
  - 控制消息（文本帧）：{"type":"start","lang":"zh"} / {"type":"config","lang":"en"} / {"type":"stop"}
  - 音频：二进制帧，16k/mono/s16le，建议 480ms = 7680 字节
  - 返回（文本帧）：session_started / partial / transcript / session_ended / error

对内（引擎）：连接 ws://127.0.0.1:47831（WHISPERLIVE_WS_PORT，见 E:\Pro2\.env）
  - 首帧 JSON options（uid/language/task/model/use_vad/audio_format=int16）
  - 音频：二进制帧
  - 引擎回 JSON：SERVER_READY / {"segments":[...]}（completed 标记定稿）
"""
import asyncio
import json
import logging
import uuid
from typing import Optional, Set

import websockets

from gateway import config
from gateway.models_registry import get_registry

log = logging.getLogger("gateway.ws")


class UpstreamSession:
    """一个前端连接对应的上游 WhisperLive 会话。"""

    def __init__(self, frontend_ws, cfg: dict, lang: str):
        self.frontend_ws = frontend_ws
        self.cfg = cfg
        self.lang = lang
        self.uid = str(uuid.uuid4())
        self.upstream_ws: Optional[websockets.WebSocketClientProtocol] = None
        self.seq = 0
        self._started = False
        self._recv_task: Optional[asyncio.Task] = None
        # 定稿文本累计（用于去重/排序）
        self._final_texts: list = []
        self._sent_final: set = set()  # 已发送过的定稿文本，避免重复

    # ---- 对外发送 ----
    async def send_json(self, obj: dict):
        try:
            await self.frontend_ws.send(json.dumps(obj, ensure_ascii=False))
        except Exception:  # noqa: BLE001
            pass

    def _next_seq(self) -> int:
        self.seq += 1
        return self.seq

    # ---- 上游连接 ----
    async def connect_upstream(self) -> bool:
        uri = f"ws://{self.cfg['upstream_host']}:{self.cfg['upstream_port']}"
        try:
            self.upstream_ws = await websockets.connect(uri, max_size=None)
            options = {
                "uid": self.uid,
                "language": self.lang,
                "task": "transcribe",
                "model": get_registry().current_engine_model(),
                "use_vad": self.cfg["use_vad"],
                "send_last_n_segments": self.cfg["send_last_n_segments"],
                "no_speech_thresh": self.cfg["no_speech_thresh"],
                "clip_audio": self.cfg["clip_audio"],
                "same_output_threshold": self.cfg["same_output_threshold"],
                "enable_translation": False,
                "audio_format": self.cfg["audio_format"],  # int16
            }
            await self.upstream_ws.send(json.dumps(options))
            self._started = True
            return True
        except Exception as e:  # noqa: BLE001
            log.error(f"连接上游失败: {e}")
            return False

    # ---- 上游接收循环 ----
    async def run_recv_loop(self):
        """不断读取上游消息，映射为契约消息回传前端。"""
        try:
            async for raw in self.upstream_ws:
                if not isinstance(raw, str):
                    # 上游理论上不回二进制，忽略
                    continue
                msg = json.loads(raw)
                await self.handle_upstream_message(msg)
        except websockets.exceptions.ConnectionClosed:
            log.info(f"[{self.uid}] 上游连接关闭")
        except Exception as e:  # noqa: BLE001
            log.error(f"[{self.uid}] 上游接收循环异常: {e}")
        finally:
            # 上游断开 → 通知前端会话结束
            await self.send_json({"type": "session_ended", "session_id": self.uid})

    async def handle_upstream_message(self, msg: dict):
        # 1) SERVER_READY → session_started
        if msg.get("message") == "SERVER_READY":
            await self.send_json({"type": "session_started", "session_id": self.uid})
            return
        # 2) 语言检测回显
        if "language" in msg:
            self.lang = msg.get("language", self.lang)
            return
        # 3) 转写 segments
        if "segments" in msg:
            segments = msg.get("segments", [])
            if not segments:
                return
            # 上游每次返回全部已定稿段 + 最后一个中间段
            # 遍历：定稿段去重后发 transcript；最后一个非定稿段发 partial
            for seg in segments:
                text = (seg.get("text") or "").strip()
                if not text:
                    continue
                is_final = bool(seg.get("completed", False))
                if is_final:
                    if text in self._sent_final:
                        continue  # 该定稿段已发送过，跳过
                    self._sent_final.add(text)
                    await self.send_json({
                        "type": "transcript",
                        "text": text,
                        "lang": self.lang,
                        "isFinal": True,
                        "seq": self._next_seq(),
                    })
                else:
                    await self.send_json({
                        "type": "partial",
                        "text": text,
                        "lang": self.lang,
                        "isFinal": False,
                        "seq": self._next_seq(),
                    })

    # ---- 发送音频 ----
    async def send_audio(self, audio_bytes: bytes):
        if self.upstream_ws and self._started:
            await self.upstream_ws.send(audio_bytes)

    # ---- 配置切换 ----
    async def reconfigure(self, lang: str):
        self.lang = lang
        # 引擎不支持会话中途改语言，这里简单记录；如需真正切换需重建上游会话。
        # 契约允许 config 切换，我们在下次 start 时生效并回显当前 lang。
        log.info(f"[{self.uid}] config lang -> {lang}")

    # ---- 停止 ----
    async def stop(self):
        if self.upstream_ws:
            try:
                await self.upstream_ws.close()
            except Exception:  # noqa: BLE001
                pass
        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()

    async def close(self):
        await self.stop()


class WsGateway:
    """对外 WebSocket 服务，处理一个前端连接的完整生命周期。"""

    def __init__(self, cfg: dict):
        self.cfg = cfg

    async def handle_connection(self, websocket):
        session: Optional[UpstreamSession] = None
        recv_task: Optional[asyncio.Task] = None
        try:
            async for raw in websocket:
                if isinstance(raw, bytes):
                    # 音频帧 → 转发上游
                    if session and session._started:
                        await session.send_audio(raw)
                    continue

                # 文本帧 → 控制消息
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await websocket.send(json.dumps({
                        "type": "error", "message": "invalid JSON control message",
                    }, ensure_ascii=False))
                    continue

                mtype = msg.get("type")
                if mtype == "start":
                    # 若已有会话则复位
                    if session:
                        await session.close()
                    lang = msg.get("lang") or self.cfg["default_lang"]
                    session = UpstreamSession(websocket, self.cfg, lang)
                    ok = await session.connect_upstream()
                    if not ok:
                        await websocket.send(json.dumps({
                            "type": "error", "message": "upstream engine unavailable",
                        }, ensure_ascii=False))
                        continue
                    recv_task = asyncio.create_task(session.run_recv_loop())
                    session._recv_task = recv_task

                elif mtype == "config":
                    if session:
                        await session.reconfigure(msg.get("lang") or self.cfg["default_lang"])

                elif mtype == "stop":
                    if session:
                        await session.stop()
                    # 契约：stop 后应发 session_ended
                    if session:
                        await websocket.send(json.dumps({
                            "type": "session_ended",
                            "session_id": session.uid,
                        }, ensure_ascii=False))
                    session = None
                    if recv_task:
                        recv_task.cancel()
                        recv_task = None
                    # 前端契约：stop 后由前端关闭连接，这里不主动 close
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            if session:
                await session.close()


async def serve_ws(cfg: dict):
    log.info(f"WS 网关监听 ws://{cfg['ws_host']}:{cfg['ws_port']}/v1/stream/transcriptions")
    gateway = WsGateway(cfg)
    async with websockets.serve(
        gateway.handle_connection,
        cfg["ws_host"],
        cfg["ws_port"],
        max_size=None,
        ping_interval=None,  # 契约约定：不启用应用层 keepalive
        ping_timeout=None,
    ) as server:
        await asyncio.Future()  # run forever
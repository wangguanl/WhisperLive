"""网关配置：端口、上游引擎地址、模型登记表。"""
import os
import json
from pathlib import Path

# ---- 默认配置（可用环境变量覆盖，也可用 config.json）----
CONFIG = {
    # 对外（前端契约）
    "ws_host": os.environ.get("GW_WS_HOST", "127.0.0.1"),
    "ws_port": int(os.environ.get("GW_WS_PORT", "47833")),
    "http_host": os.environ.get("GW_HTTP_HOST", "127.0.0.1"),
    "http_port": int(os.environ.get("GW_HTTP_PORT", "47834")),
    # 对内（WhisperLive 引擎）
    "upstream_host": os.environ.get("GW_UPSTREAM_HOST", "127.0.0.1"),
    "upstream_port": int(os.environ.get("GW_UPSTREAM_PORT", "47831")),
    # 默认转写参数
    "default_lang": "zh",
    "model": "small",
    "use_vad": True,
    "send_last_n_segments": 10,
    "no_speech_thresh": 0.45,
    "clip_audio": False,
    "same_output_threshold": 10,
    "audio_format": "int16",
}

# 契约语言码（Voxtral 语音码）
SUPPORTED_LANGS = [
    "zh", "en", "ja", "ko", "es", "fr", "de", "it", "nl", "pt", "hi", "ar", "ru",
]

# 模型登记表（数据驱动，可扩展）
# id: 前端引用用的逻辑 ID；repo_id/models 用于下载；local_dir 用于校验本地是否已存在
MODELS_REGISTRY = [
    {
        "id": "faster-whisper-small",
        "name": "Faster Whisper Small",
        "repo_id": "Systran/faster-whisper-small",
        "family": "asr",
        "description": "Faster Whisper Small，多语言，本地已下载，实时低延迟",
        "size_hint": "约 464 MB (int8)",
        "local_dir": r"E:\huggingface_cache\hub\models--Systran--faster-whisper-small",
        "engine_model": "small",  # 传给 WhisperLive 引擎的模型名
    }
]


def load_config(path: str = "config.json") -> dict:
    """若存在 config.json，则用其覆盖默认配置。"""
    cfg = dict(CONFIG)
    p = Path(path)
    if p.exists():
        try:
            user = json.loads(p.read_text(encoding="utf-8"))
            cfg.update({k: v for k, v in user.items() if k in cfg})
        except Exception as e:  # noqa: BLE001
            print(f"[gateway] 读取 {path} 失败，使用默认配置: {e}")
    return cfg


def get_registry() -> list:
    """深拷贝模型登记表，避免调用方修改全局。"""
    return [dict(m) for m in MODELS_REGISTRY]
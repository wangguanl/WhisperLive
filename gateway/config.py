"""网关配置：端口、上游引擎地址、模型登记表。"""
import os
import json
from pathlib import Path

# ---- 下载/推理环境（网关进程独立启动，需自行补齐与引擎一致的环境）----
os.environ.setdefault("HF_HOME", r"E:\huggingface_cache")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

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
# id: 前端引用用的逻辑 ID；repo_id: Hugging Face 仓库；engine_model: 传给 WhisperLive 引擎的模型名
# pros_cons: 模型优劣势一句话描述，供前端在模型列表展示
# 本地是否存在由 HF 缓存自动判定（见 models_registry._hf_cache_exists），无需手填 local_dir
_WHISPER_MODELS = [
    # (id, repo, engine_model, 名称, 大小, 优劣势)
    ("whisper-tiny", "Systran/faster-whisper-tiny", "tiny", "Whisper Tiny", "约 151 MB (float16)",
     "最快、占用极小，适合最简单低延迟场景；准确率低，长句和嘈杂环境效果差"),
    ("whisper-tiny-en", "Systran/faster-whisper-tiny.en", "tiny.en", "Whisper Tiny (English)", "约 151 MB (float16)",
     "英文最快最省；仅支持英文"),
    ("whisper-base", "Systran/faster-whisper-base", "base", "Whisper Base", "约 290 MB (float16)",
     "速度快、占用小，准确率明显优于 tiny；多语言仍一般，适合短句"),
    ("whisper-base-en", "Systran/faster-whisper-base.en", "base.en", "Whisper Base (English)", "约 290 MB (float16)",
     "英文快速且较准；仅支持英文"),
    ("whisper-small", "Systran/faster-whisper-small", "small", "Whisper Small", "约 970 MB (float16)",
     "速度与准确率均衡，多语言通用首选；准确率中等，专有名词和口音一般"),
    ("whisper-small-en", "Systran/faster-whisper-small.en", "small.en", "Whisper Small (English)", "约 970 MB (float16)",
     "英文速度与准确率均衡；仅支持英文"),
    ("whisper-medium", "Systran/faster-whisper-medium", "medium", "Whisper Medium", "约 3.07 GB (float16)",
     "准确率较高、多语言良好；体积大、实时性下降、显存占用高"),
    ("whisper-medium-en", "Systran/faster-whisper-medium.en", "medium.en", "Whisper Medium (English)", "约 3.07 GB (float16)",
     "英文准确率高；仅支持英文"),
    ("whisper-large-v2", "Systran/faster-whisper-large-v2", "large-v2", "Whisper Large v2", "约 6.17 GB (float16)",
     "准确率高、多语言优秀；推理慢、显存占用大，实时性弱"),
    ("whisper-large-v3", "Systran/faster-whisper-large-v3", "large-v3", "Whisper Large v3", "约 6.17 GB (float16)",
     "准确率最高、多语言最稳；最慢、显存占用最大"),
    ("whisper-large-v3-turbo", "Systran/faster-whisper-large-v3-turbo", "large-v3-turbo", "Whisper Large v3 Turbo", "约 1.6 GB (float16)",
     "准确率高且速度快，多语言优秀，实时性好；显存要求中等"),
    ("whisper-turbo", "Systran/faster-whisper-turbo", "turbo", "Whisper Turbo", "约 1.6 GB (float16)",
     "速度快、准确率接近 large，多语言好；显存要求中等"),
    ("whisper-distil-small-en", "Systran/faster-distil-whisper-small.en", "distil-small.en", "Distil Whisper Small", "约 484 MB (float16)",
     "轻量快速，英文蒸馏模型；仅支持英文，准确率略逊 small"),
    ("whisper-distil-medium-en", "Systran/faster-distil-whisper-medium.en", "distil-medium.en", "Distil Whisper Medium", "约 1.5 GB (float16)",
     "速度与准确率均衡，英文蒸馏；仅支持英文"),
    ("whisper-distil-large-v2", "Systran/faster-distil-whisper-large-v2", "distil-large-v2", "Distil Whisper Large v2", "约 3.1 GB (float16)",
     "大模型级英文准确率且更快；仅支持英文"),
    ("whisper-distil-large-v3", "Systran/faster-distil-whisper-large-v3", "distil-large-v3", "Distil Whisper Large v3", "约 3.1 GB (float16)",
     "大模型级英文准确率、速度与 v2 相当；仅支持英文"),
]

MODELS_REGISTRY = [
    {
        "id": mid,
        "name": name,
        "repo_id": repo,
        "family": "asr",
        "description": f"{name}，实时低延迟多语言转写",
        "size_hint": size_hint,
        "engine_model": engine_model,
        "pros_cons": pros_cons,
    }
    for (mid, repo, engine_model, name, size_hint, pros_cons) in _WHISPER_MODELS
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
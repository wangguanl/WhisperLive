"""模型推理实时率(RTF)测量。

网关为独立进程，未继承引擎的 GPU DLL 环境，故本模块在导入时自行注入
nvidia 的 cublas/curand/nvrtc DLL 到 PATH（与引擎 start_services.ps1 一致），
并在首次测速时用 faster_whisper 加载模型、跑一段固定时长音频推理，
以 耗时/音频时长 得出实时率 RTF。结果按模型缓存，避免重复加载。

RTF < 1 表示比实时快，越大越慢；据此可推导出分块/缓冲预设。
"""
import logging
import os
import threading
import time

log = logging.getLogger("gateway.rtfprobe")

# 与引擎一致：注入 nvidia DLL 目录到 PATH
_BASE = r"E:\AI\local-voice\WhisperLive\.venv\Lib\site-packages\nvidia"
for _d in ("cublas", "cuda_nvrtc", "curand"):
    _p = os.path.join(_BASE, _d, "bin")
    if os.path.isdir(_p) and _p not in os.environ.get("PATH", ""):
        os.environ["PATH"] = _p + os.pathsep + os.environ.get("PATH", "")

_AUDIO_SECS = 5.0
_SAMPLE_RATE = 16000

_CACHE: dict = {}
_LOCK = threading.Lock()


def _make_audio():
    import numpy as np

    rng = np.random.default_rng(0)
    t = np.arange(int(_AUDIO_SECS * _SAMPLE_RATE))
    # 固定时长占位音频：正弦 + 少量噪声，重点是时长与可复现性，而非内容
    audio = (0.3 * np.sin(2 * np.pi * 180 * t / _SAMPLE_RATE)
             + 0.02 * rng.standard_normal(len(t))).astype("float32")
    return audio


def measure_rtf(model_ref: str) -> dict | None:
    """对指定模型测一次实时率并缓存。失败返回 None。"""
    with _LOCK:
        if model_ref in _CACHE:
            return dict(_CACHE[model_ref])

    try:
        from faster_whisper import WhisperModel

        model = WhisperModel(model_ref, device="cuda", compute_type="float16")
        audio = _make_audio()
        t0 = time.time()
        segments, _ = model.transcribe(audio, beam_size=1)
        list(segments)  # 触发实际推理
        elapsed = time.time() - t0
        del model

        res = {
            "model": model_ref,
            "audio_secs": _AUDIO_SECS,
            "elapsed_secs": round(elapsed, 3),
            "rtf": round(elapsed / _AUDIO_SECS, 4),
        }
        with _LOCK:
            _CACHE[model_ref] = res
        log.info(f"RTF 测量完成 {model_ref}: rtf={res['rtf']}, 耗时 {elapsed:.2f}s")
        return dict(res)
    except Exception as e:  # noqa: BLE001
        log.error(f"RTF 测量失败 {model_ref}: {e}")
        return None


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()
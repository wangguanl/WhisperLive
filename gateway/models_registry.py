"""模型注册表：登记模型状态、下载进度、选中情况（数据驱动，可扩展）。"""
import logging
import os
import threading
import time
from typing import Optional

from gateway import config

log = logging.getLogger("gateway.registry")


def _hf_cache_exists(repo_id: str) -> bool:
    """按 HF 缓存判定模型是否已完整下载（存在 model.bin 即视为已下载）。"""
    try:
        from huggingface_hub import constants as hfc
    except Exception:  # noqa: BLE001
        return False
    cache_dir = os.path.join(hfc.HF_HUB_CACHE, "models--" + repo_id.replace("/", "--"))
    snap = os.path.join(cache_dir, "snapshots")
    if not os.path.isdir(snap):
        return False
    try:
        for d in os.listdir(snap):
            if os.path.isfile(os.path.join(snap, d, "model.bin")):
                return True
    except OSError:
        return False
    return False


def _tqdm_factory(callback):
    """把 snapshot_download 的进度条回调转发到 callback(done, total, desc)。"""
    try:
        from tqdm import tqdm
    except Exception:  # noqa: BLE001
        return None

    class _ProgressTqdm(tqdm):
        def update(self, n=1):
            super().update(n)
            try:
                callback(int(self.n), self.total, str(self.desc or ""))
            except Exception:  # noqa: BLE001
                pass

    return _ProgressTqdm


class ModelState:
    """单个模型的运行时状态。status: downloaded / not_downloaded / downloading / active。"""

    def __init__(self, meta: dict):
        self.meta = meta
        self.status = "downloaded" if self._exists_local() else "not_downloaded"
        self.download = None  # 下载进度信息（下载中时为 dict）

    def _exists_local(self) -> bool:
        return _hf_cache_exists(self.meta.get("repo_id", ""))

    def refresh_status(self) -> None:
        """按本地目录重新评估状态（下载中除外）。

        状态在 __init__ 时只计算一次；若网关先启动、模型后下载完成，
        仅靠启动瞬间会误判为 not_downloaded。查询/选中时实时重检可避免。
        """
        if self.status != "downloading":
            self.status = "downloaded" if self._exists_local() else "not_downloaded"

    def to_dict(self) -> dict:
        self.refresh_status()
        d = {
            "id": self.meta["id"],
            "name": self.meta.get("name", ""),
            "repo_id": self.meta.get("repo_id"),
            "family": self.meta.get("family", "asr"),
            "size_hint": self.meta.get("size_hint"),
            "pros_cons": self.meta.get("pros_cons", ""),
            "status": self.status,
        }
        if self.status == "downloading" and self.download:
            d["download"] = self.download
        return d


class ModelsRegistry:
    """管理全部登记模型 + 当前选中模型 + 下载活动。"""

    def __init__(self, registry: Optional[list] = None):
        self._lock = threading.Lock()
        self._models = [ModelState(m) for m in (registry or config.get_registry())]
        self._current_id: Optional[str] = None
        # 若无任何已下载模型，则默认选中第一个已下载的
        for m in self._models:
            if m.status == "downloaded":
                self._current_id = m.meta["id"]
                break

    # ---- 查询 ----
    def get(self, model_id: str) -> Optional[ModelState]:
        for m in self._models:
            if m.meta["id"] == model_id:
                return m
        return None

    def list_models(self) -> list:
        with self._lock:
            return [m.to_dict() for m in self._models]

    def current(self) -> Optional[str]:
        with self._lock:
            return self._current_id

    def current_model_meta(self) -> Optional[dict]:
        with self._lock:
            if not self._current_id:
                return None
            m = self.get(self._current_id)
            return m.meta if m else None

    def current_engine_model(self) -> str:
        """返回当前选中模型对应的引擎模型名；无选中时用默认。"""
        meta = self.current_model_meta()
        if meta:
            return meta.get("engine_model", config.CONFIG["model"])
        return config.CONFIG["model"]

    # ---- 选中 ----
    def select(self, model_id: str) -> dict:
        """选中模型。返回前端契约的响应。"""
        with self._lock:
            m = self.get(model_id)
            if m is None:
                return {"ok": False, "error": f"未知模型: {model_id}", "status": 404}
            m.refresh_status()  # 实时重检，避免启动瞬间误判
            if m.status == "not_downloaded":
                return {"ok": False, "error": f"模型未下载，请先下载: {model_id}", "status": 409}
            if m.status == "downloading":
                return {"ok": False, "error": f"模型正在下载中: {model_id}", "status": 409}
            changed = self._current_id != model_id
            self._current_id = model_id
            return {
                "ok": True,
                "id": model_id,
                "status": "active",
                "changed": changed,
            }

    def current_downloaded(self) -> bool:
        with self._lock:
            m = self.get(self._current_id) if self._current_id else None
            if m is None:
                return False
            m.refresh_status()
            return m.status == "downloaded"

    def current_status(self) -> Optional[str]:
        with self._lock:
            m = self.get(self._current_id) if self._current_id else None
            if m is None:
                return None
            m.refresh_status()
            return m.status

    # ---- 下载（真实 huggingface_hub 下载，进度实时回写）----
    def start_download(self, model_id: str) -> dict:
        with self._lock:
            m = self.get(model_id)
            if m is None:
                return {"ok": False, "error": f"未知模型: {model_id}", "status": 404}
            if m.status == "downloaded":
                return {"ok": True, "id": model_id, "status": "downloaded", "downloaded": True}
            if m.status == "downloading":
                return {"ok": False, "error": f"模型已在下载中: {model_id}", "status": 409}

            m.status = "downloading"
            m.download = {
                "model_id": model_id,
                "name": m.meta.get("name", ""),
                "size_hint": m.meta.get("size_hint"),
                "status": "downloading",
                "total_bytes": None,
                "downloaded_bytes": 0,
                "progress": 0.0,
                "rate_bps": 0,
                "eta_seconds": None,
                "current_file": "waiting...",
                "elapsed_seconds": 0.0,
            }
        threading.Thread(target=self._download_worker, args=(m,), daemon=True).start()
        return {"ok": True, "id": model_id, "status": "downloading", "downloaded": False}

    def _download_worker(self, m: ModelState) -> None:
        """真实 HF 下载：snapshot_download 拉取进度，实时回写速度/大小/进度。"""
        repo_id = m.meta.get("repo_id", "")
        start = time.time()
        try:
            from huggingface_hub import HfApi, snapshot_download
        except Exception as e:  # noqa: BLE001
            log.error(f"下载 {repo_id} 失败: huggingface_hub 不可用: {e}")
            self._download_finish(m, False)
            return

        # 预估总大小：model_info 的 siblings 含各文件 size
        total = None
        try:
            info = HfApi().model_info(repo_id, files_metadata=True)
            sizes = [getattr(s, "size", None) for s in (info.siblings or [])]
            sizes = [s for s in sizes if s]
            if sizes:
                total = int(sum(sizes))
        except Exception as e:  # noqa: BLE001
            log.warning(f"获取 {repo_id} 体积失败: {e}")
        if total:
            m.download["total_bytes"] = total

        def _progress(done: int, total_: Optional[int], desc: str):
            elapsed = max(time.time() - start, 1e-6)
            tot = total_ or total
            if tot:
                m.download["total_bytes"] = int(tot)
                m.download["downloaded_bytes"] = int(done)
                m.download["progress"] = min(1.0, done / tot)
            m.download["elapsed_seconds"] = round(elapsed, 2)
            rate = done / elapsed
            m.download["rate_bps"] = int(rate)
            if tot and rate > 0:
                m.download["eta_seconds"] = int(max(0, tot - done) / rate)
            if desc:
                m.download["current_file"] = desc

        try:
            snapshot_download(repo_id=repo_id, tqdm_class=_tqdm_factory(_progress))
            self._download_finish(m, True)
        except Exception as e:  # noqa: BLE001
            log.error(f"下载 {repo_id} 失败: {e}")
            self._download_finish(m, False)

    def _download_finish(self, m: ModelState, ok: bool) -> None:
        with self._lock:
            if not ok:
                m.status, m.download = "not_downloaded", None
                return
            m.status, m.download = "downloaded", None
            if self._current_id is None:
                self._current_id = m.meta["id"]

    def progress(self, model_id: str) -> dict:
        with self._lock:
            m = self.get(model_id)
            if m is None:
                return {"ok": False, "error": f"未知模型: {model_id}", "status": 404}
            if m.status == "downloading" and m.download:
                body = dict(m.download)
            else:
                body = {
                    "model_id": model_id,
                    "name": m.meta.get("name", ""),
                    "size_hint": m.meta.get("size_hint"),
                    "status": m.status,
                    "total_bytes": None,
                    "downloaded_bytes": None,
                    "progress": 1.0 if m.status == "downloaded" else 0.0,
                    "rate_bps": 0,
                    "eta_seconds": None,
                    "current_file": None,
                    "elapsed_seconds": 0.0,
                }
            return {"ok": True, **body}


_registry_instance: Optional[ModelsRegistry] = None
_registry_lock = threading.Lock()


def get_registry() -> ModelsRegistry:
    global _registry_instance
    with _registry_lock:
        if _registry_instance is None:
            _registry_instance = ModelsRegistry()
        return _registry_instance
"""模型注册表：登记模型状态、下载进度、选中情况（数据驱动，可扩展）。"""
import threading
import time
from typing import Optional

from gateway import config


class ModelState:
    """单个模型的运行时状态。status: downloaded / not_downloaded / downloading / active。"""

    def __init__(self, meta: dict):
        self.meta = meta
        self.status = "downloaded" if self._exists_local() else "not_downloaded"
        self.download = None  # 下载进度信息（下载中时为 dict）

    def _exists_local(self) -> bool:
        local = self.meta.get("local_dir")
        if not local:
            return False
        p = __import__("pathlib").Path(local)
        return p.exists()

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

    # ---- 下载（模拟进度；真实下载如需可接 huggingface_hub）----
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
                "current_file": "downloading...",
                "elapsed_seconds": 0.0,
            }
            # 启动后台线程模拟下载（本地包则瞬间完成）
            def _do():
                tick = 0.0
                while True:
                    time.sleep(0.3)
                    tick += 0.3
                    with self._lock:
                        if m.status != "downloading":
                            return
                        p = min(1.0, tick / 3.0)
                        m.download["progress"] = p
                        m.download["downloaded_bytes"] = int(p * 100)
                        m.download["elapsed_seconds"] = tick
                        m.download["rate_bps"] = int(100 / 3.0)
                        m.download["eta_seconds"] = max(0, int((1 - p) * 3.0))
                        if p >= 1.0:
                            m.status = "downloaded"
                            m.download = None
                            if self._current_id is None:
                                self._current_id = model_id
                            return

            threading.Thread(target=_do, daemon=True).start()
            return {"ok": True, "id": model_id, "status": "downloading", "downloaded": False}

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
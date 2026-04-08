# -*- coding: utf-8 -*-
"""
前端文件监听器 — 开发模式下，前端文件变更时通过 WebSocket 通知浏览器刷新
"""
import asyncio
import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger("catown.filewatcher")


class FileWatcher:
    """监听前端文件变更，触发浏览器热重载"""

    def __init__(self, watch_dir: str = None, interval: float = 1.0):
        if watch_dir is None:
            # 自动定位项目根目录下的 frontend/
            # main.py 在 backend/ 下运行，所以先试 ../frontend
            for candidate in [
                Path("../frontend"),
                Path("frontend"),
                Path("../../frontend"),
            ]:
                if candidate.exists():
                    watch_dir = str(candidate.resolve())
                    break
            else:
                watch_dir = "../frontend"  # fallback
        self.watch_dir = Path(watch_dir)
        self.interval = interval
        self._mtimes: dict[str, float] = {}
        self._running = False
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def _scan(self) -> list[str]:
        """扫描目录，返回变更文件列表"""
        changed = []
        if not self.watch_dir.exists():
            return changed

        for root, _dirs, files in os.walk(self.watch_dir):
            for fname in files:
                fpath = os.path.join(root, fname)
                try:
                    mtime = os.path.getmtime(fpath)
                except OSError:
                    continue

                old = self._mtimes.get(fpath)
                if old is None:
                    # 首次扫描，只记录不触发
                    self._mtimes[fpath] = mtime
                elif mtime != old:
                    self._mtimes[fpath] = mtime
                    changed.append(fpath)

        return changed

    def _run(self):
        """后台线程主循环"""
        # 首次扫描初始化 mtime
        self._scan()

        while self._running:
            import time
            time.sleep(self.interval)
            changed = self._scan()
            if changed and self._loop and not self._loop.is_closed():
                for f in changed:
                    logger.info(f"[FileWatcher] Changed: {f}")
                # 在事件循环中调度广播
                asyncio.run_coroutine_threadsafe(
                    self._broadcast_reload(changed),
                    self._loop
                )

    async def _broadcast_reload(self, changed_files: list[str]):
        """通过 WebSocket 广播 reload 事件"""
        try:
            from routes.websocket import websocket_manager
            await websocket_manager.broadcast({
                "type": "reload",
                "files": [os.path.basename(f) for f in changed_files]
            })
            logger.info(f"[FileWatcher] Broadcast reload to {len(websocket_manager.active_connections)} clients")
        except Exception as e:
            logger.error(f"[FileWatcher] Broadcast failed: {e}")

    def start(self, loop: asyncio.AbstractEventLoop):
        """启动文件监听（在 FastAPI 启动时调用）"""
        if not self.watch_dir.exists():
            logger.warning(f"[FileWatcher] Watch dir not found: {self.watch_dir}")
            return

        self._loop = loop
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="FileWatcher")
        self._thread.start()
        logger.info(f"[FileWatcher] Watching {self.watch_dir} (interval={self.interval}s)")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)


# 全局实例
file_watcher = FileWatcher()

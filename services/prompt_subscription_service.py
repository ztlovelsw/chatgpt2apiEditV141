from __future__ import annotations

import time
import uuid
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any, Callable
from urllib.parse import urlsplit

from fastapi import HTTPException

from services.library_service import LIBRARY_DIR, PromptLibraryService, _items_from_file, _write_json, prompt_library_service
from services.prompt_import_service import fetch_prompt_subscription, normalize_subscription_url

SUBSCRIPTIONS_FILE = LIBRARY_DIR / "prompt_subscriptions.json"


class PromptSubscriptionService:
    def __init__(
        self, path: Path = SUBSCRIPTIONS_FILE, library: PromptLibraryService = prompt_library_service,
        *, loader: Callable[[str], dict[str, Any]] = fetch_prompt_subscription,
        clock: Callable[[], float] = time.time, poll_seconds: float = 30,
    ):
        self.path = path
        self.library = library
        self.loader = loader
        self.clock = clock
        self.poll_seconds = poll_seconds
        self._lock = Lock()
        self._running: set[str] = set()
        self._stop = Event()
        self._wake = Event()
        self._thread: Thread | None = None

    def _find(self, items: list[dict[str, Any]], item_id: str) -> dict[str, Any]:
        for item in items:
            if item["id"] == item_id:
                return item
        raise HTTPException(status_code=404, detail={"error": "订阅不存在"})

    def _assert_idle(self, item_id: str) -> None:
        if item_id in self._running:
            raise HTTPException(status_code=409, detail={"error": "该订阅正在同步，请完成后再修改"})

    def list_items(self) -> dict[str, Any]:
        with self._lock:
            items = _items_from_file(self.path)
            return {"items": [{**item, "status": "syncing" if item["id"] in self._running else
                               "queued" if item["pending"] else "error" if item["last_error"] else
                               "success" if item["last_success_at"] else "idle"} for item in items]}

    def create_item(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = normalize_subscription_url(payload["url"])
        now = self.clock()
        enabled = payload.get("enabled", True)
        item = {
            "id": uuid.uuid4().hex, "name": (payload.get("name", "").strip() or urlsplit(url).hostname)[:120],
            "url": url, "enabled": enabled, "interval_minutes": payload.get("interval_minutes", 720),
            "created_at": now, "updated_at": now, "last_sync_at": None, "last_success_at": None,
            "next_sync_at": now if enabled else None, "pending": enabled, "last_error": "",
            "last_result": None, "warnings": [],
        }
        with self._lock:
            items = _items_from_file(self.path)
            if any(existing["url"] == url for existing in items):
                raise HTTPException(status_code=409, detail={"error": "该链接已订阅"})
            _write_json(self.path, {"items": [*items, item]})
        self._wake.set()
        return {"item": item, **self.list_items()}

    def update_item(self, item_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._assert_idle(item_id)
            items = _items_from_file(self.path)
            item = self._find(items, item_id)
            now = self.clock()
            if "name" in payload:
                item["name"] = payload["name"].strip()[:120] or urlsplit(item["url"]).hostname
            if "interval_minutes" in payload:
                item["interval_minutes"] = payload["interval_minutes"]
                if item["enabled"]:
                    item["next_sync_at"] = now + item["interval_minutes"] * 60
            if "enabled" in payload:
                if payload["enabled"] and not item["enabled"]:
                    item["next_sync_at"] = now
                item["enabled"] = payload["enabled"]
                if not item["enabled"]:
                    item["next_sync_at"] = None
                    item["pending"] = False
            item["updated_at"] = now
            _write_json(self.path, {"items": items})
        self._wake.set()
        return {"item": item, **self.list_items()}

    def delete_item(self, item_id: str) -> dict[str, Any]:
        with self._lock:
            self._assert_idle(item_id)
            items = _items_from_file(self.path)
            self._find(items, item_id)
            self.library.detach_subscription(item_id)
            _write_json(self.path, {"items": [item for item in items if item["id"] != item_id]})
        return self.list_items()

    def request_sync(self, item_id: str) -> dict[str, Any]:
        with self._lock:
            items = _items_from_file(self.path)
            item = self._find(items, item_id)
            if item_id not in self._running:
                item["pending"] = True
                _write_json(self.path, {"items": items})
        self._wake.set()
        return self.list_items()

    def _due(self, item: dict[str, Any]) -> bool:
        return item["pending"] or (item["enabled"] and item["next_sync_at"] <= self.clock())

    def _sync_one(self, item_id: str) -> None:
        with self._lock:
            items = _items_from_file(self.path)
            item = next((item for item in items if item["id"] == item_id), None)
            if item is None or item_id in self._running or not self._due(item):
                return
            self._running.add(item_id)
            url = item["url"]
        error = ""
        result = None
        warnings: list[str] = []
        try:
            snapshot = self.loader(url)
            if not snapshot["items"]:
                raise ValueError("来源未返回提示词，本地内容已保留")
            warnings = snapshot.get("warnings", [])
            if not self._stop.is_set():
                result = self.library.sync_subscription(item_id, snapshot["items"])
        except Exception as exc:
            if isinstance(exc, HTTPException):
                error = str(exc.detail.get("error") if isinstance(exc.detail, dict) else exc.detail)
            else:
                error = str(exc) or "同步失败，请稍后重试"
        finally:
            with self._lock:
                try:
                    # Keep a queued request due when shutdown interrupts a download.
                    if not self._stop.is_set():
                        items = _items_from_file(self.path)
                        item = self._find(items, item_id)
                        now = self.clock()
                        item.update({"last_sync_at": now, "last_error": error[:500], "warnings": warnings,
                                     "pending": False, "next_sync_at": now + item["interval_minutes"] * 60 if item["enabled"] else None})
                        if result is not None:
                            item.update({"last_success_at": now, "last_result": result})
                        _write_json(self.path, {"items": items})
                finally:
                    self._running.discard(item_id)

    def run_due(self) -> None:
        with self._lock:
            due_ids = [item["id"] for item in _items_from_file(self.path) if self._due(item)]
        for item_id in due_ids:
            if self._stop.is_set():
                break
            self._sync_one(item_id)

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.clear()
            try:
                self.run_due()
            except Exception as exc:
                print(f"[prompt-subscriptions] scheduler error: {exc}")
            self._wake.wait(self.poll_seconds)

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = Thread(target=self._run, name="prompt-subscription-scheduler", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=2)


prompt_subscription_service = PromptSubscriptionService()

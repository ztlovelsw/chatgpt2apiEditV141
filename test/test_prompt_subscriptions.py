from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api import libraries
from services import prompt_import_service as importer
from services.library_service import PromptLibraryService
from services.prompt_subscription_service import PromptSubscriptionService


def draft(number: int, content: str = "A cat") -> dict:
    return {"source_id": str(number), "name": f"Prompt {number}", "type": "摄影", "content": content,
            "source_url": f"https://example.com/prompts/{number}", "example_images": [f"https://example.com/{number}.png"]}


class PromptSubscriptionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.library = PromptLibraryService(self.root / "prompts.json")
        self.clock = mock.Mock(return_value=1000.0)
        self.loader = mock.Mock(return_value={"items": [draft(1)], "warnings": []})
        self.service = PromptSubscriptionService(self.root / "subscriptions.json", self.library,
                                                 loader=self.loader, clock=self.clock, poll_seconds=0.02)
        self.addCleanup(self.service.stop)

    def add(self, url="https://example.com/prompts.json", **kwargs):
        return self.service.create_item({"url": url, "name": "Example", "interval_minutes": 5, **kwargs})["item"]

    def test_first_sync_updates_content_and_images_without_duplicating(self):
        subscription = self.add()
        self.service.run_due()
        original = self.library.list_items()["items"][0]
        self.loader.return_value = {"items": [{**draft(1, "Updated cat"), "example_images": ["https://example.com/new.png"]}, draft(2)]}
        self.service.request_sync(subscription["id"])
        self.service.run_due()
        items = self.library.list_items()["items"]
        self.assertEqual(len(items), 2)
        updated = next(item for item in items if item["id"] == original["id"])
        self.assertEqual(updated["content"], "Updated cat")
        self.assertEqual(updated["example_images"], ["https://example.com/new.png"])
        self.assertEqual(updated["created_at"], original["created_at"])
        self.assertEqual(self.service.list_items()["items"][0]["last_result"], {"total": 2, "added": 1, "updated": 1, "unchanged": 0, "skipped": 0})
        self.service.request_sync(subscription["id"])
        self.service.run_due()
        self.assertEqual(self.service.list_items()["items"][0]["last_result"]["unchanged"], 2)
        self.assertEqual(len(self.library.list_items()["items"]), 2)

    def test_due_time_and_restart_preserve_schedule(self):
        self.add()
        self.service.run_due()
        self.assertEqual(self.service.list_items()["items"][0]["next_sync_at"], 1300)
        restarted = PromptSubscriptionService(self.service.path, self.library, loader=self.loader, clock=self.clock)
        self.clock.return_value = 1299
        restarted.run_due()
        self.assertEqual(self.loader.call_count, 1)
        self.clock.return_value = 1300
        restarted.run_due()
        self.assertEqual(self.loader.call_count, 2)
        self.assertEqual(restarted.list_items()["items"][0]["next_sync_at"], 1600)

    def test_pause_manual_sync_resume_and_interval_changes(self):
        item = self.add(enabled=False)
        self.service.run_due()
        self.loader.assert_not_called()
        self.service.request_sync(item["id"])
        self.service.run_due()
        self.assertEqual(self.loader.call_count, 1)
        self.assertIsNone(self.service.list_items()["items"][0]["next_sync_at"])
        self.service.update_item(item["id"], {"enabled": True})
        self.service.run_due()
        self.assertEqual(self.loader.call_count, 2)
        self.service.update_item(item["id"], {"interval_minutes": 60, "name": "Hourly"})
        self.assertEqual(self.service.list_items()["items"][0]["next_sync_at"], 4600)
        self.service.update_item(item["id"], {"enabled": False})
        self.clock.return_value = 9000
        self.service.run_due()
        self.assertEqual(self.loader.call_count, 2)

    def test_multiple_sources_are_isolated_and_failure_is_retryable(self):
        first = self.add("https://example.com/one.json")
        second = self.add("https://example.com/two.json")
        self.loader.side_effect = [HTTPException(502, {"error": "upstream unavailable"}), {"items": [draft(1)]}]
        self.service.run_due()
        states = {item["id"]: item for item in self.service.list_items()["items"]}
        self.assertEqual(states[first["id"]]["status"], "error")
        self.assertEqual(states[first["id"]]["last_error"], "upstream unavailable")
        self.assertEqual(states[second["id"]]["status"], "success")
        self.assertEqual(states[first["id"]]["next_sync_at"], 1300)
        self.loader.side_effect = None
        self.service.request_sync(first["id"])
        self.service.run_due()
        self.assertEqual(len(self.library.list_items()["items"]), 2)

    def test_failed_or_invalid_snapshot_does_not_partially_write(self):
        item = self.add()
        self.service.run_due()
        before = self.library.path.read_bytes()
        for result in ({"items": []}, {"items": [draft(2), {**draft(3), "example_images": ["javascript:alert(1)"]}]}):
            self.loader.return_value = result
            self.service.request_sync(item["id"])
            self.service.run_due()
            self.assertEqual(self.library.path.read_bytes(), before)
            self.assertEqual(self.service.list_items()["items"][0]["status"], "error")
            self.assertEqual(self.service.list_items()["items"][0]["last_success_at"], 1000)

    def test_local_edits_deletions_and_manual_items_survive_sync(self):
        item = self.add()
        self.loader.return_value = {"items": [draft(1), draft(2)]}
        self.service.run_due()
        first, second = self.library.list_items()["items"]
        self.library.update_item(first["id"], {"content": "My custom cat"})
        self.library.delete_item(second["id"])
        manual = self.library.create_item({"name": "Manual", "content": "My manual prompt"})["item"]
        self.library.update_item(manual["id"], {"note": "Keep me"})
        self.loader.return_value = {"items": [draft(1, "New source cat"), draft(2, "New second"), draft(3)]}
        self.service.request_sync(item["id"])
        self.service.run_due()
        items = self.library.list_items()["items"]
        self.assertEqual(len(items), 3)
        self.assertEqual(next(row for row in items if row["id"] == first["id"])["content"], "My custom cat")
        self.assertEqual(self.service.list_items()["items"][0]["last_result"]["skipped"], 2)
        self.assertTrue(any(row["id"] == manual["id"] for row in items))
        restarted = PromptLibraryService(self.library.path)
        self.assertEqual(restarted.sync_subscription(item["id"], [draft(2)])["skipped"], 1)

    def test_source_removal_and_unsubscribe_keep_downloaded_content(self):
        item = self.add()
        self.service.run_due()
        self.loader.return_value = {"items": [draft(2)]}
        self.service.request_sync(item["id"])
        self.service.run_due()
        self.assertEqual(len(self.library.list_items()["items"]), 2)
        self.service.delete_item(item["id"])
        self.assertEqual(self.service.list_items()["items"], [])
        self.assertTrue(all("subscription_id" not in row for row in self.library.list_items()["items"]))
        self.assertEqual(len(self.library.list_items()["items"]), 2)
        self.clock.return_value = 9000
        self.service.run_due()
        self.assertEqual(self.loader.call_count, 2)

    def test_same_name_records_with_distinct_source_ids_stay_separate(self):
        self.add()
        self.loader.return_value = {"items": [{**draft(number), "name": "Untitled"} for number in (1, 2)]}
        self.service.run_due()
        self.assertEqual(len(self.library.list_items()["items"]), 2)

    def test_generic_named_records_update_without_source_id(self):
        item = draft(1)
        item.pop("source_id")
        self.library.sync_subscription("source", [item])
        result = self.library.sync_subscription("source", [{**item, "content": "A new cat"}])
        self.assertEqual((result["updated"], result["added"]), (1, 0))

    def test_repository_aliases_reject_duplicate_subscription(self):
        self.add(importer.SHUIXIAN_REPOSITORY)
        with self.assertRaises(HTTPException) as caught:
            self.add("https://prompt.qqsrc.com/")
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(len(self.service.list_items()["items"]), 1)

    def test_concurrent_sync_is_coalesced_and_mutations_wait_until_idle(self):
        item = self.add()
        entered, release = threading.Event(), threading.Event()
        def blocked(_url):
            entered.set()
            release.wait(timeout=3)
            return {"items": [draft(1)]}
        self.loader.side_effect = blocked
        worker = threading.Thread(target=self.service.run_due)
        worker.start()
        try:
            self.assertTrue(entered.wait(timeout=2))
            self.assertEqual(self.service.list_items()["items"][0]["status"], "syncing")
            self.service.request_sync(item["id"])
            self.service.run_due()
            for operation in (lambda: self.service.delete_item(item["id"]), lambda: self.service.update_item(item["id"], {"enabled": False})):
                with self.assertRaises(HTTPException) as caught:
                    operation()
                self.assertEqual(caught.exception.status_code, 409)
        finally:
            release.set()
            worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.loader.call_count, 1)
        self.assertFalse(self.service.list_items()["items"][0]["pending"])

    def test_background_worker_fetches_without_a_browser_and_stops(self):
        self.service.start()
        self.service.start()
        self.add()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if self.service.list_items()["items"][0]["status"] == "success":
                break
            time.sleep(0.02)
        self.assertEqual(len(self.library.list_items()["items"]), 1)
        self.service.stop()
        self.assertFalse(self.service._thread.is_alive())


class SubscriptionSourceTests(unittest.TestCase):
    def test_unnamed_text_and_json_keep_identity_when_content_changes(self):
        for mime, before, after in (("text/plain", "A cat in the sun", "A dog in the moonlight"),
                                    ("application/json", '{"prompt":"A cat"}', '{"prompt":"A dog"}')):
            with self.subTest(mime=mime):
                first = importer.parse_prompt_document(before, mime, "https://example.com/feed", "https://example.com/feed")
                second = importer.parse_prompt_document(after, mime, "https://example.com/feed", "https://example.com/feed")
                self.assertEqual(first["items"][0]["source_id"], second["items"][0]["source_id"])

    def test_json_subscription_is_not_limited_to_preview_page(self):
        records = [{"id": number, "title": f"Record {number}", "prompt": "Shared text"} for number in range(250)]
        url = "https://example.com/all.json"
        with mock.patch.object(importer, "_download_document", return_value=(json.dumps(records), "application/json", url)):
            result = importer.fetch_prompt_subscription(url)
        self.assertEqual(len(result["items"]), 250)
        self.assertEqual(result["items"][-1]["source_id"], "249")

    def test_oversized_subscription_is_rejected_instead_of_truncated(self):
        url = "https://example.com/all.json"
        with mock.patch.object(importer, "MAX_SUBSCRIPTION_ITEMS", 2):
            for count in (2, 3):
                records = [{"id": index, "prompt": "A cat"} for index in range(count)]
                with mock.patch.object(importer, "_download_document", return_value=(json.dumps(records), "application/json", url)):
                    if count == 2:
                        self.assertEqual(len(importer.fetch_prompt_subscription(url)["items"]), 2)
                    else:
                        with self.assertRaises(HTTPException):
                            importer.fetch_prompt_subscription(url)

    def test_shuixian_subscription_refreshes_all_partitions(self):
        with mock.patch.object(importer, "_load_shuixian_part", side_effect=lambda part, **kwargs: [draft(part)]) as load:
            result = importer.fetch_prompt_subscription(importer.SHUIXIAN_REPOSITORY)
        self.assertEqual(len(result["items"]), 3)
        self.assertEqual(load.call_args_list, [mock.call(1, refresh=True), mock.call(2, refresh=True), mock.call(3, refresh=True)])


class SubscriptionApiTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.service = PromptSubscriptionService(root / "subscriptions.json", PromptLibraryService(root / "prompts.json"))
        self.patches = [mock.patch.object(libraries, "prompt_subscription_service", self.service),
                        mock.patch("api.support._legacy_admin_identity", side_effect=lambda token: {"role": "user"} if token == "subscription-test" else None),
                        mock.patch("api.support.auth_service.authenticate", return_value=None)]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)
        app = FastAPI()
        app.include_router(libraries.create_router())
        self.client = TestClient(app)
        self.headers = {"Authorization": "Bearer subscription-test"}

    def test_subscription_routes_require_login(self):
        item = self.service.create_item({"url": "https://example.com/all.json", "enabled": False})["item"]
        base = "/api/prompt-subscriptions"
        for method, path, body in (("GET", base, None), ("POST", base, {"url": "https://example.com/a.json"}),
                                   ("POST", f"{base}/{item['id']}", {"enabled": True}),
                                   ("POST", f"{base}/{item['id']}/sync", None), ("DELETE", f"{base}/{item['id']}", None)):
            with self.subTest(path=path):
                response = self.client.request(method, path, json=body)
                self.assertEqual(response.status_code, 401)

    def test_create_update_queue_delete_and_validation(self):
        base = "/api/prompt-subscriptions"
        created = self.client.post(base, headers=self.headers, json={"name": "Source", "url": "https://example.com/all.json"})
        self.assertEqual(created.status_code, 200)
        item = created.json()["items"][0]
        self.assertEqual((item["interval_minutes"], item["status"]), (720, "queued"))
        path = f"{base}/{item['id']}"
        response = self.client.post(path, headers=self.headers, json={"enabled": False})
        self.assertFalse(response.json()["items"][0]["enabled"])
        queued = self.client.post(path + "/sync", headers=self.headers)
        self.assertEqual(queued.json()["items"][0]["status"], "queued")
        for interval in (0, 4, 10081):
            self.assertEqual(self.client.post(path, headers=self.headers, json={"interval_minutes": interval}).status_code, 422)
        self.assertEqual(self.client.post(base, headers=self.headers, json={"url": "file:///secret"}).status_code, 400)
        deleted = self.client.delete(path, headers=self.headers)
        self.assertEqual(deleted.json()["items"], [])
        self.assertEqual(self.client.post(path + "/sync", headers=self.headers).status_code, 404)

    def test_app_lifespan_starts_and_stops_subscription_scheduler(self):
        from api import app as app_module
        with (mock.patch.object(app_module, "start_limited_account_watcher"),
              mock.patch.object(app_module, "start_image_cleanup_scheduler"),
              mock.patch.object(app_module, "backup_service"),
              mock.patch.object(app_module.config, "cleanup_old_images"),
              mock.patch.object(app_module, "prompt_subscription_service") as scheduler):
            with TestClient(app_module.create_app()):
                scheduler.start.assert_called_once()
            scheduler.stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()

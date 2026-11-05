from __future__ import annotations

import unittest
from unittest import mock

from services.openai_backend_api import OpenAIBackendAPI
from services.protocol import openai_v1_models as models


def model_result(*ids):
    return {"object": "list", "data": [models._model_entry(i, availability="account") for i in ids]}


class ModelListTests(unittest.TestCase):
    def setUp(self):
        models._clear_model_cache()
        self.accounts = [{"access_token": "token-a", "source_type": "web", "type": "free"}]
        self.account_patch = mock.patch.object(models.account_service, "list_accounts", side_effect=lambda: self.accounts)
        self.catalog_patch = mock.patch.object(models, "get_image_models", return_value={"gpt-image-2"})
        self.account_patch.start()
        self.catalog_patch.start()
        self.addCleanup(self.account_patch.stop)
        self.addCleanup(self.catalog_patch.stop)

    def test_unknown_future_models_are_returned_without_inventing_chat_models(self):
        backend = mock.Mock()
        backend.list_models.return_value = model_result("auto", "future-model-987")
        with mock.patch.object(models, "OpenAIBackendAPI", return_value=backend):
            ids = {m["id"] for m in models.list_models()["data"]}
        self.assertIn("future-model-987", ids)
        self.assertNotIn("gpt-5-6-sol", ids)
        self.assertNotIn("gptfree", ids)
        backend.close.assert_called_once()

    def test_union_of_accounts_is_deduplicated_and_disabled_accounts_are_skipped(self):
        self.accounts += [
            {"access_token": "token-b", "type": "Plus"},
            {"access_token": "disabled", "status": "禁用"},
        ]
        backends = {token: mock.Mock() for token in ("token-a", "token-b")}
        backends["token-a"].list_models.return_value = model_result("auto", "new-a")
        backends["token-b"].list_models.return_value = model_result("auto", "new-b")
        with mock.patch.object(models, "OpenAIBackendAPI", side_effect=lambda access_token: backends[access_token]):
            ids = [m["id"] for m in models.list_models()["data"]]
        self.assertEqual(ids.count("auto"), 1)
        self.assertTrue({"new-a", "new-b"} <= set(ids))

    def test_one_failed_account_does_not_hide_models_from_another(self):
        self.accounts.append({"access_token": "token-b"})
        bad, good = mock.Mock(), mock.Mock()
        bad.list_models.side_effect = RuntimeError("offline")
        good.list_models.return_value = model_result("new-b")
        with mock.patch.object(models, "OpenAIBackendAPI", side_effect=lambda access_token: bad if access_token == "token-a" else good):
            self.assertIn("new-b", {m["id"] for m in models.list_models()["data"]})
        bad.close.assert_called_once()

    def test_fallback_to_anonymous_after_account_failure(self):
        bad, anonymous = mock.Mock(), mock.Mock()
        bad.list_models.side_effect = RuntimeError("offline")
        anonymous.list_models.return_value = model_result("anonymous-new")
        with mock.patch.object(models, "OpenAIBackendAPI", side_effect=lambda **kwargs: bad if kwargs else anonymous):
            self.assertIn("anonymous-new", {m["id"] for m in models.list_models()["data"]})

    def test_cached_result_is_copied_and_force_refresh_fetches_again(self):
        with mock.patch.object(models, "_fetch_upstream_models", side_effect=[model_result("first"), model_result("second")]) as fetch:
            first = models.list_models()
            first["data"].clear()
            self.assertIn("first", {m["id"] for m in models.list_models()["data"]})
            self.assertIn("second", {m["id"] for m in models.list_models(refresh=True)["data"]})
        self.assertEqual(fetch.call_count, 2)

    def test_account_addition_and_removal_invalidate_cache(self):
        with mock.patch.object(models, "_fetch_upstream_models", side_effect=[model_result("a"), model_result("b"), model_result("c")]) as fetch:
            models.list_models()
            self.accounts.append({"access_token": "token-b"})
            self.assertIn("b", {m["id"] for m in models.list_models()["data"]})
            self.accounts.pop()
            self.assertIn("c", {m["id"] for m in models.list_models()["data"]})
        self.assertEqual(fetch.call_count, 3)

    def test_cache_expiry_discovers_new_models(self):
        with mock.patch.object(models, "_fetch_upstream_models", side_effect=[model_result("old"), model_result("new")]):
            models.list_models()
            models._model_cache_expires_at = 0
            ids = {m["id"] for m in models.list_models()["data"]}
        self.assertIn("new", ids)
        self.assertNotIn("old", ids)

    def test_refresh_query_is_forwarded_by_http_api(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from api import ai

        app = FastAPI()
        app.include_router(ai.create_router())
        with (
            mock.patch.object(ai, "require_identity", return_value={}),
            mock.patch.object(ai.openai_v1_models, "list_models", return_value=model_result("new")) as fetch,
            TestClient(app) as client,
        ):
            response = client.get("/v1/models?refresh=true")
        self.assertEqual(response.status_code, 200)
        fetch.assert_called_once_with(refresh=True)

    def test_stale_cache_survives_outage_but_is_not_reused_for_different_accounts(self):
        with mock.patch.object(models, "_fetch_upstream_models", side_effect=[model_result("cached"), RuntimeError("offline"), RuntimeError("offline")]):
            models.list_models()
            self.assertIn("cached", {m["id"] for m in models.list_models(refresh=True)["data"]})
            self.accounts.clear()
            with self.assertRaises(RuntimeError):
                models.list_models()

    def test_discovered_new_image_version_and_codex_aliases_need_no_code_change(self):
        self.accounts.append({"source_type": "codex", "type": "Team"})
        with (
            mock.patch.object(models, "_fetch_upstream_models", return_value=model_result("new-chat")),
            mock.patch.object(models, "get_image_models", return_value={"gpt-image-9.3-sunburst", "gpt-image-9.3-flare"}),
        ):
            entries = {m["id"]: m for m in models.list_models()["data"]}
        self.assertEqual(entries["gpt-image-9.3"]["alias_for"], "gpt-image-9.3-sunburst")
        self.assertEqual(entries["gpt-image-9.3"]["availability"], "catalog")
        self.assertIn("team-codex-gpt-image-9.3-flare", entries)
        self.assertNotIn("plus-codex-gpt-image-9.3-flare", entries)

    def test_upstream_metadata_and_hidden_flags_are_preserved(self):
        backend = object.__new__(OpenAIBackendAPI)
        backend.access_token = "fake-token"
        backend.base_url = "https://example.invalid"
        backend._bootstrap = mock.Mock()
        backend._headers = mock.Mock(return_value={})
        backend.session = mock.Mock()
        backend.session.get.return_value.status_code = 200
        backend.session.get.return_value.json.return_value = {"models": [
            {"slug": "future-chat", "title": "Future Chat", "tags": ["hidden"]},
        ]}
        entry = backend.list_models()["data"][0]
        self.assertEqual(entry["title"], "Future Chat")
        self.assertTrue(entry["hidden"])
        self.assertEqual(entry["availability"], "account")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from services.account_service import AccountService
from services.storage.json_storage import JSONStorageBackend
from services.protocol import openai_v1_image_generations
from utils.account_pools import image_pool


class SubscriptionPoolTests(unittest.TestCase):
    def service(self, root: str, accounts: list[dict]) -> AccountService:
        storage = JSONStorageBackend(Path(root) / "accounts.json")
        storage.save_accounts(accounts)
        service = AccountService(storage)
        service.fetch_remote_info = lambda token, *_args: service.get_account(token)
        return service

    def test_pool_classification_ignores_case_and_groups_paid_variants(self):
        for plan, pool in (("free", "free"), ("Plus", "plus"), ("team", "plus"),
                           ("pro", "pro"), ("ProLite", "pro"), ("enterprise", "pro")):
            self.assertEqual(image_pool({"type": plan}), pool)

    def test_auto_uses_free_then_plus_then_pro_as_each_pool_is_exhausted(self):
        with tempfile.TemporaryDirectory() as root:
            service = self.service(root, [
                {"access_token": "pro-token", "type": "Pro", "quota": 100},
                {"access_token": "plus-token", "type": "Plus", "quota": 1},
                {"access_token": "free-token", "type": "free", "quota": 1},
            ])
            for expected in ("free-token", "plus-token", "pro-token"):
                token = service.get_available_access_token(source_type="default")
                self.assertEqual(token, expected)
                service.mark_image_result(token, True)

    def test_explicit_pool_never_spills_into_other_subscriptions(self):
        with tempfile.TemporaryDirectory() as root:
            service = self.service(root, [
                {"access_token": "plus-token", "type": "Plus", "quota": 120},
                {"access_token": "free-token", "type": "free", "quota": 0},
            ])
            self.assertEqual(service._list_ready_candidate_tokens(source_type="free"), [])
            with self.assertRaises(RuntimeError):
                service.get_available_access_token(source_type="free")
            token = service.get_available_access_token(source_type="plus")
            self.assertEqual(token, "plus-token")
            service.release_image_slot(token)

    def test_unavailable_free_accounts_do_not_block_paid_fallback(self):
        with tempfile.TemporaryDirectory() as root:
            service = self.service(root, [
                {"access_token": "limited", "type": "free", "quota": 20, "status": "限流"},
                {"access_token": "disabled", "type": "free", "quota": 20, "status": "禁用"},
                {"access_token": "plus-token", "type": "Plus", "quota": 120},
            ])
            self.assertEqual(service._list_available_candidate_tokens(source_type="default"), ["plus-token"])

    def test_real_free_quota_is_preserved_and_zero_is_not_reset_to_twenty(self):
        with tempfile.TemporaryDirectory() as root:
            service = self.service(root, [{"access_token": "free-token", "type": "free", "source_type": "web", "quota": 20}])
            self.assertEqual(service.get_account("free-token")["image_pool"], "free")
            self.assertEqual(service.get_account("free-token")["quota"], 20)
            for _ in range(20):
                service.mark_image_result("free-token", True)
            self.assertEqual(service.get_account("free-token")["quota"], 0)
            reloaded = AccountService(service.storage)
            self.assertEqual(reloaded.get_account("free-token")["quota"], 0)

    def test_image_api_accepts_each_subscription_pool(self):
        from api.ai import ImageGenerationRequest
        from api.image_tasks import ImageGenerationTaskRequest
        from api.image_inputs import _payload_from_fields

        for pool in ("free", "plus", "pro"):
            request = ImageGenerationRequest(prompt="dot", account_pool=pool)
            self.assertEqual(request.account_pool, pool)
            task = ImageGenerationTaskRequest(client_task_id="test", prompt="dot", account_pool=pool)
            self.assertEqual(task.account_pool, pool)
            self.assertEqual(_payload_from_fields({"prompt": "dot", "account_pool": pool})["account_pool"], pool)
            with (
                mock.patch.object(openai_v1_image_generations, "stream_image_outputs_with_pool", return_value=iter([])) as generate,
                mock.patch.object(openai_v1_image_generations, "collect_image_outputs", return_value={"data": []}),
            ):
                openai_v1_image_generations.handle({"prompt": "dot", "account_pool": pool})
            self.assertEqual(generate.call_args.args[0].source_type, pool)

    def test_text_chat_preserves_explicit_subscription_pool(self):
        from services.protocol import openai_v1_chat_complete as chat
        with (
            mock.patch.object(chat.chat_completion_cache, "get_or_compute_stream", side_effect=lambda _key, fn: fn()),
            mock.patch.object(chat, "text_backend") as backend,
            mock.patch.object(chat, "stream_text_chat_completion", return_value=iter([])) as stream,
        ):
            list(chat.handle({"model": "auto", "messages": [{"role": "user", "content": "hi"}],
                              "stream": True, "account_pool": "plus"}))
        backend.assert_called_once_with("plus")
        self.assertEqual(stream.call_args.args[-1], "plus")

    def test_responses_preserves_explicit_subscription_pool(self):
        from services.protocol import openai_v1_response as responses
        with (
            mock.patch.object(responses.chat_completion_cache, "get_or_compute_stream", side_effect=lambda _key, fn: fn()),
            mock.patch.object(responses, "text_backend") as backend,
            mock.patch.object(responses, "stream_text_response", return_value=iter([])),
        ):
            list(responses.response_events({"model": "auto", "input": "hi", "account_pool": "pro"}))
        backend.assert_called_once_with("pro")


if __name__ == "__main__":
    unittest.main()

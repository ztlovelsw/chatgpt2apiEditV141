from __future__ import annotations

import json
import unittest
from unittest import mock

from services.config import config
from services.openai_backend_api import ChatRequirements, OpenAIBackendAPI
from services.protocol import conversation, openai_v1_models, openai_v1_response
from utils.helper import image_generation_model, is_codex_image_model, is_image_chat_request, split_image_model


class ImageModelVersionTests(unittest.TestCase):
    def test_versioned_models_route_to_images_and_keep_snapshots(self):
        for model in (
            "gpt-image-2.5", "gpt-image-2.6", "gpt-image-2.6-flare",
            "gpt-image-2.5-sunburst-2026-09-08", "gpt-image-2.7-sunburst",
        ):
            with self.subTest(model=model):
                self.assertEqual(split_image_model(model), (None, model))
                self.assertTrue(is_image_chat_request({"model": model}))
                self.assertFalse(openai_v1_response.is_text_response_request({"model": model}))
                self.assertFalse(is_codex_image_model(model))
        self.assertEqual(image_generation_model("gpt-image-2.5"), "gpt-image-2.5-sunburst")
        self.assertEqual(image_generation_model("gpt-image-2.6"), "gpt-image-2.6-sunburst")
        self.assertEqual(
            image_generation_model("gpt-image-2.5-flare-2026-09-08"),
            "gpt-image-2.5-flare-2026-09-08",
        )
        for model in ("gpt-image-2.6/invalid", "gpt-image-2.", "plus-gpt-image-2.6", "gpt-6"):
            self.assertEqual(split_image_model(model), (None, None))

    def test_codex_plan_prefix_and_alias_keep_image_version(self):
        for plan in ("plus", "team", "pro"):
            model = f"{plan}-codex-gpt-image-2.6-flare"
            self.assertEqual(split_image_model(model), (plan, "codex-gpt-image-2.6-flare"))
            self.assertTrue(is_codex_image_model(model))
            self.assertEqual(image_generation_model(model), "gpt-image-2.6-flare")

    def test_responses_image_tool_model_is_forwarded(self):
        with (
            mock.patch.object(openai_v1_response, "stream_image_outputs_with_pool", return_value=iter([])) as generate,
            mock.patch.object(openai_v1_response, "stream_image_response", return_value=iter([])),
        ):
            list(openai_v1_response.response_events({
                "model": "gpt-6",
                "input": "circle",
                "tools": [{"type": "image_generation", "model": "gpt-image-2.6-flare"}],
            }))
        self.assertEqual(generate.call_args.args[0].model, "gpt-image-2.6-flare")

    def test_web_prepare_and_generation_send_requested_version(self):
        backend = object.__new__(OpenAIBackendAPI)
        backend.base_url = "https://example.invalid"
        backend.session = mock.Mock()
        backend.session.post.return_value.status_code = 200
        backend.session.post.return_value.json.return_value = {"conduit_token": "test-conduit"}
        backend._image_headers = mock.Mock(return_value={})
        requirements = ChatRequirements(token="test-token")
        for model in ("gpt-image-2.5", "gpt-image-2.6-flare"):
            with self.subTest(model=model):
                backend._prepare_image_conversation("circle", requirements, model)
                prepare = backend.session.post.call_args.kwargs["json"]
                backend._start_image_generation("circle", requirements, "test-conduit", model)
                generate = backend.session.post.call_args.kwargs["json"]
                self.assertEqual(prepare["model"], image_generation_model(model))
                self.assertEqual(generate["model"], prepare["model"])
                self.assertIn("picture_v2", generate["system_hints"])
        self.assertEqual(backend._image_model_slug("gpt-image-2"), config.image_default_model_slug)

    def test_codex_tool_receives_selected_version(self):
        backend = object.__new__(OpenAIBackendAPI)
        backend.access_token = "fake-token"
        backend.base_url = "https://example.invalid"
        backend._ensure_codex_source_account = mock.Mock()
        backend._iter_codex_response_events = mock.Mock(return_value=iter([]))
        with (
            mock.patch("services.openai_backend_api.account_service") as accounts,
            mock.patch("services.openai_backend_api.urllib.request.urlopen") as urlopen,
        ):
            accounts.get_account.return_value = {}
            accounts._decode_jwt_payload.return_value = {}
            list(backend.iter_codex_image_response_events("circle", model="plus-codex-gpt-image-2.6"))
        payload = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(payload["tools"][0]["model"], "gpt-image-2.6-sunburst")

    def test_codex_stream_forwards_model(self):
        backend = mock.Mock()
        backend.iter_codex_image_response_events.return_value = iter([
            {"type": "image_generation_call", "result": "aW1hZ2U="},
        ])
        request = conversation.ConversationRequest(model="team-codex-gpt-image-2.5-flare", prompt="circle")
        with mock.patch.object(conversation, "format_image_result", return_value={"data": [{"b64_json": "aW1hZ2U="}]}):
            result = list(conversation.stream_codex_image_outputs(backend, request))
        self.assertEqual(backend.iter_codex_image_response_events.call_args.kwargs["model"], request.model)
        self.assertEqual(result[0].model, request.model)

    def test_model_list_exposes_new_web_and_codex_models_by_account_source(self):
        for source in ("web", "codex"):
            with (
                self.subTest(source=source),
                mock.patch.object(openai_v1_models, "_get_upstream_models", return_value={"object": "list", "data": []}),
                mock.patch.object(openai_v1_models, "get_image_models", return_value={"gpt-image-2.5-sunburst", "gpt-image-2.6-sunburst", "gpt-image-2.6-flare"}),
                mock.patch.object(openai_v1_models.account_service, "list_accounts", return_value=[
                    {"type": "Plus", "source_type": source},
                ]),
            ):
                ids = [item["id"] for item in openai_v1_models.list_models()["data"]]
                self.assertIn("gpt-image-2.5", ids)
                self.assertIn("gpt-image-2.6-sunburst", ids)
                self.assertIn("gpt-image-2.6-flare", ids)
                self.assertEqual(len(ids), len(set(ids)))
                self.assertEqual("plus-codex-gpt-image-2.6" in ids, source == "codex")


if __name__ == "__main__":
    unittest.main()

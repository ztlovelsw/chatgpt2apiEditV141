from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from PIL import Image

from services.image_storage_service import ImageStorageService, WebDAVClient
from services import image_service
from services.openai_backend_api import OpenAIBackendAPI
from services.account_service import AccountService
from services.protocol import conversation, openai_v1_image_generations
from utils.helper import UpstreamHTTPError, build_chat_image_markdown_content
from utils.image_formats import generation_options, image_metadata


def gif_bytes() -> bytes:
    output = BytesIO()
    first = Image.new("RGB", (16, 16), "red")
    second = Image.new("RGB", (16, 16), "blue")
    first.save(output, format="GIF", save_all=True, append_images=[second], duration=200, loop=0)
    return output.getvalue()


class GifGenerationTests(unittest.TestCase):
    def test_output_options_validate_gif_combinations(self):
        self.assertEqual(generation_options({"output_format": "gif"}),
                         {"output_format": "gif", "frame_count": 9, "frame_duration_ms": 200})
        for options in (
            {"stream": True}, {"frame_count": 1}, {"frame_count": 17},
            {"frame_duration_ms": 0}, {"frame_count": True},
            {"images": ["ref"], "model": "codex-gpt-image-2"},
            {"size": "17x16"}, {"account_pool": "gptfree"},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                generation_options({"output_format": "gif", **options})
        with self.assertRaises(ValueError):
            generation_options({"output_format": "png", "frame_count": 9})

    def test_backend_sends_native_gif_parameters_to_direct_image_endpoint(self):
        backend = object.__new__(OpenAIBackendAPI)
        backend.base_url = "https://example.invalid"
        backend.session = mock.Mock()
        backend.session.post.return_value.status_code = 200
        backend.session.post.return_value.json.return_value = {"data": [{"b64_json": "test"}]}
        backend._ensure_codex_source_account = mock.Mock()
        backend.access_token = "fake-token"
        backend._headers = mock.Mock(return_value={})
        with mock.patch("services.openai_backend_api.account_service.get_account", return_value={"source_type": "oauth_login"}):
            backend.generate_gif("dot", "codex-gpt-image-2.6-flare", "auto", "low", 2, 200)
        args = backend.session.post.call_args
        self.assertTrue(args.args[0].endswith("/backend-api/codex/images/generations"))
        self.assertEqual(args.kwargs["json"]["model"], "gpt-image-2.6-flare")
        self.assertEqual(args.kwargs["json"]["output_format"], "gif")
        self.assertEqual(args.kwargs["json"]["frame_count"], 2)

    def test_gif_source_selection_includes_oauth_login_and_excludes_web_accounts(self):
        for source in ("oauth_login", "codex"):
            self.assertTrue(AccountService._account_matches_source_type({"source_type": source}, "codex_images"))
        for source in ("web", "gptfree"):
            self.assertFalse(AccountService._account_matches_source_type({"source_type": source}, "codex_images"))
        self.assertFalse(AccountService._account_matches_source_type({"source_type": "oauth_login"}, "codex"))

    def test_gif_result_preserves_bytes_frames_and_mime(self):
        payload = gif_bytes()
        backend = mock.Mock()
        backend.generate_gif.return_value = [{"b64_json": base64.b64encode(payload).decode()}]
        request = conversation.ConversationRequest(model="codex-gpt-image-2.6-flare", prompt="dot", output_format="gif", frame_count=2)
        with mock.patch.object(conversation, "save_image_bytes", return_value="http://example.invalid/result.gif"):
            outputs = list(conversation.stream_gif_image_outputs(backend, request))
        result = outputs[0].data[0]
        self.assertEqual(base64.b64decode(result["b64_json"]), payload)
        self.assertEqual(result["mime_type"], "image/gif")
        self.assertEqual(result["frame_count"], 2)
        self.assertIn("data:image/gif;base64,", build_chat_image_markdown_content({"data": [result]}))

    def test_static_image_is_not_accepted_as_gif_success(self):
        output = BytesIO()
        Image.new("RGB", (16, 16)).save(output, format="PNG")
        backend = mock.Mock()
        backend.generate_gif.return_value = [{"b64_json": base64.b64encode(output.getvalue()).decode()}]
        with self.assertRaises(conversation.ImageGenerationError) as raised:
            list(conversation.stream_gif_image_outputs(backend, conversation.ConversationRequest(model="codex-gpt-image-2.6-flare", output_format="gif")))
        self.assertEqual(raised.exception.code, "invalid_gif_output")

    def test_upstream_permission_rejection_is_not_a_success(self):
        backend = mock.Mock()
        backend.generate_gif.side_effect = UpstreamHTTPError("gif", 403, "Forbidden")
        with self.assertRaises(conversation.ImageGenerationError) as raised:
            list(conversation.stream_gif_image_outputs(backend, conversation.ConversationRequest(model="codex-gpt-image-2.6-flare", output_format="gif")))
        self.assertEqual(raised.exception.status_code, 403)

    def test_gif_preview_error_remains_visible_in_public_response(self):
        backend = mock.Mock()
        backend.generate_gif.side_effect = UpstreamHTTPError(
            "/backend-api/codex/images/generations", 400,
            {"detail": "GIF output is not enabled for this request."},
        )
        with self.assertRaises(conversation.ImageGenerationError) as raised:
            list(conversation.stream_gif_image_outputs(backend, conversation.ConversationRequest(model="codex-gpt-image-2.6-flare", output_format="gif")))
        error = raised.exception.to_openai_error()["error"]
        self.assertEqual(error["code"], "gif_not_enabled")
        self.assertIn("Codex", error["message"])
        self.assertIn("官网链路", error["message"])

    def test_gif_requires_codex_account_before_any_generation(self):
        with mock.patch.object(conversation.account_service, "list_accounts", return_value=[{"source_type": "web"}]):
            with self.assertRaises(conversation.ImageGenerationError) as raised:
                list(conversation.stream_image_outputs_with_pool(conversation.ConversationRequest(model="codex-gpt-image-2.6", output_format="gif")))
        self.assertEqual(raised.exception.code, "gif_account_required")

    def test_protocol_forwards_gif_options(self):
        with (
            mock.patch.object(openai_v1_image_generations, "stream_image_outputs_with_pool", return_value=iter([])) as generate,
            mock.patch.object(openai_v1_image_generations, "collect_image_outputs", return_value={"data": []}),
        ):
            openai_v1_image_generations.handle({"prompt": "dot", "output_format": "gif", "frame_count": 2, "frame_duration_ms": 300})
        request = generate.call_args.args[0]
        self.assertEqual((request.output_format, request.frame_count, request.frame_duration_ms), ("gif", 2, 300))

    def test_storage_and_remote_download_keep_gif_frames(self):
        payload = gif_bytes()
        with tempfile.TemporaryDirectory() as directory, mock.patch("services.image_storage_service.config") as cfg:
            cfg.images_dir = Path(directory) / "images"
            cfg.base_url = "http://example.invalid"
            cfg.get_image_storage_settings.return_value = {"mode": "local"}
            storage = ImageStorageService(Path(directory) / "index.json")
            stored = storage.save(payload)
            self.assertTrue(stored.rel.endswith(".gif"))
            self.assertEqual(storage.get_bytes(stored.rel), payload)
            self.assertEqual(image_metadata(storage.get_bytes(stored.rel))["frame_count"], 2)
            self.assertEqual(storage.list_items("http://example.invalid")[0]["mime_type"], "image/gif")
            with mock.patch.object(image_service, "image_storage_service") as remote:
                remote.has_local.return_value = False
                remote.get_bytes.return_value = payload
                response = image_service.get_image_download_response(stored.rel)
                self.assertEqual(response.media_type, "image/gif")
                self.assertEqual(response.body, payload)

    def test_webdav_put_uses_gif_content_type(self):
        client = object.__new__(WebDAVClient)
        client.ensure_dirs = mock.Mock()
        client.remote_url = mock.Mock(return_value="https://example.invalid/test.gif")
        client._request = mock.Mock()
        client.put("test.gif", gif_bytes())
        self.assertEqual(client._request.call_args.kwargs["headers"]["Content-Type"], "image/gif")

    def test_http_generation_passes_animation_settings_and_returns_animated_data(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from api import ai

        app = FastAPI()
        app.include_router(ai.create_router())
        payload = gif_bytes()
        backend = mock.Mock()
        backend.generate_gif.return_value = [{"b64_json": base64.b64encode(payload).decode()}]
        requests = []

        def generate(request, index, total):
            requests.append(request)
            result = conversation.format_image_result(backend.generate_gif.return_value, request.prompt, request.response_format)
            return [conversation.ImageOutput(kind="result", model=request.model, index=index, total=total, data=result["data"])]


        with (
            mock.patch.object(ai, "require_identity", return_value={"id": "test-owner", "role": "admin"}),
            mock.patch.object(ai, "filter_or_log", mock.AsyncMock()),
            mock.patch.object(conversation.account_service, "list_accounts", return_value=[{"source_type": "codex"}]),
            mock.patch.object(conversation, "_generate_single_image", side_effect=generate),
            mock.patch.object(conversation, "save_image_bytes", return_value="http://example.invalid/animation.gif"),
            TestClient(app) as client,
        ):
            response = client.post("/v1/images/generations", json={
                "prompt": "dot", "model": "gpt-image-2.6-flare", "output_format": "gif",
                "frame_count": 2, "frame_duration_ms": 200,
            })
            invalid = client.post("/v1/images/generations", json={"prompt": "dot", "output_format": "gif", "stream": True})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(requests[0].frame_count, 2)
        result = response.json()["data"][0]
        self.assertEqual(result["mime_type"], "image/gif")
        self.assertEqual(image_metadata(base64.b64decode(result["b64_json"]))["frame_count"], 2)

    def test_background_task_keeps_animation_parameters_after_reload(self):
        from services.image_task_service import ImageTaskService
        from test.test_image_task_service import wait_for_task

        seen = []
        def generate(payload):
            seen.append(payload)
            return {"data": [{"url": "http://example.invalid/animation.gif", "mime_type": "image/gif"}]}
        owner = {"id": "test-owner", "role": "admin"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.json"
            service = ImageTaskService(path, generation_handler=generate, retention_days_getter=lambda: 30)
            service.submit_generation(owner, client_task_id="gif-test", prompt="dot", model="gpt-image-2.6",
                                      size="auto", output_format="gif", frame_count=2, frame_duration_ms=300)
            wait_for_task(service, owner, "gif-test", "success")
            restored = ImageTaskService(path, generation_handler=generate, retention_days_getter=lambda: 30)
            task = restored.list_tasks(owner, ["gif-test"])["items"][0]
        self.assertEqual(seen[0]["frame_duration_ms"], 300)
        self.assertEqual(task["output_format"], "gif")
        self.assertEqual(task["frame_count"], 2)
        self.assertTrue(task["data"][0]["url"].endswith(".gif"))

    def test_web_gif_uses_website_conversation_and_filters_png_preview(self):
        backend = mock.Mock()
        gif = {"output_format": "gif", "mime_type": "image/gif", "frame_count": 4, "url": "/animation.gif"}
        preview = {"output_format": "png", "mime_type": "image/png", "url": "/preview.png"}
        output = conversation.ImageOutput(kind="result", model="gpt-image-2.6-flare", index=1, total=1,
                                          data=[gif, preview], conversation_id="web-conversation")
        request = conversation.ConversationRequest(model="gpt-image-2.6-flare", prompt="ball", output_format="gif",
                                                    frame_count=4, frame_duration_ms=200, images=[{"reference": "test"}])
        with mock.patch.object(conversation, "stream_image_outputs", return_value=iter([output])) as web_stream:
            result = list(conversation.stream_gif_image_outputs(backend, request))
        backend.generate_gif.assert_not_called()
        forwarded = web_stream.call_args.args[1]
        self.assertIn("4 animation frames", forwarded.prompt)
        self.assertIn("200 milliseconds", forwarded.prompt)
        self.assertEqual(forwarded.images, request.images)
        self.assertEqual(result[0].data, [gif])
        self.assertEqual(result[0].conversation_id, "web-conversation")

    def test_web_gif_does_not_require_a_codex_source_account(self):
        request = conversation.ConversationRequest(model="gpt-image-2.6-flare", output_format="gif")
        output = conversation.ImageOutput(kind="result", model=request.model, index=1, total=1,
                                          data=[{"url": "/animation.gif"}])
        with (
            mock.patch.object(conversation.account_service, "list_accounts", return_value=[{"source_type": "web"}]),
            mock.patch.object(conversation, "_generate_single_image", return_value=[output]) as generate,
        ):
            self.assertEqual(list(conversation.stream_image_outputs_with_pool(request)), [output])
        generate.assert_called_once()


if __name__ == "__main__":
    unittest.main()

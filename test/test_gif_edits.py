from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from api import ai, image_tasks
from services.image_task_service import ImageTaskService
from services.protocol import conversation
from test.test_gif_generation import gif_bytes
from test.test_image_task_service import wait_for_task
from utils.image_formats import image_metadata


class GifEditsTests(unittest.TestCase):
    def setUp(self):
        reference = BytesIO()
        Image.new("RGB", (16, 16), "red").save(reference, format="PNG")
        self.reference = reference.getvalue()
        self.reference_url = "data:image/png;base64," + base64.b64encode(self.reference).decode()
        self.animation = gif_bytes()
        self.owner = {"id": "gif-edit-owner", "role": "admin"}
        self.requests = []
        self.backend = mock.Mock()

        def web_stream(backend, request, index, total):
            self.requests.append(request)
            yield conversation.ImageOutput(
                kind="result", model=request.model, index=index, total=total,
                data=[{"b64_json": base64.b64encode(self.animation).decode(),
                       **image_metadata(self.animation)}],
            )

        def generate(request, index, total):
            return conversation.stream_gif_image_outputs(self.backend, request, index, total)

        for patcher in (
            mock.patch.object(ai, "require_identity", return_value=self.owner),
            mock.patch.object(ai, "filter_or_log", mock.AsyncMock()),
            mock.patch.object(image_tasks, "require_identity", return_value=self.owner),
            mock.patch.object(image_tasks, "filter_or_log", mock.AsyncMock()),
            mock.patch.object(conversation, "stream_image_outputs", side_effect=web_stream),
            mock.patch.object(conversation, "_generate_single_image", side_effect=generate),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        app = FastAPI()
        app.include_router(ai.create_router())
        app.include_router(image_tasks.create_router())
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_images_edits_accepts_json_and_multipart_and_reaches_website(self):
        options = {"prompt": "make the character wave", "model": "gpt-image-2",
                   "output_format": "gif", "frame_count": 4, "frame_duration_ms": 300}
        for encoding in ("json", "multipart"):
            with self.subTest(encoding=encoding):
                if encoding == "json":
                    response = self.client.post("/v1/images/edits", json={
                        **options, "images": [{"image_url": self.reference_url}]})
                else:
                    response = self.client.post("/v1/images/edits",
                        data={key: str(value) for key, value in options.items()},
                        files={"image": ("reference.png", self.reference, "image/png")})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(base64.b64decode(response.json()["data"][0]["b64_json"]), self.animation)
                request = self.requests[-1]
                self.assertEqual(request.images[0], base64.b64encode(self.reference).decode())
                self.assertEqual((request.output_format, request.frame_count, request.frame_duration_ms), ("gif", 4, 300))
                self.assertIn("4 animation frames", request.prompt)
        self.backend.generate_gif.assert_not_called()

    def test_invalid_edit_options_are_rejected_before_upstream(self):
        for options in (
            {"stream": True}, {"frame_count": 1}, {"frame_count": True},
            {"frame_count": "invalid"}, {"frame_duration_ms": 2001},
            {"size": "17x16"}, {"model": "codex-gpt-image-2"},
            {"model": "plus-codex-gpt-image-2"},
        ):
            with self.subTest(options=options):
                response = self.client.post("/v1/images/edits", json={
                    "prompt": "animate", "output_format": "gif",
                    "images": [{"image_url": self.reference_url}], **options})
                self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self.requests, [])

    def test_chat_and_responses_forward_reference_images_for_gif(self):
        options = {"model": "gpt-image-2", "output_format": "gif",
                   "frame_count": 4, "frame_duration_ms": 300}
        for endpoint, body in (
            ("/v1/chat/completions", {"messages": [{"role": "user", "content": [
                {"type": "text", "text": "animate this character"},
                {"type": "image_url", "image_url": {"url": self.reference_url}},
            ]}]}),
            ("/v1/responses", {"input": [{"role": "user", "content": [
                {"type": "input_text", "text": "animate this character"},
                {"type": "input_image", "image_url": self.reference_url},
            ]}]}),
        ):
            with self.subTest(endpoint=endpoint):
                response = self.client.post(endpoint, json={**options, **body})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertIn(base64.b64encode(self.animation).decode(), response.text)
                self.assertEqual(self.requests[-1].images, [base64.b64encode(self.reference).decode()])
                self.assertEqual(self.requests[-1].output_format, "gif")

    def test_background_edit_task_keeps_images_and_gif_settings_after_reload(self):
        seen = []

        def edit(payload):
            seen.append(payload)
            return {"data": [{"url": "/animation.gif", "mime_type": "image/gif"}]}

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.json"
            service = ImageTaskService(path, edit_handler=edit, retention_days_getter=lambda: 30)
            with mock.patch.object(image_tasks, "image_task_service", service):
                response = self.client.post("/api/image-tasks/edits",
                    data={"client_task_id": "gif-edit", "prompt": "animate", "output_format": "gif",
                          "frame_count": "4", "frame_duration_ms": "300"},
                    files={"image": ("reference.png", self.reference, "image/png")})
            self.assertEqual(response.status_code, 200, response.text)
            wait_for_task(service, self.owner, "gif-edit", "success")
            restored = ImageTaskService(path, edit_handler=edit, retention_days_getter=lambda: 30)
            task = restored.list_tasks(self.owner, ["gif-edit"])["items"][0]
        self.assertEqual(seen[0]["images"][0][0], self.reference)
        self.assertEqual((seen[0]["output_format"], seen[0]["frame_count"], seen[0]["frame_duration_ms"]), ("gif", 4, 300))
        self.assertEqual((task["output_format"], task["frame_count"], task["frame_duration_ms"]), ("gif", 4, 300))
        self.assertEqual(task["data"][0]["url"], "/animation.gif")

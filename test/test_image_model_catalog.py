from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from services import image_model_catalog as catalog
from utils.helper import image_generation_model, is_image_chat_request


class ImageCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = Path(self.temp.name) / "catalog.json"
        for patcher in (
            mock.patch.object(catalog, "CATALOG_CACHE_FILE", self.cache),
            mock.patch.object(catalog, "_models", set()),
            mock.patch.object(catalog, "_expires_at", 0.0),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_parse_only_supported_models_and_route_future_major_versions(self):
        html = '<p>Supported models include <code>gpt-image-9.3-sunburst</code>, <code>gpt-image-9.3-flare</code>.</p><p>gpt-image-1 was retired.</p>'
        discovered = catalog.parse_image_models(html)
        self.assertEqual(discovered, {"gpt-image-9.3-sunburst", "gpt-image-9.3-flare"})
        with mock.patch.object(catalog, "_models", discovered):
            self.assertEqual(image_generation_model("gpt-image-9.3"), "gpt-image-9.3-sunburst")
            self.assertTrue(is_image_chat_request({"model": "gpt-image-9.3"}))

    def test_catalog_is_fetched_saved_and_reused_without_network(self):
        with mock.patch.object(catalog.requests, "Session") as session:
            session.return_value.__enter__.return_value.get.return_value.text = '<p>Supported models include <code>gpt-image-9-flare</code>.</p>'
            self.assertEqual(catalog.get_image_models(), {"gpt-image-9-flare"})
            self.assertTrue(self.cache.exists())
            self.assertEqual(catalog.get_image_models(), {"gpt-image-9-flare"})
            session.assert_called_once()
        catalog._models = set()
        catalog._expires_at = 0
        with mock.patch.object(catalog.requests, "Session") as session:
            self.assertEqual(catalog.get_image_models(), {"gpt-image-9-flare"})
            session.assert_not_called()

    def test_expired_catalog_survives_network_failure(self):
        self.cache.write_text(json.dumps({"models": ["gpt-image-9-flare"], "expires_at": time.time()-10}), encoding="utf-8")
        with mock.patch.object(catalog.requests, "Session", side_effect=RuntimeError("offline")):
            self.assertEqual(catalog.get_image_models(), {"gpt-image-9-flare"})

    def test_invalid_page_does_not_replace_last_successful_catalog(self):
        catalog._models = {"gpt-image-9-flare"}
        with mock.patch.object(catalog.requests, "Session") as session:
            session.return_value.__enter__.return_value.get.return_value.text = '<p>Access denied</p>'
            self.assertEqual(catalog.get_image_models(refresh=True), {"gpt-image-9-flare"})


if __name__ == "__main__":
    unittest.main()

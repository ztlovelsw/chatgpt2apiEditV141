from __future__ import annotations

import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api import libraries
from services import prompt_import_service as importer
from services.library_service import PromptLibraryService


SOURCE = "https://example.com/prompts/demo.md"
MARKDOWN = """# 提示词合集
## 橘猫
### Prompt
```text
A ginger cat on a windowsill.
Soft natural light, 3:2.
```
### 效果图
![橘猫](../images/cat.png)
## 小狗
![小狗](dog.webp)
> A white puppy in a garden.
"""


class PromptParsingTests(unittest.TestCase):
    def parse(self, text, mime="text/html"):
        return importer.parse_prompt_document(text, mime, SOURCE, SOURCE)

    def test_markdown_pairs_multiple_prompts_and_relative_images(self):
        data = self.parse(MARKDOWN, "text/markdown")
        self.assertEqual([item["name"] for item in data["items"]], ["橘猫", "小狗"])
        self.assertEqual(data["items"][0]["content"], "A ginger cat on a windowsill.\nSoft natural light, 3:2.")
        self.assertEqual(data["items"][0]["example_images"], ["https://example.com/images/cat.png"])
        self.assertEqual(data["items"][1]["example_images"], ["https://example.com/prompts/dog.webp"])
        self.assertEqual(data["warnings"], [])

    def test_html_preserves_prompt_and_skips_navigation_and_inline_scripts(self):
        result = self.parse('''<html><head><title>猫的光影</title></head><body>
        <nav><pre>Navigation sample</pre><img src="logo.png"></nav>
        <article><h1>猫的光影</h1><pre><code>A cat &amp; a dog.\nWarm light.</code></pre>
        <img data-src="../cat.png" src="data:image/png;base64,placeholder">
        <img src="tracking.png" width="1"><script>alert('not content')</script></article></body></html>''')
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["content"], "A cat & a dog.\nWarm light.")
        self.assertEqual(result["items"][0]["example_images"], ["https://example.com/cat.png"])

    def test_cards_keep_their_own_images(self):
        result = self.parse('''<main><h1>Examples</h1>
        <div class="card"><div class="prompt">First prompt</div><img src="first.png"></div>
        <div class="card"><div class="prompt">Second prompt</div><img src="second.png"></div></main>''')
        self.assertEqual([item["example_images"] for item in result["items"]], [
            ["https://example.com/prompts/first.png"], ["https://example.com/prompts/second.png"],
        ])

    def test_nested_prompt_block_is_only_imported_once(self):
        result = self.parse('<article><h1>Example</h1><div class="prompt"><pre>Draw a cat</pre></div></article>')
        self.assertEqual([item["content"] for item in result["items"]], ["Draw a cat"])

    def test_labeled_paragraph_and_og_image(self):
        result = self.parse('<html><head><meta property="og:image" content="/preview.png"></head><body><p><b>提示词：</b> 一只橘猫</p></body></html>')
        self.assertEqual(result["items"][0]["content"], "一只橘猫")
        self.assertEqual(result["items"][0]["example_images"], ["https://example.com/preview.png"])

    def test_prompt_heading_collects_only_the_prompt_paragraphs(self):
        result = self.parse('<article><h1>橘猫</h1><p>介绍</p><h2>提示词</h2><p>A cat</p><p>Warm light</p><h2>使用方法</h2><p>不要当成提示词</p><img src="cat.png"></article>')
        self.assertEqual(result["items"][0]["content"], "A cat\n\nWarm light")
        self.assertEqual(result["items"][0]["name"], "橘猫")

    def test_markdown_badge_is_not_mistaken_for_json(self):
        result = self.parse('[![badge](https://example.com/badge.svg)](https://example.com)\n' + MARKDOWN, "text/plain")
        self.assertEqual([item["name"] for item in result["items"]], ["橘猫", "小狗"])

    def test_json_collection_and_embedded_page_data(self):
        records = {"items": [{"title": "Portrait", "prompt": "A portrait", "images": [{"url": "/portrait.jpg"}]},
                             {"name": "Cat", "prompt": "A cat", "image_url": "cat.png"}]}
        for mime, text in (("application/json", json.dumps(records)), ("text/html", '<script type="application/json">' + json.dumps(records) + '</script>')):
            with self.subTest(mime=mime):
                result = self.parse(text, mime)
                self.assertEqual(len(result["items"]), 2)
                self.assertEqual(result["items"][0]["name"], "Portrait")
                self.assertEqual(result["items"][0]["example_images"], ["https://example.com/portrait.jpg"])

    def test_plain_text_without_images_is_importable(self):
        result = self.parse("一只橘猫坐在窗台上，暖色阳光。", "text/plain")
        self.assertEqual(result["items"][0]["content"], "一只橘猫坐在窗台上，暖色阳光。")
        self.assertEqual(result["items"][0]["example_images"], [])
        self.assertTrue(result["warnings"])

    def test_login_and_script_only_pages_are_not_saved_as_prompts(self):
        for page in ('<html><body><h1>Sign in</h1><p>Please sign in</p></body></html>', '<html><body><div id="app"></div><script>fetch("/data")</script></body></html>'):
            with self.subTest(page=page), self.assertRaises(HTTPException):
                self.parse(page)

    def test_unsafe_and_duplicate_image_links_are_filtered(self):
        result = self.parse(json.dumps({"prompt": "A cat", "images": ["javascript:alert(1)", "file:///etc/passwd", "/cat.png", "/cat.png"]}), "application/json")
        self.assertEqual(result["items"][0]["example_images"], ["https://example.com/cat.png"])

    def test_malformed_empty_or_contentless_json_is_rejected(self):
        for text in ("{", "[]", '{"title":"Empty"}'):
            with self.subTest(text=text), self.assertRaises(HTTPException):
                self.parse(text, "application/json")

    def test_github_blob_download_uses_raw_document_and_keeps_source(self):
        source = "https://github.com/org/repo/blob/main/examples/cat.md"
        raw = "https://raw.githubusercontent.com/org/repo/main/examples/cat.md"
        with mock.patch.object(importer, "_download_document", return_value=("```\nA cat\n```\n![cat](cat.png)", "text/plain", raw)) as download:
            result = importer.import_prompt_url(source)
        download.assert_called_once_with(raw)
        self.assertEqual(result["items"][0]["source_url"], source)
        self.assertEqual(result["items"][0]["example_images"], ["https://raw.githubusercontent.com/org/repo/main/examples/cat.png"])


class PromptDownloadTests(unittest.TestCase):
    def setUp(self):
        self.dns = mock.patch.object(importer.socket, "getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]).start()
        self.addCleanup(mock.patch.stopall)
        self.session_class = mock.patch.object(importer.requests, "Session").start()
        self.session = self.session_class.return_value.__enter__.return_value

    def response(self, text=b"A prompt", *, status=200, headers=None):
        response = mock.Mock(status_code=status, headers=headers or {"content-type": "text/plain; charset=utf-8"})
        response.iter_content.return_value = iter([text])
        return response

    def test_download_limits_redirects_and_does_not_send_auth(self):
        redirect = self.response(status=302, headers={"location": "/final.md"})
        final = self.response("一只猫".encode())
        self.session.get.side_effect = [redirect, final]
        text, _, url = importer._download_document(SOURCE)
        self.assertEqual((text, url), ("一只猫", "https://example.com/final.md"))
        self.assertEqual(self.dns.call_count, 2)
        for call in self.session.get.call_args_list:
            self.assertFalse(call.kwargs["allow_redirects"])
            self.assertNotIn("Authorization", call.kwargs["headers"])
        redirect.close.assert_called_once()
        final.close.assert_called_once()

    def test_invalid_schemes_and_credentials_never_make_a_request(self):
        for url in ("file:///etc/passwd", "ftp://example.com/test", "https://user:password@example.com/", "https://example.com:bad/path", "https://example.com/\nfoo"):
            with self.subTest(url=url), self.assertRaises(HTTPException):
                importer.import_prompt_url(url)
        self.session.get.assert_not_called()

    def test_private_dns_and_redirects_are_rejected_before_fetch(self):
        for address in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "224.0.0.1"):
            self.dns.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]
            with self.subTest(address=address), self.assertRaises(HTTPException):
                importer._download_document(SOURCE)
        self.session.get.assert_not_called()
        self.dns.side_effect = [[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
                                [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))]]
        self.session.get.return_value = self.response(status=302, headers={"location": "http://127.0.0.1/private"})
        with self.assertRaises(HTTPException):
            importer._download_document(SOURCE)
        self.session.get.assert_called_once()

    def test_oversized_download_is_stopped_and_closed(self):
        response = self.response(b"x" * (importer.MAX_DOCUMENT_BYTES + 1))
        self.session.get.return_value = response
        with self.assertRaises(HTTPException) as caught:
            importer._download_document(SOURCE)
        self.assertIn("2MB", str(caught.exception.detail))
        response.close.assert_called_once()

    def test_http_error_and_binary_response_are_actionable(self):
        for response in (self.response(status=403), self.response(headers={"content-type": "image/png"})):
            self.session.get.return_value = response
            with self.assertRaises(HTTPException):
                importer._download_document(SOURCE)
            response.close.assert_called_once()


class ShuixianImportTests(unittest.TestCase):
    def setUp(self):
        # Same schema as shuixian-unified/data/prompts.part*.json, with no live dependency.
        self.records = [
            {"id": 22861, "title": "车极简", "category": "车", "prompt": 'A {argument name="car" default="BMW M4"} on a clean background.',
             "image": "images/originals/22861.jpg", "images": ["images/originals/22861.jpg", "images/originals/22862.jpg"]},
            {"id": 2, "title": "City poster", "category": "城市海报", "prompt": "Warm light in a city", "image": "images/city.png", "images": []},
        ]
        self.parts = {1: self.records, 2: [], 3: []}
        self.downloads = []

        def download(url, **kwargs):
            self.downloads.append((url, kwargs))
            part = int(url.split("part")[1].split(".")[0])
            return json.dumps(self.parts[part]), "text/plain", url

        mock.patch.object(importer, "_download_document", side_effect=download).start()
        mock.patch.object(importer, "_shuixian_cache", {}).start()
        self.addCleanup(mock.patch.stopall)

    def test_repository_root_loads_data_with_category_images_and_attribution(self):
        result = importer.import_prompt_url(importer.SHUIXIAN_REPOSITORY)
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["source_name"], "水仙的AI提示词")
        item = result["items"][0]
        self.assertEqual(item["content"], self.records[0]["prompt"])
        self.assertEqual(item["type"], "车")
        self.assertEqual(item["example_images"], ["https://r2.qqsrc.com/images/originals/22861.jpg", "https://r2.qqsrc.com/images/originals/22862.jpg"])
        self.assertEqual(item["source_url"], "https://prompt.qqsrc.com/detail.html?id=22861")
        self.assertIn(importer.SHUIXIAN_REPOSITORY, item["note"])
        self.assertEqual(len(self.downloads), 3)
        self.assertTrue(all(options["max_bytes"] == importer.SHUIXIAN_PART_BYTES for _, options in self.downloads))
        self.assertTrue(all("prompts.part" in url for url, _ in self.downloads))

    def test_supported_links_use_full_data_and_the_correct_partition(self):
        for url in (importer.SHUIXIAN_REPOSITORY + "/", importer.SHUIXIAN_REPOSITORY + "/blob/main/README.md", "https://prompt.qqsrc.com/", "https://prompt.qqsrc.com/classic/"):
            with self.subTest(url=url):
                self.assertEqual(importer.import_prompt_url(url)["total"], 2)
        self.parts[2] = [{**self.records[1], "id": 3}]
        importer._shuixian_cache.clear()
        self.downloads.clear()
        for url in (importer.SHUIXIAN_REPOSITORY + "/blob/main/shuixian-unified/data/list.part2.json", importer.SHUIXIAN_DATA_BASE + "prompts.part2.json"):
            with self.subTest(url=url):
                result = importer.import_prompt_url(url)
                self.assertEqual(result["total"], 1)
                self.assertEqual(result["items"][0]["source_id"], "3")
        self.assertEqual(len(self.downloads), 1)
        self.assertIn("prompts.part2.json", self.downloads[0][0])

    def test_detail_query_and_classic_fragment_select_one_prompt(self):
        for url in ("https://prompt.qqsrc.com/detail.html?id=22861", "https://prompt.qqsrc.com/classic/#id=22861"):
            with self.subTest(url=url):
                result = importer.import_prompt_url(url)
                self.assertEqual(result["total"], 1)
                self.assertEqual(result["items"][0]["name"], "车极简")
        with self.assertRaises(HTTPException) as caught:
            importer.import_prompt_url("https://prompt.qqsrc.com/detail.html?id=missing")
        self.assertEqual(caught.exception.status_code, 404)

    def test_pagination_covers_all_rows_and_reuses_cached_data(self):
        self.parts[1] = [{**self.records[0], "id": index} for index in range(205)]
        first = importer.import_prompt_url(importer.SHUIXIAN_REPOSITORY)
        second = importer.import_prompt_url(importer.SHUIXIAN_REPOSITORY, page=2)
        third = importer.import_prompt_url(importer.SHUIXIAN_REPOSITORY, page=3)
        self.assertEqual([len(page["items"]) for page in (first, second, third)], [100, 100, 5])
        self.assertEqual([page["has_more"] for page in (first, second, third)], [True, True, False])
        self.assertEqual(len({item["source_id"] for page in (first, second, third) for item in page["items"]}), 205)
        self.assertEqual(len(self.downloads), 3)
        self.assertEqual(importer.import_prompt_url(importer.SHUIXIAN_REPOSITORY, page=999)["page"], 3)

    def test_search_covers_title_category_and_content_in_all_parts(self):
        self.parts[3] = [{**self.records[1], "id": 3, "prompt": "A bright waterfall", "category": "自然景观"}]
        for query, expected in (("车极简", "22861"), ("自然景观", "3"), ("WATERFALL", "3")):
            with self.subTest(query=query):
                result = importer.import_prompt_url(importer.SHUIXIAN_REPOSITORY, query=query)
                self.assertEqual(result["total"], 1)
                self.assertEqual(result["items"][0]["source_id"], expected)
        empty = importer.import_prompt_url(importer.SHUIXIAN_REPOSITORY, page=5, query="does not exist")
        self.assertEqual((empty["items"], empty["total"], empty["page"], empty["has_more"]), ([], 0, 1, False))

    def test_cache_refreshes_after_five_minutes(self):
        importer.import_prompt_url(importer.SHUIXIAN_REPOSITORY)
        with mock.patch.object(importer.time, "monotonic", return_value=10**15):
            importer.import_prompt_url(importer.SHUIXIAN_REPOSITORY)
        self.assertEqual(len(self.downloads), 6)

    def test_wrong_hosts_and_unrelated_repository_pages_do_not_select_adapter(self):
        for url in ("https://prompt.qqsrc.com.evil.example/", importer.SHUIXIAN_REPOSITORY + "-fake", importer.SHUIXIAN_REPOSITORY + "/issues", "https://example.com/BaYue-SYJ/shuixian-prompts"):
            with self.subTest(url=url):
                self.assertIsNone(importer._shuixian_target(url))

    def test_data_failure_does_not_fall_back_to_readme_commands(self):
        with mock.patch.object(importer, "_download_document", return_value=("<html>Unavailable</html>", "text/html", importer.SHUIXIAN_DATA_BASE)):
            with self.assertRaises(HTTPException) as caught:
                importer.import_prompt_url(importer.SHUIXIAN_REPOSITORY)
        self.assertEqual(caught.exception.status_code, 502)
        self.assertEqual(importer._shuixian_cache, {})


class PromptLibraryApiTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "prompts.json"
        self.service = PromptLibraryService(self.path)
        mock.patch.object(libraries, "prompt_library_service", self.service).start()
        mock.patch("api.support._legacy_admin_identity", side_effect=lambda token: {"role": "user"} if token == "test-prompt-key" else None).start()
        mock.patch("api.support.auth_service.authenticate", return_value=None).start()
        self.addCleanup(mock.patch.stopall)
        app = FastAPI()
        app.include_router(libraries.create_router())
        self.client = TestClient(app)
        self.headers = {"Authorization": "Bearer test-prompt-key"}

    def test_import_requires_authentication_and_does_not_mutate_library(self):
        with mock.patch.object(importer, "_download_document", return_value=(MARKDOWN, "text/markdown", SOURCE)) as download:
            denied = self.client.post("/api/prompt-library/import-url", json={"url": SOURCE})
            self.assertEqual(denied.status_code, 401)
            download.assert_not_called()
            response = self.client.post("/api/prompt-library/import-url", headers=self.headers, json={"url": SOURCE})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["items"]), 2)
        self.assertFalse(self.path.exists())

    def test_import_create_edit_reload_and_delete_preserve_images(self):
        draft = importer.parse_prompt_document(MARKDOWN, "text/markdown", SOURCE, SOURCE)["items"][0]
        created = self.client.post("/api/prompt-library", headers=self.headers, json=draft)
        self.assertEqual(created.status_code, 200, created.text)
        item = created.json()["item"]
        result = []
        thread = threading.Thread(target=lambda: result.append(self.client.post(f"/api/prompt-library/{item['id']}", headers=self.headers, json={"name": "Updated"})), daemon=True)
        thread.start()
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive(), "Editing must not deadlock while returning the updated library")
        self.assertEqual(result[0].status_code, 200)
        restored = PromptLibraryService(self.path).list_items()["items"][0]
        self.assertEqual(restored["name"], "Updated")
        self.assertEqual(restored["content"], draft["content"])
        self.assertEqual(restored["example_images"], draft["example_images"])
        self.assertEqual(restored["source_url"], SOURCE)
        removed = self.client.delete(f"/api/prompt-library/{item['id']}", headers=self.headers)
        self.assertEqual(removed.json()["items"], [])

    def test_legacy_records_can_be_read_and_updated(self):
        self.path.write_text(json.dumps({"items": [{"id": "old", "name": "Old", "content": "A cat", "type": "默认"}]}), encoding="utf-8")
        result = self.client.get("/api/prompt-library", headers=self.headers)
        self.assertEqual(result.json()["items"][0]["content"], "A cat")
        # New metadata can be added or explicitly cleared without migrating old files.
        self.service.update_item("old", {"source_url": SOURCE, "example_images": ["https://example.com/cat.png"]})
        response = self.client.post("/api/prompt-library/old", headers=self.headers, json={"source_url": "", "example_images": []})
        self.assertEqual(response.json()["item"]["example_images"], [])

    def test_invalid_metadata_is_not_persisted(self):
        for fields in ({"example_images": ["javascript:alert(1)"]}, {"source_url": "file:///secret"}, {"example_images": [f"https://example.com/{i}.png" for i in range(13)]}):
            with self.subTest(fields=fields):
                response = self.client.post("/api/prompt-library", headers=self.headers, json={"content": "A cat", **fields})
                self.assertIn(response.status_code, (400, 422))
        self.assertEqual(self.service.list_items()["items"], [])

    def test_import_api_forwards_page_and_query(self):
        with mock.patch.object(libraries, "import_prompt_url", return_value={"items": [], "warnings": [], "total": 0}) as load:
            response = self.client.post("/api/prompt-library/import-url", headers=self.headers, json={"url": importer.SHUIXIAN_REPOSITORY, "page": 2, "query": "海报"})
            self.assertEqual(response.status_code, 200)
            load.assert_called_once_with(importer.SHUIXIAN_REPOSITORY, page=2, query="海报")
            invalid = self.client.post("/api/prompt-library/import-url", headers=self.headers, json={"url": importer.SHUIXIAN_REPOSITORY, "page": 0})
            self.assertEqual(invalid.status_code, 422)


if __name__ == "__main__":
    unittest.main()

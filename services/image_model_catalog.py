"""Discover image model names separately from the ChatGPT chat-model endpoint."""
from __future__ import annotations

import json
import re
import time
from html import unescape
from threading import Lock

from curl_cffi import requests

from services.config import DATA_DIR
from services.proxy_service import proxy_settings

CATALOG_URL = "https://developers.openai.com/api/reference/resources/images/methods/generate"
CATALOG_CACHE_FILE = DATA_DIR / "image_model_catalog.json"
CATALOG_TTL_SECONDS = 6 * 60 * 60
IMAGE_MODEL_PATTERN = r"(?:gpt-image-\d+(?:\.\d+)?(?:-[a-z0-9]+)*|chatgpt-image-latest)"
_lock = Lock()
_models: set[str] = set()
_expires_at = 0.0


def parse_image_models(html: str) -> set[str]:
    # Only the supported-model paragraph: the page also mentions retired models.
    paragraph = re.search(r"Supported models include\b(.*?)</p>", html, re.DOTALL | re.IGNORECASE)
    if paragraph is None:
        raise ValueError("image catalog does not contain a supported-model list")
    text = unescape(re.sub(r"<[^>]+>", " ", paragraph.group(1)))
    models = set(re.findall(r"\b" + IMAGE_MODEL_PATTERN + r"\b", text))
    if not models:
        raise ValueError("image catalog contains no image models")
    return models


def image_model_aliases(models: set[str]) -> dict[str, str]:
    return {
        model.removesuffix("-sunburst"): model
        for model in models
        if model.endswith("-sunburst") and model.removesuffix("-sunburst") not in models
    }


def cached_image_model_aliases() -> dict[str, str]:
    # Request routing must never perform catalog network I/O.
    return image_model_aliases(_models)


def get_image_models(*, refresh: bool = False) -> set[str]:
    global _models, _expires_at
    with _lock:
        if not refresh and time.time() < _expires_at:
            return set(_models)
        if not _models:
            try:
                saved = json.loads(CATALOG_CACHE_FILE.read_text(encoding="utf-8"))
                _models = {
                    model for model in saved["models"]
                    if isinstance(model, str) and re.fullmatch(IMAGE_MODEL_PATTERN, model)
                }
                if not refresh and _models and time.time() < float(saved["expires_at"]):
                    _expires_at = float(saved["expires_at"])
                    return set(_models)
            except (OSError, ValueError, KeyError, TypeError):
                pass
        try:
            with requests.Session(**proxy_settings.build_session_kwargs(impersonate="chrome", verify=True)) as session:
                response = session.get(CATALOG_URL, timeout=15)
                response.raise_for_status()
                discovered = parse_image_models(response.text)
            _models = discovered
            _expires_at = time.time() + CATALOG_TTL_SECONDS
            try:
                CATALOG_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
                temporary = CATALOG_CACHE_FILE.with_suffix(".tmp")
                temporary.write_text(json.dumps({"models": sorted(_models), "expires_at": _expires_at}), encoding="utf-8")
                temporary.replace(CATALOG_CACHE_FILE)
            except OSError:
                pass
        except Exception:
            # Keep the last successful catalog; retry soon after a transient failure.
            _expires_at = time.time() + 60
        return set(_models)

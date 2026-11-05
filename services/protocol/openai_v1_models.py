from __future__ import annotations

import copy
import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from typing import Any

from services.account_service import account_service
from services.image_model_catalog import get_image_models, image_model_aliases
from services.openai_backend_api import OpenAIBackendAPI
from utils.helper import IMAGE_MODEL_PLAN_TYPES, WEB_IMAGE_MODELS, is_supported_image_model

MODEL_CACHE_TTL_SECONDS = 5 * 60
_model_cache: dict[str, Any] | None = None
_model_cache_expires_at = 0.0
_model_cache_accounts = ""
_model_cache_lock = Lock()


def _model_entry(model_id: str, **metadata: Any) -> dict[str, Any]:
    return {
        "id": model_id, "object": "model", "created": 0,
        "owned_by": "chatgpt2api", "permission": [], "root": model_id,
        "parent": None, **metadata,
    }


def _account_fingerprint(accounts: list[dict]) -> str:
    values = sorted(
        repr(tuple(account.get(key) for key in ("access_token", "source_type", "type", "status", "proxy")))
        for account in accounts if isinstance(account, dict)
    )
    return hashlib.sha256("\n".join(values).encode()).hexdigest()


def _fetch_upstream_models(accounts: list[dict]) -> dict[str, Any]:
    tokens = list(dict.fromkeys(
        str(account["access_token"])
        for account in accounts
        if isinstance(account, dict) and account.get("access_token")
        and account.get("status") not in {"禁用", "异常"}
        and account_service._normalize_source_type(account.get("source_type")) != "gptfree"
    ))

    def fetch(token: str) -> list[dict] | None:
        backend = None
        try:
            backend = OpenAIBackendAPI(access_token=token) if token else OpenAIBackendAPI()
            data = backend.list_models().get("data")
            return data if isinstance(data, list) else None
        except Exception:
            return None
        finally:
            if backend is not None:
                backend.close()

    entries: dict[str, dict] = {}
    successful = False
    if tokens:
        with ThreadPoolExecutor(max_workers=min(len(tokens), 4)) as executor:
            for data in executor.map(fetch, tokens):
                if data is None:
                    continue
                successful = True
                for item in data:
                    if isinstance(item, dict) and (model_id := str(item.get("id") or "").strip()):
                        entries.setdefault(model_id, item)
    if not successful:
        anonymous = fetch("")
        if anonymous is None:
            raise RuntimeError("unable to fetch upstream models")
        for item in anonymous:
            if isinstance(item, dict) and (model_id := str(item.get("id") or "").strip()):
                entries.setdefault(model_id, item)
    return {"object": "list", "data": list(entries.values())}


def _get_upstream_models(*, refresh: bool = False) -> dict[str, Any]:
    global _model_cache, _model_cache_expires_at, _model_cache_accounts
    accounts = account_service.list_accounts()
    fingerprint = _account_fingerprint(accounts)
    with _model_cache_lock:
        if not refresh and fingerprint == _model_cache_accounts and time.monotonic() < _model_cache_expires_at:
            return copy.deepcopy(_model_cache)
        try:
            result = _fetch_upstream_models(accounts)
        except Exception:
            if _model_cache is None or fingerprint != _model_cache_accounts:
                raise
            _model_cache_expires_at = time.monotonic() + 60
            return copy.deepcopy(_model_cache)
        _model_cache = copy.deepcopy(result)
        _model_cache_accounts = fingerprint
        _model_cache_expires_at = time.monotonic() + MODEL_CACHE_TTL_SECONDS
        return copy.deepcopy(result)


def _clear_model_cache() -> None:
    global _model_cache, _model_cache_expires_at, _model_cache_accounts
    with _model_cache_lock:
        _model_cache = None
        _model_cache_expires_at = 0.0
        _model_cache_accounts = ""


def list_models(*, refresh: bool = False) -> dict[str, Any]:
    result = _get_upstream_models(refresh=refresh)
    entries = {
        str(item["id"]): item for item in result.get("data", [])
        if isinstance(item, dict) and item.get("id")
    }
    accounts = [
        account for account in account_service.list_accounts()
        if isinstance(account, dict) and account.get("status") not in {"禁用", "异常"}
    ]
    if accounts:
        # The chat-model endpoint omits image engines; catalog membership does
        # not establish an individual account's permission to use the model.
        discovered_images = get_image_models(refresh=refresh)
        image_models = discovered_images or set(WEB_IMAGE_MODELS)
        aliases = image_model_aliases(image_models)
        for model in sorted(image_models | aliases.keys()):
            entries.setdefault(model, _model_entry(
                model, kind="image", availability="catalog",
                discovery_source="openai_image_catalog" if discovered_images else "compatibility_fallback",
                **({"alias_for": aliases[model]} if model in aliases else {}),
            ))
        for account in accounts:
            source = account_service._normalize_source_type(account.get("source_type"))
            plan = account_service._normalize_account_type(account.get("type"))
            if source == "codex" and plan and plan.lower() in IMAGE_MODEL_PLAN_TYPES:
                for model in image_models | aliases.keys():
                    for alias in (f"codex-{model}", f"{plan.lower()}-codex-{model}"):
                        entries.setdefault(alias, _model_entry(alias, kind="image", availability="catalog"))
            elif source == "gptfree":
                entries.setdefault("gptfree", _model_entry("gptfree", kind="text", availability="alias"))
                for item in list(entries.values()):
                    model = item["id"]
                    if item.get("availability") == "account" and not is_supported_image_model(model):
                        alias = f"gptfree/{model}"
                        entries.setdefault(alias, _model_entry(alias, kind="text", availability="alias"))
    return {"object": "list", "data": sorted(entries.values(), key=lambda item: (item["id"] != "auto", item["id"]))}

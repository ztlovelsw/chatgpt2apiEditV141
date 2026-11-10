from __future__ import annotations

import ipaddress
import json
import re
import socket
import time
from threading import Lock
from typing import Any
from urllib.parse import parse_qs, quote, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag
from curl_cffi import CurlOpt, requests
from fastapi import HTTPException
from markdown_it import MarkdownIt

from services.proxy_service import proxy_settings

MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_IMPORT_ITEMS = 100
MAX_SUBSCRIPTION_ITEMS = 20000
MAX_EXAMPLE_IMAGES = 12
PROMPT_LABEL = re.compile(r"^(?:提示词|正向提示词|prompt|positive prompt)(?:\s*[：:]\s*|\s*$)", re.I)
PROMPT_SELECTOR = '[data-prompt], .prompt, .prompt-text, .prompt-content, #prompt, textarea'
SHUIXIAN_REPOSITORY = "https://github.com/BaYue-SYJ/shuixian-prompts"
SHUIXIAN_DATA_BASE = "https://raw.githubusercontent.com/BaYue-SYJ/shuixian-prompts/main/shuixian-unified/data/"
SHUIXIAN_IMAGE_BASE = "https://r2.qqsrc.com/"
SHUIXIAN_PART_BYTES = 8 * 1024 * 1024
SHUIXIAN_CACHE_SECONDS = 300
_shuixian_cache: dict[int, tuple[float, list[dict[str, Any]]]] = {}
_shuixian_lock = Lock()


def _error(message: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": message})


def normalize_http_url(value: str) -> str:
    """Validate URLs used for source links and browser image previews."""
    value = value.strip()
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise _error("链接格式无效，请填写完整的 HTTP 或 HTTPS 地址") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or "\\" in value
        or any(ord(char) <= 32 for char in value)
    ):
        raise _error("请填写不含账号密码的 HTTP 或 HTTPS 链接")
    try:
        host = parsed.hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise _error("链接域名格式无效") from exc
    authority = f"[{host}]" if ":" in host else host
    if port is not None:
        authority += f":{port}"
    return urlunsplit((parsed.scheme, authority, parsed.path or "/", parsed.query, parsed.fragment))


def normalize_example_images(values: list[str], base_url: str = "") -> list[str]:
    images: list[str] = []
    for value in values:
        url = normalize_http_url(urljoin(base_url, value.strip()))
        if url not in images:
            images.append(url)
    if len(images) > MAX_EXAMPLE_IMAGES:
        raise _error(f"每条提示词最多保存 {MAX_EXAMPLE_IMAGES} 张示例图")
    return images


def _public_addresses(url: str) -> tuple[str, int, list[str]]:
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        addresses = list(dict.fromkeys(info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))
    except socket.gaierror as exc:
        raise _error("无法解析链接域名，请检查地址或网络", 502) from exc
    if not addresses or any(not ipaddress.ip_address(address).is_global or ipaddress.ip_address(address).is_multicast for address in addresses):
        raise _error("仅支持公开网页链接，不能访问本机或内网地址")
    return host, port, addresses


def _download_document(url: str, *, max_bytes: int = MAX_DOCUMENT_BYTES) -> tuple[str, str, str]:
    """Bound downloads and validate every redirect before making a request."""
    current = url
    for _ in range(5):
        current = normalize_http_url(current)
        host, port, addresses = _public_addresses(current)
        resolved = ",".join(f"[{address}]" if ":" in address else address for address in addresses)
        try:
            with requests.Session(
                trust_env=False,
                curl_options={CurlOpt.RESOLVE: [f"{host}:{port}:{resolved}"]},
                **proxy_settings.build_session_kwargs(resource=True),
            ) as session:
                response = session.get(
                    current,
                    headers={"User-Agent": "Mozilla/5.0 (compatible; ChatGPT2API-PromptImporter/1.0)",
                             "Accept": "text/html, text/plain, text/markdown, application/json"},
                    timeout=(10, 30),
                    allow_redirects=False,
                    stream=True,
                )
                try:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise _error("网页返回了无效的跳转地址", 502)
                        current = urljoin(current, location)
                        continue
                    if not 200 <= response.status_code < 300:
                        raise _error(f"拉取失败：目标网站返回 HTTP {response.status_code}，请使用可公开访问的页面", 502)
                    mime = response.headers.get("content-type", "").split(";", 1)[0].lower()
                    if mime and not (mime.startswith("text/") or mime in {"application/json", "application/ld+json", "application/xhtml+xml", "application/octet-stream"}):
                        raise _error("该链接不是网页、Markdown、文本或 JSON 文件")
                    content = bytearray()
                    for chunk in response.iter_content():
                        content.extend(chunk)
                        if len(content) > max_bytes:
                            raise _error(f"页面内容超过 {max_bytes // (1024 * 1024)}MB，请使用具体提示词页面或原始文件链接")
                    # Prefer an explicit HTTP charset; otherwise recognize HTML meta and UTF-8.
                    charset = re.search(r"charset\s*=\s*[\"']?([\w-]+)", response.headers.get("content-type", ""), re.I)
                    if charset is None:
                        charset = re.search(r"charset\s*=\s*[\"']?([\w-]+)", bytes(content[:4096]).decode("ascii", errors="ignore"), re.I)
                    encoding = charset.group(1) if charset else "utf-8-sig"
                    try:
                        text = content.decode(encoding, errors="replace")
                    except LookupError:
                        text = content.decode("utf-8-sig", errors="replace")
                    return text, mime, current
                finally:
                    response.close()
        except requests.RequestsError as exc:
            raise _error("拉取网页失败，请检查链接或网络后重试", 502) from exc
    raise _error("页面跳转次数过多，请填写最终页面地址")


def _image_urls(values: Any, base_url: str) -> list[str]:
    if not isinstance(values, list):
        values = [values]
    result: list[str] = []
    for value in values:
        if isinstance(value, dict):
            value = value.get("url") or value.get("contentUrl") or value.get("src")
        if not isinstance(value, str) or not value.strip():
            continue
        try:
            url = normalize_http_url(urljoin(base_url, value.strip()))
        except HTTPException:
            continue
        if url not in result:
            result.append(url)
    return result[:MAX_EXAMPLE_IMAGES]


def _draft(content: str, title: str, images: list[str], source_url: str, **extra: str) -> dict[str, Any]:
    return {
        "name": (" ".join(title.split()) or content.strip()[:24])[:120],
        "type": extra.get("type", "默认"),
        "content": content.strip(),
        "note": extra.get("note", "")[:500],
        "source_url": source_url,
        "example_images": images,
    }


def _json_drafts(value: Any, base_url: str, source_url: str, *, embedded: bool = False, max_items: int = MAX_IMPORT_ITEMS) -> list[dict[str, Any]]:
    drafts: list[dict[str, Any]] = []
    pending = [value]
    while pending and len(drafts) < max_items:
        item = pending.pop()
        if isinstance(item, list):
            pending.extend(reversed(item))
        elif isinstance(item, dict):
            content = item.get("prompt") or item.get("prompt_text") or item.get("positive_prompt")
            if not content and not embedded:
                content = item.get("content")
            if isinstance(content, str) and content.strip():
                images = item.get("example_images") or item.get("images") or item.get("image") or item.get("image_url") or []
                drafts.append(_draft(
                    content, str(item.get("name") or item.get("title") or ""),
                    _image_urls(images, base_url), source_url,
                    type=str(item.get("type") or "默认"), note=str(item.get("note") or ""),
                ))
                if item.get("id") is not None:
                    drafts[-1]["source_id"] = str(item["id"])
                elif not (item.get("name") or item.get("title")):
                    drafts[-1]["source_id"] = f"position:{len(drafts)}"
            else:
                pending.extend(reversed([child for child in item.values() if isinstance(child, (dict, list))]))
    return drafts


def _node_text(node: Tag) -> str:
    if node.name in {"pre", "textarea"}:
        return node.get_text().strip()
    return node.get_text("\n", strip=True)


def _html_images(root: Tag, base_url: str) -> list[str]:
    values = []
    for image in root.select("img"):
        if any(str(image.get(dimension, "")).isdigit() and int(image[dimension]) < 64 for dimension in ("width", "height")):
            continue
        value = image.get("data-src") or image.get("data-original") or image.get("src")
        if not value and image.get("srcset"):
            value = str(image["srcset"]).split(",")[-1].strip().split()[0]
        values.append(value)
    return _image_urls(values, base_url)


def _html_drafts(text: str, base_url: str, source_url: str, *, max_items: int = MAX_IMPORT_ITEMS) -> tuple[list[dict[str, Any]], list[str]]:
    soup = BeautifulSoup(text, "html.parser")
    title = _node_text(soup.title) if soup.title else ""
    metadata_images = _image_urls([
        tag.get("content") for tag in soup.select('meta[property="og:image"], meta[name="twitter:image"]')
    ], base_url)
    embedded: list[dict[str, Any]] = []
    for script in soup.select('script[type="application/json"], script[type="application/ld+json"]'):
        try:
            embedded.extend(_json_drafts(json.loads(script.get_text()), base_url, source_url, embedded=True, max_items=max_items))
        except (ValueError, RecursionError):
            continue
    for node in soup.select("script, style, nav, header, footer, aside, button, noscript, [hidden], [aria-hidden='true']"):
        node.decompose()
    root = soup.select_one("article, .markdown-body, main, [role='main']") or soup.body or soup
    candidates = root.select(f"{PROMPT_SELECTOR}, pre, blockquote")
    # Keep the enclosing block once, including multiline code and blockquotes.
    candidate_ids = {id(node) for node in candidates}
    candidates = [node for node in candidates if not any(id(parent) in candidate_ids for parent in node.parents)]
    drafts = list(embedded)
    for node in candidates:
        content = str(node.get("data-prompt") or _node_text(node)).strip()
        content = PROMPT_LABEL.sub("", content).strip()
        if not content:
            continue
        heading = node.find_previous(re.compile(r"^h[1-6]$"))
        name = _node_text(heading) if heading else title
        if PROMPT_LABEL.fullmatch(name):
            heading = heading.find_previous(re.compile(r"^h[1-6]$")) if heading else None
            name = _node_text(heading) if heading else title
        # A card/section or heading interval keeps different prompts' examples apart.
        images: list[str] = []
        for parent in node.parents:
            if parent is root:
                break
            if sum(1 for candidate in candidates if any(ancestor is parent for ancestor in candidate.parents)) > 1:
                break
            images = _html_images(parent, base_url)
            if images:
                break
        if not images:
            scope = BeautifulSoup("", "html.parser")
            start = heading or node
            for sibling in start.next_siblings:
                if isinstance(sibling, Tag):
                    if re.fullmatch(r"h[1-6]", sibling.name) and (heading is None or int(sibling.name[1]) <= int(heading.name[1])) and not PROMPT_LABEL.fullmatch(_node_text(sibling)):
                        break
                    scope.append(BeautifulSoup(str(sibling), "html.parser"))
            images = _html_images(scope, base_url)
        drafts.append(_draft(content, name, images, source_url))
        if not name:
            drafts[-1]["source_id"] = f"block:{len(drafts)}"
        if len(drafts) >= max_items:
            break

    if not drafts:
        for heading in root.find_all(re.compile(r"^h[1-6]$")):
            if not PROMPT_LABEL.fullmatch(_node_text(heading)):
                continue
            paragraphs: list[str] = []
            for sibling in heading.next_siblings:
                if isinstance(sibling, Tag):
                    if re.fullmatch(r"h[1-6]", sibling.name):
                        break
                    if sibling.name == "p":
                        paragraphs.append(_node_text(sibling))
            content = "\n\n".join(part for part in paragraphs if part)
            if content:
                name = heading.find_previous(re.compile(r"^h[1-6]$"))
                drafts.append(_draft(content, _node_text(name) if name else title, [], source_url))

    if not drafts:
        # A labeled paragraph is common on hand-written tutorial pages.
        for node in root.select("p"):
            content = node.get_text(" ", strip=True)
            if PROMPT_LABEL.match(content):
                content = PROMPT_LABEL.sub("", content).strip()
                if not content:
                    following = node.find_next_sibling()
                    content = _node_text(following) if following and following.name == "p" else ""
                if content:
                    drafts.append(_draft(content, title, [], source_url))
    warnings = []
    if not drafts:
        raise _error("未找到可识别的提示词。请使用包含提示词正文的公开页面、Markdown 或 JSON 链接；登录页和仅靠脚本加载的内容暂不支持")
    if len(drafts) == 1 and not drafts[0]["example_images"]:
        drafts[0]["example_images"] = _html_images(root, base_url) or metadata_images
    return drafts, warnings


def parse_prompt_document(text: str, content_type: str, base_url: str, source_url: str, *, max_items: int = MAX_IMPORT_ITEMS) -> dict[str, Any]:
    stripped = text.lstrip()
    warnings: list[str] = []
    is_json = content_type in {"application/json", "application/ld+json"} or urlsplit(base_url).path.endswith(".json")
    value: Any = None
    if is_json or stripped.startswith(("{", "[")):
        try:
            value = json.loads(text)
            is_json = True
        except (ValueError, RecursionError) as exc:
            if is_json:
                raise _error("链接返回的 JSON 格式无效") from exc
    if is_json:
        drafts = _json_drafts(value, base_url, source_url, max_items=max_items)
    elif content_type in {"text/html", "application/xhtml+xml"} or re.match(r"(?is)\s*(?:<!doctype\s+html|<html|<head|<body|<article|<main)", text):
        drafts, warnings = _html_drafts(text, base_url, source_url, max_items=max_items)
    else:
        rendered = MarkdownIt("commonmark", {"html": True}).render(text)
        try:
            drafts, warnings = _html_drafts(rendered, base_url, source_url, max_items=max_items)
        except HTTPException:
            root = BeautifulSoup(rendered, "html.parser")
            heading = root.find(re.compile(r"^h[1-6]$"))
            title = _node_text(heading) if heading else ""
            images = _html_images(root, base_url)
            for image in root.select("img"):
                image.decompose()
            if heading:
                heading.decompose()
            content = root.get_text("\n", strip=True)
            drafts = [{**_draft(content, title, images, source_url), "source_id": "document"}] if content else []
            warnings.append("未发现独立的提示词区块，已提取文本，请核对后保存。")
    unique = {(item.get("source_id") or item["content"]): item for item in reversed(drafts) if item["content"].strip()}
    items = list(reversed(unique.values()))[:max_items]
    if not items:
        raise _error("链接中没有找到提示词内容")
    if len(items) == max_items:
        warnings.append(f"本次最多展示前 {max_items} 条提示词，请使用具体页面缩小范围。")
    if not any(item["example_images"] for item in items):
        warnings.append("页面中未找到示例效果图，可以手动补充图片链接。")
    return {"items": items, "warnings": warnings}


def _shuixian_target(url: str) -> tuple[list[int], str] | None:
    """Recognize the documented repository/data links and the site's detail links."""
    parsed = urlsplit(url)
    path = parsed.path.rstrip("/")
    if parsed.hostname == "prompt.qqsrc.com":
        if path not in {"", "/index.html", "/gallery.html", "/search.html", "/detail.html", "/classic", "/classic/index.html"}:
            return None
    elif parsed.hostname in {"github.com", "raw.githubusercontent.com"}:
        prefix = "/bayue-syj/shuixian-prompts"
        if not path.lower().startswith(prefix + "/") and path.lower() != prefix:
            return None
        suffix = path[len(prefix):]
        if suffix in {"", "/tree/main", "/tree/main/shuixian-unified", "/blob/main/README.md", "/main/README.md"}:
            pass
        else:
            part = re.fullmatch(r"/(?:blob/|raw/)?main/shuixian-unified/data/(?:prompts|list)\.part([123])\.json", suffix)
            if part is None:
                return None
            return [int(part[1])], ""
    else:
        return None
    parameters = {**parse_qs(parsed.fragment), **parse_qs(parsed.query)}
    return [1, 2, 3], parameters.get("id", [""])[0]


def _load_shuixian_part(part: int, *, refresh: bool = False) -> list[dict[str, Any]]:
    with _shuixian_lock:
        cached = _shuixian_cache.get(part)
        if not refresh and cached and time.monotonic() - cached[0] < SHUIXIAN_CACHE_SECONDS:
            return cached[1]
        text, _, _ = _download_document(
            f"{SHUIXIAN_DATA_BASE}prompts.part{part}.json", max_bytes=SHUIXIAN_PART_BYTES,
        )
        try:
            records = json.loads(text)
        except (ValueError, RecursionError) as exc:
            raise _error("水仙提示词数据格式无效，请稍后重试", 502) from exc
        if not isinstance(records, list):
            raise _error("水仙提示词数据格式已变化，请检查数据源", 502)
        items = []
        for record in records:
            if not isinstance(record, dict) or not isinstance(record.get("prompt"), str) or not record["prompt"].strip():
                continue
            record_id = str(record["id"])
            # The source puts the cover first, followed by any extra example images.
            images = _image_urls([record.get("image"), *(record.get("images") or [])], SHUIXIAN_IMAGE_BASE)
            item = _draft(
                record["prompt"], str(record.get("title") or ""), images,
                f"https://prompt.qqsrc.com/detail.html?id={quote(record_id, safe='')}",
                type=str(record.get("category") or "默认"),
                note=f"来源：水仙的AI提示词 · {SHUIXIAN_REPOSITORY}",
            )
            items.append({**item, "source_id": record_id})
        _shuixian_cache[part] = time.monotonic(), items
        return items


def _import_shuixian(parts: list[int], prompt_id: str, page: int, query: str) -> dict[str, Any]:
    items = [item for part in parts for item in _load_shuixian_part(part)]
    if prompt_id:
        items = [item for item in items if item["source_id"] == prompt_id]
        if not items:
            raise _error("没有找到这条水仙提示词，请检查详情链接中的 id", 404)
    keyword = query.strip().casefold()
    if keyword:
        items = [item for item in items if keyword in f'{item["name"]}\n{item["type"]}\n{item["content"]}'.casefold()]
    total = len(items)
    page = min(page, max(1, (total + MAX_IMPORT_ITEMS - 1) // MAX_IMPORT_ITEMS))
    start = (page - 1) * MAX_IMPORT_ITEMS
    return {
        "items": items[start:start + MAX_IMPORT_ITEMS],
        "warnings": [],
        "source_name": "水仙的AI提示词",
        "total": total,
        "page": page,
        "page_size": MAX_IMPORT_ITEMS,
        "has_more": start + MAX_IMPORT_ITEMS < total,
    }


def import_prompt_url(url: str, *, page: int = 1, query: str = "", max_items: int = MAX_IMPORT_ITEMS) -> dict[str, Any]:
    source_url = normalize_http_url(url)
    shuixian = _shuixian_target(source_url)
    if shuixian is not None:
        return _import_shuixian(*shuixian, page=max(1, page), query=query)
    parsed = urlsplit(source_url)
    download_url = source_url
    # GitHub file pages contain UI code; import the original document instead.
    parts = parsed.path.strip("/").split("/")
    if parsed.hostname == "github.com" and len(parts) >= 5 and parts[2] == "blob":
        download_url = "https://raw.githubusercontent.com/" + "/".join(parts[:2] + parts[3:])
    text, content_type, final_url = _download_document(download_url)
    return parse_prompt_document(text, content_type, final_url, source_url, max_items=max_items)


def fetch_prompt_subscription(url: str) -> dict[str, Any]:
    """Fetch a complete source snapshot; preview pagination does not limit subscriptions."""
    source_url = normalize_http_url(url)
    target = _shuixian_target(source_url)
    if target is not None:
        parts, prompt_id = target
        items = [item for part in parts for item in _load_shuixian_part(part, refresh=True)]
        if prompt_id:
            items = [item for item in items if item["source_id"] == prompt_id]
        if not items:
            raise _error("订阅来源没有可用的提示词")
        return {"items": items, "warnings": []}
    result = import_prompt_url(source_url, max_items=MAX_SUBSCRIPTION_ITEMS + 1)
    if len(result["items"]) > MAX_SUBSCRIPTION_ITEMS:
        raise _error(f"订阅超过 {MAX_SUBSCRIPTION_ITEMS} 条，请拆分为多个数据链接")
    return result


def normalize_subscription_url(url: str) -> str:
    source = normalize_http_url(url)
    target = _shuixian_target(source)
    if target is None:
        return source
    parts, prompt_id = target
    if prompt_id:
        return f"https://prompt.qqsrc.com/detail.html?id={quote(prompt_id, safe='')}"
    if len(parts) == 1:
        return f"{SHUIXIAN_DATA_BASE}prompts.part{parts[0]}.json"
    return SHUIXIAN_REPOSITORY

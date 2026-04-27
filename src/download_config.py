import copy
import json
import random
import re
import time
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

import yt_dlp


BILIBILI_COOKIE_KEYS = ("SESSDATA", "DedeUserID", "bili_jct")
BILIBILI_KEEP_QUERY_KEYS = {"p", "page"}
DEFAULT_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def is_bilibili_platform(platform: str) -> bool:
    return str(platform or "").lower() in {"bilibili", "b站"}


def is_bilibili_412(exc: Exception) -> bool:
    message = str(exc)
    return "HTTP Error 412" in message and "BiliBili" in message


def load_project_config(config_path: str | Path = "config.json", logger=None) -> dict:
    path = Path(config_path)
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            config = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        if logger:
            logger.warning(f"无法读取下载配置: {exc}")
        return {}
    return config if isinstance(config, dict) else {}


def sanitize_bilibili_url(video_url: str) -> str:
    parts = urlsplit(video_url)
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key in BILIBILI_KEEP_QUERY_KEYS
    ]
    path = parts.path or "/"
    return urlunsplit((parts.scheme, parts.netloc, path, urlencode(query), ""))


def _download_section(config: dict) -> dict:
    download_cfg = (config or {}).get("download", {}) or {}
    return download_cfg if isinstance(download_cfg, dict) else {}


def _platform_section(download_cfg: dict, platform: str) -> dict:
    platform_cfg = download_cfg.get(str(platform or "").lower(), {}) or {}
    return platform_cfg if isinstance(platform_cfg, dict) else {}


def _pick(download_cfg: dict, platform_cfg: dict, name: str):
    value = platform_cfg.get(name)
    if value in (None, "", [], {}):
        value = download_cfg.get(name)
    return value


def _cookie_text_has_login_state(text: str) -> bool:
    return any(key in (text or "") for key in BILIBILI_COOKIE_KEYS)


def _cookiefile_has_login_state(cookie_path: Path) -> bool:
    text = cookie_path.read_text(encoding="utf-8", errors="ignore")
    if _cookie_text_has_login_state(text):
        return True

    jar = MozillaCookieJar()
    try:
        jar.load(str(cookie_path), ignore_discard=True, ignore_expires=True)
    except Exception:
        return False
    names = {cookie.name for cookie in jar}
    return any(key in names for key in BILIBILI_COOKIE_KEYS)


def _normalized_browser_cookies(value):
    if not value:
        return None
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(value)
    return None


def _human_delay(min_seconds: float = 0.3, max_seconds: float = 1.2):
    time.sleep(random.uniform(min_seconds, max_seconds))


def _extract_cover_url(html: str) -> Optional[str]:
    patterns = (
        r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']+)["\']',
        r'<meta\s+content=["\']([^"\']+)["\']\s+property=["\']og:image["\']',
        r'"pic"\s*:\s*"([^"]+)"',
    )
    for pattern in patterns:
        match = re.search(pattern, html or "", re.IGNORECASE)
        if match:
            url = match.group(1).replace("\\/", "/")
            if url.startswith("//"):
                return "https:" + url
            return url
    return None


def _open_url(url: str, headers: dict, timeout: float = 8.0) -> bytes:
    request = Request(url, headers=headers)
    with urlopen(request, timeout=timeout) as response:
        return response.read(512 * 1024)


def preflight_bilibili(video_url: str, config: dict, logger=None):
    download_cfg = _download_section(config)
    if not bool(download_cfg.get("human_like", True)):
        return

    headers = dict(DEFAULT_BROWSER_HEADERS)
    configured_headers = download_cfg.get("http_headers")
    if isinstance(configured_headers, dict):
        headers.update(configured_headers)
    headers.setdefault("Referer", "https://www.bilibili.com/")

    try:
        if logger:
            logger.info("B站下载预热: 访问视频页面")
        html_bytes = _open_url(video_url, headers)
        html = html_bytes.decode("utf-8", errors="ignore")
        _human_delay(0.3, 1.2)

        cover_url = _extract_cover_url(html)
        if cover_url:
            cover_headers = dict(headers)
            cover_headers["Referer"] = video_url
            if logger:
                logger.info("B站下载预热: 访问封面资源")
            _open_url(cover_url, cover_headers, timeout=5.0)
            _human_delay(0.3, 1.2)
    except Exception as exc:
        if logger:
            logger.warning(f"B站下载预热失败，继续尝试下载: {exc}")


def apply_ydl_download_config(
    ydl_opts: dict,
    config: dict,
    platform: str,
    logger=None,
    prefer_browser_cookies: bool = False,
):
    download_cfg = _download_section(config)
    platform_cfg = _platform_section(download_cfg, platform)
    is_bilibili = is_bilibili_platform(platform)

    headers = _pick(download_cfg, platform_cfg, "http_headers")
    headers = dict(headers) if isinstance(headers, dict) else {}
    if is_bilibili:
        merged_headers = dict(DEFAULT_BROWSER_HEADERS)
        merged_headers.update(headers)
        headers = merged_headers

    cookiefile = _pick(download_cfg, platform_cfg, "cookiefile")
    if cookiefile and not prefer_browser_cookies:
        cookie_path = Path(str(cookiefile)).expanduser()
        if cookie_path.exists():
            cookie_is_valid = True
            if is_bilibili:
                cookie_is_valid = _cookiefile_has_login_state(cookie_path)
            if cookie_is_valid:
                ydl_opts["cookiefile"] = str(cookie_path)
            elif logger:
                logger.warning(
                    f"B站 cookies 文件缺少登录态字段 {BILIBILI_COOKIE_KEYS}，跳过: {cookie_path}"
                )
        elif logger:
            logger.warning(f"cookies 文件不存在，跳过: {cookie_path}")

    cookie_string = _pick(download_cfg, platform_cfg, "cookie_string")
    cookie_string_file = _pick(download_cfg, platform_cfg, "cookie_string_file")
    if cookie_string_file and not prefer_browser_cookies:
        cookie_string_path = Path(str(cookie_string_file)).expanduser()
        if cookie_string_path.exists():
            cookie_string = cookie_string_path.read_text(encoding="utf-8", errors="ignore").strip()
        elif logger:
            logger.warning(f"Cookie 字符串文件不存在，跳过: {cookie_string_path}")
    if cookie_string and not prefer_browser_cookies:
        if is_bilibili and not _cookie_text_has_login_state(str(cookie_string)):
            if logger:
                logger.warning(f"B站 Cookie 字符串缺少登录态字段 {BILIBILI_COOKIE_KEYS}，跳过")
        elif "Cookie" not in headers:
            headers["Cookie"] = str(cookie_string).strip()

    cookies_from_browser = _normalized_browser_cookies(_pick(download_cfg, platform_cfg, "cookies_from_browser"))
    if cookies_from_browser and (prefer_browser_cookies or "cookiefile" not in ydl_opts and "Cookie" not in headers):
        ydl_opts["cookiesfrombrowser"] = cookies_from_browser

    if headers:
        ydl_opts["http_headers"] = headers

    if bool(_pick(download_cfg, platform_cfg, "human_like")):
        ydl_opts["sleep_interval_requests"] = random.uniform(0.8, 1.6)
        ydl_opts["sleep_interval"] = random.uniform(1.0, 2.2)
        ydl_opts["max_sleep_interval"] = random.uniform(2.8, 5.0)
        _human_delay(0.3, 1.2)


def extract_info_with_recovery(
    video_url: str,
    ydl_opts: dict,
    config: dict,
    platform: str,
    logger=None,
    download: bool = True,
):
    is_bilibili = is_bilibili_platform(platform)
    target_url = sanitize_bilibili_url(video_url) if is_bilibili else video_url
    if is_bilibili and target_url != video_url and logger:
        logger.info(f"B站下载 URL 已清理: {target_url}")

    def run_attempt(prefer_browser_cookies: bool = False):
        opts = copy.deepcopy(ydl_opts)
        apply_ydl_download_config(
            opts,
            config,
            platform,
            logger=logger,
            prefer_browser_cookies=prefer_browser_cookies,
        )
        if is_bilibili:
            preflight_bilibili(target_url, config, logger=logger)
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(target_url, download=download)

    try:
        return run_attempt(prefer_browser_cookies=False)
    except Exception as exc:
        if not (is_bilibili and is_bilibili_412(exc)):
            raise
        if logger:
            logger.warning("B站下载触发 HTTP 412，执行一次浏览器态恢复重试")
        try:
            return run_attempt(prefer_browser_cookies=True)
        except Exception as retry_exc:
            raise_bilibili_download_error(retry_exc)


def raise_bilibili_download_error(exc: Exception):
    if is_bilibili_412(exc):
        raise RuntimeError(
            "B站下载失败: HTTP 412 Precondition Failed。已尝试页面预热和一次恢复重试，"
            "但仍被拒绝。请确认 B站 cookies 未过期且包含 SESSDATA/DedeUserID/bili_jct，"
            "或在 config.json 的 download.cookies_from_browser 配置 edge/chrome/firefox 后关闭对应浏览器再重试。"
        ) from exc
    raise exc

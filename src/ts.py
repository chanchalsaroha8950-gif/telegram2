import argparse
import base64
import re
import sys
from html import unescape as html_unescape
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urljoin
from urllib.request import Request, urlopen


DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)


def http_get(url: str, headers: Dict[str, str], timeout: int) -> str:
    req = Request(url, headers=headers, method="GET")
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def build_headers(user_agent: str, referer: Optional[str]) -> Dict[str, str]:
    headers: Dict[str, str] = {
        "User-Agent": user_agent or DEFAULT_UA,
        "Accept": "*/*",
        "Accept-Encoding": "identity",
        "Connection": "keep-alive",
    }
    if referer:
        headers["Referer"] = referer
        headers["Origin"] = referer
    return headers


def http_head_or_range_exists(url: str, headers: Dict[str, str], timeout: int) -> bool:
    # Use Range to avoid full download if HEAD not allowed
    h = dict(headers)
    h["Range"] = "bytes=0-0"
    try:
        req = Request(url, headers=h, method="GET")
        with urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 400
    except Exception:
        return False


def ts_url_to_m3u8_url(ts_url: str) -> Optional[str]:
    # Convert .../segment_001.ts (or any *.ts) to .../index.m3u8 in same directory
    if not ts_url.lower().endswith('.ts'):
        return None
    # Strip query if present
    main = ts_url.split('?', 1)[0]
    # Replace last path component with index.m3u8
    m = re.sub(r"/[^/]+\.ts$", "/index.m3u8", main)
    return m


def extract_bunny_embeds(page_html: str) -> List[str]:
    normalized = html_unescape(page_html).replace("\\/", "/")
    embeds: List[str] = []
    esc_matches = re.findall(r"https:\\/\\/play\\.bunnycdn\\.to\\/embed-3\\/([A-Za-z0-9_\-+=/]+)", page_html)
    for token in esc_matches:
        embeds.append(f"https://play.bunnycdn.to/embed-3/{token}")
    direct_matches = re.findall(r"https?://play\\.bunnycdn\\.to/embed-3/([A-Za-z0-9_\-+=/]+)", page_html)
    for token in direct_matches:
        embeds.append(f"https://play.bunnycdn.to/embed-3/{token}")
    direct_norm = re.findall(r"https?://play\.bunnycdn\.to/embed-3/([A-Za-z0-9_\-+=/]+)", normalized)
    for token in direct_norm:
        embeds.append(f"https://play.bunnycdn.to/embed-3/{token}")
    # decoded possibilities
    for token in set(esc_matches + direct_matches + direct_norm):
        try:
            decoded = base64.b64decode(token).decode("utf-8", errors="ignore")
            decoded = html_unescape(decoded)
            if decoded.startswith("http"):
                embeds.append(decoded)
        except Exception:
            pass
    # Additionally, sometimes a decoded token is itself an m3u8 URL; include it
    # dedupe
    seen = set()
    uniq: List[str] = []
    for u in embeds:
        if u not in seen:
            uniq.append(u)
            seen.add(u)
    return uniq


def extract_first_m3u8_from_html(html: str) -> Optional[str]:
    m = re.search(r"https?://[^'\"\s>]+\.m3u8", html)
    return m.group(0) if m else None


def extract_m3u8_from_jwplayer(html: str) -> Optional[str]:
    # Look for jwplayer setup with sources: [{file:"...m3u8"}]
    m = re.search(r"sources\s*:\s*\[\s*\{[^}]*file\s*:\s*['\"](https?://[^'\"\s]+\.m3u8)['\"]", html)
    if m:
        return m.group(1)
    m = re.search(r"file\s*:\s*['\"](https?://[^'\"\s]+\.m3u8)['\"]", html)
    return m.group(1) if m else None


def extract_first_ts_from_html(html: str) -> Optional[str]:
    m = re.search(r"https?://[^'\"\s>]+\.ts", html)
    return m.group(0) if m else None


def find_external_scripts(html: str, base_url: str) -> List[str]:
    urls: List[str] = []
    for m in re.finditer(r"<script[^>]+src=['\"]([^'\"]+)['\"]", html, flags=re.IGNORECASE):
        src = m.group(1)
        if src.startswith("http"):
            urls.append(src)
        else:
            urls.append(urljoin(base_url, src))
    return urls


def guess_ts_from_episode_url(page_url: str, timeout: int, user_agent: str) -> Optional[str]:
    # Derive slug and episode from AnimixPlay URL and probe common CDN path
    # Example: https://animixplay.name/v1/naruto-shippuuden-dub/ep465
    m = re.search(r"/v1/([^/]+)/ep(\d+)$", page_url)
    if not m:
        return None
    slug = m.group(1)
    ep = m.group(2)
    base_host = "https://hlsx3cdn.echovideo.to"
    qualities = ["1080", "720", "480", "360"]
    headers = build_headers(user_agent, base_host)
    for q in qualities:
        candidate = f"{base_host}/{slug}/{ep}/{q}/segment_001.ts"
        if http_head_or_range_exists(candidate, headers, timeout):
            return candidate
    return None


def guess_m3u8_from_episode_url(page_url: str, timeout: int, user_agent: str) -> Optional[str]:
    m = re.search(r"/v1/([^/]+)/ep(\d+)$", page_url)
    if not m:
        return None
    slug = m.group(1)
    ep = m.group(2)
    base_host = "https://hlsx3cdn.echovideo.to"
    qualities = ["1080", "720", "480", "360"]
    headers = build_headers(user_agent, base_host)
    # Try index.m3u8 directly per quality
    for q in qualities:
        m3u8 = f"{base_host}/{slug}/{ep}/{q}/index.m3u8"
        try:
            _ = http_get(m3u8, headers, timeout)
            return m3u8
        except Exception:
            continue
    # fallback: find a working segment then convert
    ts = guess_ts_from_episode_url(page_url, timeout, user_agent)
    if ts:
        conv = ts_url_to_m3u8_url(ts)
        if conv:
            return conv
    return None


def parse_master_variants(content: str, base_url: str) -> List[str]:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    variants: List[str] = []
    i = 0
    while i < len(lines):
        if lines[i].startswith("#EXT-X-STREAM-INF"):
            j = i + 1
            while j < len(lines) and lines[j].startswith("#"):
                j += 1
            if j < len(lines):
                variants.append(urljoin(base_url, lines[j]))
            i = j
        else:
            i += 1
    return variants


def parse_media_first_ts(content: str, base_url: str) -> Optional[str]:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    for line in lines:
        if line.startswith("#"):
            continue
        if line.endswith(".ts"):
            return urljoin(base_url, line)
    # Sometimes media URIs don't end with .ts; still return first non-tag line
    for line in lines:
        if not line.startswith("#"):
            return urljoin(base_url, line)
    return None


def find_m3u8_from_page(page_url: str, timeout: int, user_agent: str) -> Optional[str]:
    # Fetch AnimixPlay page
    page_html = http_get(page_url, build_headers(user_agent, page_url), timeout)
    embeds = extract_bunny_embeds(page_html)
    if not embeds:
        return None
    # If any embed already is a direct m3u8, use it
    for e in embeds:
        if e.endswith(".m3u8"):
            m3u8 = e
            return m3u8
    else:
        # Use first embed
        embed_url = embeds[0]
        # Prefer Bunny referer for embed/m3u8 requests
        embed_headers = build_headers(user_agent, "https://play.bunnycdn.to/")
        embed_html = http_get(embed_url, embed_headers, timeout)
        # Try to get m3u8 directly from embed HTML
        m3u8 = extract_first_m3u8_from_html(embed_html) or extract_m3u8_from_jwplayer(embed_html)
        # If no m3u8, try to pick .ts directly from embed HTML
        if not m3u8:
            ts_direct = extract_first_ts_from_html(embed_html)
            if ts_direct:
                # Convert ts to m3u8 if possible
                conv = ts_url_to_m3u8_url(ts_direct)
                if conv:
                    return conv
        # Also, search external script files for m3u8 or ts
        script_urls = find_external_scripts(embed_html, embed_url)
        for s in script_urls:
            try:
                js = http_get(s, embed_headers, timeout)
            except Exception:
                continue
            m3u8 = extract_first_m3u8_from_html(js) or extract_m3u8_from_jwplayer(js)
            if m3u8:
                return m3u8
            ts_direct = extract_first_ts_from_html(js)
            if ts_direct:
                conv = ts_url_to_m3u8_url(ts_direct)
                if conv:
                    return conv
        if not m3u8:
            # Sometimes the episode page itself exposes m3u8
            m3u8 = extract_first_m3u8_from_html(page_html) or extract_m3u8_from_jwplayer(page_html)
            if m3u8:
                return m3u8
        if not m3u8:
            # Finally, search episode page and its external scripts for .ts
            ts_direct = extract_first_ts_from_html(page_html)
            if ts_direct:
                conv = ts_url_to_m3u8_url(ts_direct)
                if conv:
                    return conv
            page_scripts = find_external_scripts(page_html, page_url)
            for s in page_scripts:
                try:
                    js = http_get(s, build_headers(user_agent, page_url), timeout)
                except Exception:
                    continue
                ts_direct = extract_first_ts_from_html(js)
                if ts_direct:
                    conv = ts_url_to_m3u8_url(ts_direct)
                    if conv:
                        return conv
    return None


def resolve_from_ts_input(ts_input_url: str, timeout: int, user_agent: str) -> str:
    # Fast-path: convert ts to m3u8 and validate
    m3u8 = ts_url_to_m3u8_url(ts_input_url)
    if not m3u8:
        raise RuntimeError("Input is not a .ts URL")
    headers = build_headers(user_agent, ts_input_url)
    if not http_head_or_range_exists(m3u8, headers, timeout):
        # Some servers may not allow range on m3u8; try GET small read
        try:
            _ = http_get(m3u8, headers, timeout)
        except Exception as e:
            raise RuntimeError(f"Derived m3u8 not accessible: {m3u8}") from e
    return m3u8


def main() -> None:
    parser = argparse.ArgumentParser(description="Print index.m3u8 from .ts or episode URL; fallback to first .ts if only that is discoverable")
    parser.add_argument("url", help="AnimixPlay episode URL (or a .ts segment URL)")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--user-agent", default=DEFAULT_UA)
    args = parser.parse_args()

    try:
        if args.url.lower().endswith('.ts'):
            # Convert .ts -> index.m3u8
            m3u8 = resolve_from_ts_input(args.url, args.timeout, args.user_agent)
            print(m3u8)
        else:
            # Try to resolve m3u8 directly from episode page
            m3u8 = find_m3u8_from_page(args.url, args.timeout, args.user_agent)
            if not m3u8:
                # Guess CDN path for m3u8
                m3u8 = guess_m3u8_from_episode_url(args.url, args.timeout, args.user_agent)
            if m3u8:
                print(m3u8)
            else:
                # Absolute fallback: get first ts (legacy behavior)
                ts_fallback = guess_ts_from_episode_url(args.url, args.timeout, args.user_agent)
                if ts_fallback:
                    print(ts_url_to_m3u8_url(ts_fallback) or ts_fallback)
                else:
                    raise RuntimeError("Could not resolve m3u8 or ts for this episode")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()



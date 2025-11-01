#!/usr/bin/env python3
"""
Usage:
  python extract_m3u8_ts.py "https://anizone.to/anime/spjuxray/1"
  python extract_m3u8_ts.py "https://example.com/stream/playlist.m3u8"
Outputs: prints only found .m3u8 or .ts URLs (one per line). No cookies used.
"""

import sys
import re
from urllib.parse import urljoin
import requests

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
HEADERS = {"User-Agent": USER_AGENT, "Accept": "*/*"}

M3U8_PATTERN = re.compile(r"""(?:"|')(https?://[^"'\\\s]*?\.m3u8(?:\?[^"'\\\s]*)?)(?:"|')""", re.IGNORECASE)
M3U8_PATTERN_PLAIN = re.compile(r"""(https?://[^\s"'<>]*?\.m3u8(?:\?[^\s"'<>]*)?)""", re.IGNORECASE)
TS_PATTERN = re.compile(r"""(?:"|')(https?://[^"'\\\s]*?\.ts(?:\?[^"'\\\s]*)?)(?:"|')""", re.IGNORECASE)
REL_M3U8_PATTERN = re.compile(r"""(?:"|')((?:\/|[a-zA-Z0-9_\-\.\/]+?\.m3u8(?:\?[^"'\\\s]*)?))(?:["']|\s)""", re.IGNORECASE)
REL_TS_PATTERN = re.compile(r"""(?:"|')((?:\/|[a-zA-Z0-9_\-\.\/]+?\.ts(?:\?[^"'\\\s]*)?))(?:["']|\s)""", re.IGNORECASE)

def fetch_text(url):
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.text, r.url  # return final URL (after redirects) for base resolution

def find_absolute_urls_in_text(text, base_url):
    found = []
    # absolute m3u8
    for m in M3U8_PATTERN.finditer(text):
        found.append(m.group(1))
    for m in M3U8_PATTERN_PLAIN.finditer(text):
        found.append(m.group(1))
    # absolute ts
    for m in TS_PATTERN.finditer(text):
        found.append(m.group(1))
    # relative m3u8/ts
    for m in REL_M3U8_PATTERN.finditer(text):
        rel = m.group(1)
        found.append(urljoin(base_url, rel))
    for m in REL_TS_PATTERN.finditer(text):
        rel = m.group(1)
        found.append(urljoin(base_url, rel))
    # also look for raw occurrences without quotes (rare)
    for ext in (".m3u8", ".ts"):
        for m in re.finditer(rf"(https?://[^\s\"'<>]*{re.escape(ext)}(?:\?[^\s\"'<>]*)?)", text, flags=re.IGNORECASE):
            found.append(m.group(1))
    # deduplicate while preserving order
    seen = set()
    out = []
    for u in found:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out

def main():
    if len(sys.argv) < 2:
        print("Usage: python extract_m3u8_ts.py <url>")
        sys.exit(1)
    url = sys.argv[1].strip()
    try:
        # If URL itself ends with .m3u8 or .ts, just print and exit
        if re.search(r"\.m3u8($|\?)", url, re.IGNORECASE) or re.search(r"\.ts($|\?)", url, re.IGNORECASE):
            print(url)
            return

        text, final = fetch_text(url)
        results = find_absolute_urls_in_text(text, base_url=final)

        # If nothing found in HTML, try scanning inline scripts and small heuristic
        if not results:
            # scan for <script>...</script> blocks
            scripts = re.findall(r"<script[^>]*>(.*?)</script>", text, flags=re.S|re.I)
            for s in scripts:
                more = find_absolute_urls_in_text(s, base_url=final)
                for u in more:
                    if u not in results:
                        results.append(u)

        # print only .m3u8 or .ts (filter just in case)
        out_filtered = [u for u in results if u.lower().endswith(".m3u8") or u.lower().split("?")[0].endswith(".ts")]
        # If no strict match but some results contain .m3u8/.ts in query path, include them
        if not out_filtered:
            for u in results:
                if ".m3u8" in u.lower() or ".ts" in u.lower():
                    out_filtered.append(u)

        # final output: one-per-line, nothing else
        for u in out_filtered:
            print(u)

    except requests.RequestException as e:
        # silence other output — print nothing if failed (per request "only .ts or m3u8")
        # but to help debugging, you can uncomment the next line
        # print(f"# ERROR: {e}", file=sys.stderr)
        sys.exit(0)
    except Exception:
        sys.exit(0)

if __name__ == "__main__":
    main()

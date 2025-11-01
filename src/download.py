import argparse
import base64
import concurrent.futures
import itertools
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from html import unescape as html_unescape
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)


def build_headers(user_agent: str, referer: Optional[str], extra_headers: List[str]) -> Dict[str, str]:
    headers: Dict[str, str] = {
        "User-Agent": user_agent or DEFAULT_UA,
        "Accept": "*/*",
        "Accept-Encoding": "identity",
        "Connection": "keep-alive",
        "DNT": "1",
        "Origin": referer or "",
    }
    if referer:
        headers["Referer"] = referer
    for header in extra_headers:
        if ":" not in header:
            continue
        key, value = header.split(":", 1)
        headers[key.strip()] = value.strip()
    return headers


def augment_browser_headers(headers: Dict[str, str]) -> Dict[str, str]:
    # Add common fetch headers if not present to better mimic a browser
    out = dict(headers)
    out.setdefault("Sec-Fetch-Mode", "cors")
    out.setdefault("Sec-Fetch-Site", "cross-site")
    out.setdefault("Sec-Fetch-Dest", "empty")
    out.setdefault("Accept", "*/*")
    return out


# --- Page/embed extraction (parity with main.py) ---
def extract_bunny_embed_urls_from_animixplay_page(page_html: str) -> List[str]:
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
    for token in set(esc_matches + direct_matches + direct_norm):
        try:
            decoded = base64.b64decode(token).decode("utf-8", errors="ignore")
            decoded = html_unescape(decoded)
            if decoded.startswith("http"):
                embeds.append(decoded)
        except Exception:
            pass
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


def parse_master_variants_only(content: str, base_url: str) -> List[Tuple[int, Optional[int], str]]:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    variants: List[Tuple[int, Optional[int], str]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("#EXT-X-STREAM-INF"):
            bandwidth_match = re.search(r"BANDWIDTH=(\\d+)", line)
            resolution_match = re.search(r"RESOLUTION=(\\d+)x(\\d+)", line)
            bandwidth = int(bandwidth_match.group(1)) if bandwidth_match else 0
            height = int(resolution_match.group(2)) if resolution_match else None
            j = i + 1
            while j < len(lines) and lines[j].startswith("#"):
                j += 1
            if j < len(lines):
                uri = urljoin(base_url, lines[j])
                variants.append((bandwidth, height, uri))
            i = j
        else:
            i += 1
    return variants


def choose_variant_best(variants: List[Tuple[int, Optional[int], str]]) -> str:
    return max(variants, key=lambda v: (v[0], v[1] or 0))[2]


def resolve_m3u8_from_page(page_url: str, headers: Dict[str, str], timeout: int) -> Optional[str]:
    # Load page
    page_html = http_get(page_url, headers, timeout).decode("utf-8", errors="ignore")
    embeds = extract_bunny_embed_urls_from_animixplay_page(page_html)
    if not embeds:
        return None
    embed_url = embeds[0]
    embed_headers = build_headers(headers.get("User-Agent", DEFAULT_UA), "https://play.bunnycdn.to/", [])
    embed_html = http_get(embed_url, embed_headers, timeout).decode("utf-8", errors="ignore")
    m3u8 = extract_first_m3u8_from_html(embed_html)
    if m3u8:
        return m3u8
    # try page
    m3u8 = extract_first_m3u8_from_html(page_html)
    if m3u8:
        return m3u8
    return None


def derive_output_basename_from_m3u8(m3u8_url: str) -> Optional[str]:
    # Expecting: https://host/<slug>/<episode>/<quality>/index.m3u8
    # Produce: Ep <episode> <Title From Slug> <quality>p
    try:
        path = urlparse(m3u8_url).path.strip("/")
        parts = path.split("/")
        if len(parts) < 4:
            return None
        slug, episode, quality, last = parts[-4], parts[-3], parts[-2], parts[-1]
        if not last.endswith(".m3u8"):
            return None
        # Beautify slug: replace dashes, strip common flags, Title Case
        title = slug.replace("-", " ")
        title = re.sub(r"\b(dub|sub|dual audio)\b", "", title, flags=re.IGNORECASE).strip()
        title = re.sub(r"\s+", " ", title)
        title = " ".join(w.capitalize() for w in title.split())
        # Ensure episode is numeric
        if not episode.isdigit():
            return None
        if not quality.isdigit():
            return None
        return f"Ep {episode} {title} {quality}p"
    except Exception:
        return None


def http_get(url: str, headers: Dict[str, str], timeout: int) -> bytes:
    request = Request(url, headers=headers, method="GET")
    with urlopen(request, timeout=timeout) as resp:
        return resp.read()


def try_download(url: str, headers: Dict[str, str], timeout: int, retries: int, backoff: float) -> Tuple[bool, Optional[bytes], Optional[int]]:
    for attempt in range(retries + 1):
        try:
            data = http_get(url, headers, timeout)
            return True, data, None
        except HTTPError as e:
            if e.code in (403, 404):
                return False, None, e.code
            if attempt >= retries:
                return False, None, e.code
        except URLError:
            if attempt >= retries:
                return False, None, None
        except Exception:
            if attempt >= retries:
                return False, None, None
        time.sleep(backoff * (2 ** attempt))
    return False, None, None


def parse_m3u8(content: str, base_url: str) -> List[str]:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if any(line.startswith("#EXT-X-STREAM-INF") for line in lines):
        # Caller must resolve master using the same headers used to fetch the top-level playlist.
        raise ValueError("Master playlist provided; use resolve_segments_from_m3u8() that preserves headers")

    segments: List[str] = []
    for idx, line in enumerate(lines):
        if line.startswith("#"):
            continue
        segments.append(urljoin(base_url, line))
    if not segments:
        raise ValueError("No segments found in playlist")
    return segments


def resolve_segments_from_m3u8(m3u8_url: str, headers: Dict[str, str], timeout: int) -> List[str]:
    # Fetch top-level playlist
    top = http_get(m3u8_url, headers, timeout).decode("utf-8", errors="ignore")
    lines = [line.strip() for line in top.splitlines() if line.strip()]
    if any(line.startswith("#EXT-X-STREAM-INF") for line in lines):
        # Master: collect variants and choose best by bandwidth
        variants: List[Tuple[int, str]] = []
        i = 0
        while i < len(lines):
            line = lines[i]
            if line.startswith("#EXT-X-STREAM-INF"):
                bandwidth_match = re.search(r"BANDWIDTH=(\d+)", line)
                bandwidth = int(bandwidth_match.group(1)) if bandwidth_match else 0
                j = i + 1
                while j < len(lines) and lines[j].startswith("#"):
                    j += 1
                if j < len(lines):
                    uri = urljoin(m3u8_url, lines[j])
                    variants.append((bandwidth, uri))
                i = j
            else:
                i += 1
        if not variants:
            raise ValueError("No HLS variants found in master playlist")
        best_uri = max(variants, key=lambda x: x[0])[1]
        media = http_get(best_uri, headers, timeout).decode("utf-8", errors="ignore")
        return parse_m3u8(media, best_uri)
    else:
        # Media playlist directly
        return parse_m3u8(top, m3u8_url)


def playlist_has_encryption(m3u8_text: str) -> bool:
    # Detect AES-128 or SAMPLE-AES keys
    for line in m3u8_text.splitlines():
        if line.strip().startswith("#EXT-X-KEY") and "METHOD=NONE" not in line:
            return True
    return False


def detect_encryption_in_playlist_chain(m3u8_url: str, headers: Dict[str, str], timeout: int) -> bool:
    top = http_get(m3u8_url, headers, timeout).decode("utf-8", errors="ignore")
    lines = [line.strip() for line in top.splitlines() if line.strip()]
    if any(line.startswith("#EXT-X-STREAM-INF") for line in lines):
        # Master: choose best and inspect
        variants: List[Tuple[int, str]] = []
        i = 0
        while i < len(lines):
            line = lines[i]
            if line.startswith("#EXT-X-STREAM-INF"):
                bandwidth_match = re.search(r"BANDWIDTH=(\\d+)", line)
                bandwidth = int(bandwidth_match.group(1)) if bandwidth_match else 0
                j = i + 1
                while j < len(lines) and lines[j].startswith("#"):
                    j += 1
                if j < len(lines):
                    uri = urljoin(m3u8_url, lines[j])
                    variants.append((bandwidth, uri))
                i = j
            else:
                i += 1
        if not variants:
            return False
        best_uri = max(variants, key=lambda x: x[0])[1]
        media = http_get(best_uri, headers, timeout).decode("utf-8", errors="ignore")
        return playlist_has_encryption(media)
    else:
        return playlist_has_encryption(top)


def ffmpeg_download_m3u8(m3u8_url: str, headers: Dict[str, str], output_path: Path) -> bool:
    import subprocess
    import shutil as _shutil

    ffmpeg = _shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    # Build headers string for ffmpeg
    hdr_lines = []
    for k, v in headers.items():
        hdr_lines.append(f"{k}: {v}")
    hdrs = "\\r\\n".join(hdr_lines)
    cmd = [
        ffmpeg,
        "-y",
        "-loglevel", "error",
        "-headers", hdrs,
        "-allowed_extensions", "ALL",
        "-i", m3u8_url,
        "-c", "copy",
        str(output_path),
    ]
    try:
        subprocess.run(cmd, check=True, timeout=60*20)
        return True
    except subprocess.CalledProcessError:
        return False
    except subprocess.TimeoutExpired:
        return False


def yt_dlp_available() -> Optional[str]:
    try:
        import os as _os
        import shutil as _shutil
        # Allow override via env var
        override = _os.environ.get("YTDLP_PATH")
        if override and Path(override).exists():
            return override
        return _shutil.which("yt-dlp") or _shutil.which("yt_dlp") or _shutil.which("yt-dlp.exe")
    except Exception:
        return None


def yt_dlp_download_m3u8(m3u8_url: str, headers: Dict[str, str], output_path: Path, prefer_mp4: bool) -> bool:
    import subprocess
    bin_path = yt_dlp_available()
    if not bin_path:
        # Try Python module fallback
        try:
            from yt_dlp import YoutubeDL  # type: ignore
            ydl_opts: Dict[str, object] = {
                "outtmpl": str(output_path),
                "concurrent_fragment_downloads": 20,
                "http_headers": headers,
                "retries": 10,
                "fragment_retries": 10,
                "nocheckcertificate": True,
                "http_chunk_size": 25 * 1024 * 1024,
            }
            if prefer_mp4:
                ydl_opts.update({"merge_output_format": "mp4", "postprocessors": [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}]})
            with YoutubeDL(ydl_opts) as ydl:
                ydl.download([m3u8_url])
            return True
        except Exception:
            return False
    # Build add-header args
    header_args: List[str] = []
    for k, v in headers.items():
        header_args += ["--add-header", f"{k}: {v}"]
    out_template = str(output_path)
    args = [
        bin_path,
        m3u8_url,
        "-o", out_template,
        "--force-overwrites",
        "--concurrent-fragments", "20",
        "--fragment-retries", "10",
        "--retry-sleep", "1",
        # Speed tweaks
        "--http-chunk-size", "25M",
        # Robustness (only options widely supported across versions)
        "--retries", "15",
        "--socket-timeout", "30",
    ] + header_args
    if prefer_mp4:
        args += ["--merge-output-format", "mp4", "--remux-video", "mp4"]
    # Prefer faster downloader if available
    try:
        import shutil as _shutil
        if _shutil.which("aria2c"):
            # Use aria2c for robust, multi-connection chunking
            args += ["--downloader", "aria2c", "--downloader-args", "aria2c:-x16 -s16 -j16 -k1M"]
        else:
            # Use ffmpeg backend which is often faster than hlsnative
            args += ["--downloader", "ffmpeg"]
    except Exception:
        pass
    try:
        subprocess.run(args, check=True, timeout=60*20)
        return True
    except subprocess.CalledProcessError:
        return False
    except subprocess.TimeoutExpired:
        return False


def write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(data)
    tmp.replace(path)


def download_segments_concurrent(urls: List[str], headers: Dict[str, str], timeout: int, retries: int, out_dir: Path, concurrency: int) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    total_segments = len(urls)

    # Progress state
    progress = {
        "start": time.time(),
        "done": 0,
        "bytes": 0,
        "stop": False,
    }
    progress_lock = None
    try:
        import threading
        progress_lock = threading.Lock()
    except Exception:
        pass

    def task(index_and_url: Tuple[int, str]) -> Tuple[int, bool, Optional[int]]:
        index, seg_url = index_and_url
        ok, data, code = try_download(seg_url, headers, timeout, retries, backoff=0.5)
        if ok and data is not None:
            write_atomic(out_dir / f"{index:06d}.ts", data)
            if progress_lock:
                with progress_lock:
                    progress["done"] += 1
                    progress["bytes"] += len(data)
        return index, ok, code

    # Printer thread
    printer_thread = None
    try:
        import threading

        def printer() -> None:
            last_print = 0.0
            while True:
                if progress["stop"]:
                    break
                now = time.time()
                if now - last_print < 0.5:
                    time.sleep(0.1)
                    continue
                last_print = now
                done = progress["done"]
                bytes_dl = progress["bytes"]
                elapsed = max(0.001, now - progress["start"])
                speed = bytes_dl / elapsed  # bytes/sec
                mb = bytes_dl / (1024 * 1024)
                mbs = speed / (1024 * 1024)
                msg = f"Downloading: {done}/{total_segments} segments | {mb:.2f} MB | {mbs:.2f} MB/s"
                print("\r" + msg, end="", flush=True)
                time.sleep(0.1)

        printer_thread = threading.Thread(target=printer, daemon=True)
        printer_thread.start()
    except Exception:
        printer_thread = None

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(task, (i, u)) for i, u in enumerate(urls)]
        for fut in concurrent.futures.as_completed(futures):
            index, ok, code = fut.result()
            if not ok:
                print(f"Failed segment {index} (code={code})", file=sys.stderr)

    # Stop printer and finalize line
    progress["stop"] = True
    if printer_thread:
        try:
            printer_thread.join(timeout=1.0)
        except Exception:
            pass
    # Print a newline after progress
    if total_segments > 0:
        print()


def template_url_generator(template: str, start: int, end: Optional[int]) -> Iterable[Tuple[int, str]]:
    def format_url(i: int) -> str:
        # Support {index}, {index:03d}, etc.
        return template.format(index=i)

    if end is not None:
        for i in range(start, end + 1):
            yield i, format_url(i)
    else:
        for i in itertools.count(start):
            yield i, format_url(i)


def download_template_mode(template: str, headers: Dict[str, str], timeout: int, retries: int, out_dir: Path, concurrency: int, start: int, end: Optional[int]) -> List[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    results: List[Path] = []
    max_consecutive_missing = 5
    consecutive_missing = 0

    def task(index_and_url: Tuple[int, str]) -> Tuple[int, bool, Optional[bytes]]:
        i, url = index_and_url
        ok, data, code = try_download(url, headers, timeout, retries, backoff=0.5)
        if not ok and code in (403, 404):
            return i, False, None
        return i, ok, data

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=concurrency)
    futures: Dict[concurrent.futures.Future, int] = {}
    gen = template_url_generator(template, start, end)

    def submit_next(batch: int) -> None:
        for _ in range(batch):
            try:
                idx, url = next(gen)
            except StopIteration:
                return
            futures[executor.submit(task, (idx, url))] = idx

    submit_next(concurrency)
    try:
        while futures:
            for fut in concurrent.futures.as_completed(list(futures.keys())):
                idx = futures.pop(fut)
                i, ok, data = fut.result()
                if ok and data is not None:
                    path = out_dir / f"{i:06d}.ts"
                    write_atomic(path, data)
                    results.append(path)
                    consecutive_missing = 0
                else:
                    consecutive_missing += 1
                if consecutive_missing >= max_consecutive_missing and end is None:
                    executor.shutdown(cancel_futures=True)
                    return results
                submit_next(1)
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
    return results


def concat_segments_to_ts(segments_dir: Path, output_ts: Path) -> None:
    parts = sorted(segments_dir.glob("*.ts"))
    if not parts:
        raise RuntimeError("No segment files to merge")
    with output_ts.open("wb") as out:
        for p in parts:
            out.write(p.read_bytes())


def optional_ffmpeg_mp4(input_ts: Path, output_mp4: Path) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    # Attempt stream copy; if it fails, caller can ignore.
    # Using concat result as input.
    import subprocess

    cmd = [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(input_ts),
        "-c",
        "copy",
        str(output_mp4),
    ]
    try:
        subprocess.run(cmd, check=True)
        return True
    except subprocess.CalledProcessError:
        return False


def get_downloads_dir() -> Path:
    # Cross-platform: default to user's Downloads folder
    home = Path.home()
    downloads = home / "Downloads"
    if not downloads.exists():
        try:
            downloads.mkdir(parents=True, exist_ok=True)
        except Exception:
            return home
    return downloads


def ensure_output_path(path: str, default_name: str) -> Path:
    downloads = get_downloads_dir()
    # Case 1: no path provided → Downloads/default_name
    if not path:
        p = downloads / default_name
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    p = Path(path)
    # Case 2: user provided a directory path → use that directory
    if p.is_dir():
        p = p / default_name
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    # Case 3: user provided only a filename (no parent dir) → save in Downloads
    try:
        has_parent = p.parent and p.parent != Path('.') and str(p.parent) not in ('', '.')
    except Exception:
        has_parent = False
    if not has_parent:
        p = downloads / p.name
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    # Case 4: explicit file path with parent dirs
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def main() -> None:
    parser = argparse.ArgumentParser(description="HLS .ts downloader (playlist or numbered template)")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--m3u8", help="URL to .m3u8 playlist")
    src.add_argument("--page", help="AnimixPlay page URL")
    src.add_argument("--template", help="URL template with {index} placeholder, e.g. https://.../segment_{index:03d}.ts")
    parser.add_argument("--episode", type=int, default=0, help="Episode index (reserved)")
    parser.add_argument("--start", type=int, default=1, help="Start index for template mode (default: 1)")
    parser.add_argument("--end", type=int, default=None, help="End index for template mode (inclusive). If omitted, auto-stop on 404s")
    parser.add_argument("--output", "-o", default="", help="Output .ts path (default: auto name saved in Downloads)")
    parser.add_argument("--mp4", default=None, help="Optional MP4 output path (via ffmpeg or yt-dlp)")
    parser.add_argument("--yt-dlp", action="store_true", help="Use yt-dlp to download (faster, recommended)")
    parser.add_argument("--concurrency", type=int, default=8, help="Parallel downloads (default: 8)")
    parser.add_argument("--retries", type=int, default=3, help="Per-request retry count (default: 3)")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout seconds (default: 30)")
    parser.add_argument("--referer", default=None, help="Referer header value to send")
    parser.add_argument("--user-agent", default=DEFAULT_UA, help="User-Agent header (default: modern Chrome)")
    parser.add_argument("--header", action="append", default=[], help="Extra header as 'Key: Value' (repeatable)")
    parser.add_argument("--keep-temp", action="store_true", help="Keep downloaded segments directory")

    args = parser.parse_args()

    headers = build_headers(args.user_agent, args.referer, args.header)

    segments_dir = Path(tempfile.mkdtemp(prefix="hls_segments_"))
    # Determine friendly default name when using --m3u8 and default output name
    default_name = "output.ts"
    if args.m3u8 and (args.output == "output.ts" or not args.output):
        base = derive_output_basename_from_m3u8(args.m3u8)
        if base:
            default_name = f"{base}.ts"
    output_ts = ensure_output_path(args.output, default_name)

    try:
        if args.m3u8 or args.page:
            if args.page:
                maybe = resolve_m3u8_from_page(args.page, headers, args.timeout)
                if not maybe:
                    raise RuntimeError("Could not resolve m3u8 from page")
                base = maybe
            else:
                base = args.m3u8
            # Prefer yt-dlp path if requested and available
            if args.yt_dlp:
                prefer_mp4 = True if args.mp4 is not None or True else False
                # If mp4 name provided, use it; else derive mp4 from base name
                if args.mp4:
                    out_path = ensure_output_path(args.mp4, output_ts.with_suffix(".mp4").name)
                else:
                    # derive base mp4 alongside current naming
                    out_path = output_ts.with_suffix(".mp4")
                ok = yt_dlp_download_m3u8(base, augment_browser_headers(headers), out_path, prefer_mp4)
                if ok:
                    # Remove temp .ts if exists and a separate mp4 was created
                    try:
                        if output_ts.exists() and out_path.suffix.lower() == ".mp4":
                            output_ts.unlink(missing_ok=True)
                    except Exception:
                        pass
                    print(f"Saved via yt-dlp to {out_path}")
                    return
                else:
                    print("yt-dlp failed; falling back to ffmpeg/manual.", file=sys.stderr)
            # If playlist is encrypted, prefer ffmpeg to handle decryption.
            try:
                is_encrypted = detect_encryption_in_playlist_chain(base, headers, args.timeout)
            except Exception:
                is_encrypted = False
            if is_encrypted:
                print("Detected encrypted HLS. Using ffmpeg to download and mux.")
                ok = ffmpeg_download_m3u8(base, headers, output_ts)
                if not ok:
                    print("ffmpeg failed. Falling back to manual segment download (may not decrypt).", file=sys.stderr)
                else:
                    print(f"Saved via ffmpeg to {output_ts}")
                    # Optional MP4 remux
                    if args.mp4:
                        mp4_path = ensure_output_path(args.mp4, output_ts.with_suffix(".mp4").name)
                        ok2 = optional_ffmpeg_mp4(output_ts, mp4_path)
                        if ok2:
                            print(f"Wrote MP4 {mp4_path}")
                        else:
                            print("ffmpeg remux failed; kept .ts", file=sys.stderr)
                    return
            # Resolve segments while preserving headers (important for referer-protected hosts)
            # Resolve segments while preserving headers (important for referer-protected hosts)
            segments = resolve_segments_from_m3u8(base, headers, args.timeout)
            print(f"Found {len(segments)} segments in playlist")
            # Probe the first segment with augmented headers; if blocked, try ffmpeg directly
            test_headers = augment_browser_headers(headers)
            if segments:
                ok_probe, _, code = try_download(segments[0], test_headers, args.timeout, 0, backoff=0)
                if not ok_probe and code in (401, 403):
                    print("Segments blocked by origin policy; switching to ffmpeg...", file=sys.stderr)
                    ok = ffmpeg_download_m3u8(base, test_headers, output_ts)
                    if ok:
                        print(f"Saved via ffmpeg to {output_ts}")
                        if args.mp4:
                            mp4_path = ensure_output_path(args.mp4, output_ts.with_suffix(".mp4").name)
                            ok2 = optional_ffmpeg_mp4(output_ts, mp4_path)
                            if ok2:
                                print(f"Wrote MP4 {mp4_path}")
                            else:
                                print("ffmpeg remux failed; kept .ts", file=sys.stderr)
                        return
                    else:
                        print("ffmpeg fallback failed; attempting manual segment downloads.", file=sys.stderr)
            download_segments_concurrent(segments, test_headers, args.timeout, args.retries, segments_dir, args.concurrency)
            # Verify we actually downloaded most segments
            downloaded = sorted(segments_dir.glob("*.ts"))
            if len(downloaded) < max(1, int(0.8 * len(segments))):
                print("Too many segment failures; switching to ffmpeg fallback...", file=sys.stderr)
                ok = ffmpeg_download_m3u8(base, test_headers, output_ts)
                if ok:
                    print(f"Saved via ffmpeg to {output_ts}")
                    if args.mp4:
                        mp4_path = ensure_output_path(args.mp4, output_ts.with_suffix(".mp4").name)
                        ok2 = optional_ffmpeg_mp4(output_ts, mp4_path)
                        if ok2:
                            print(f"Wrote MP4 {mp4_path}")
                        else:
                            print("ffmpeg remux failed; kept .ts", file=sys.stderr)
                    return
                else:
                    print("ffmpeg fallback failed; proceeding with whatever segments downloaded.", file=sys.stderr)
        else:
            print("Template mode: downloading segments until end or consecutive 404s")
            download_template_mode(args.template, headers, args.timeout, args.retries, segments_dir, args.concurrency, args.start, args.end)

        concat_segments_to_ts(segments_dir, output_ts)
        print(f"Merged into {output_ts}")

        if args.mp4:
            mp4_path = ensure_output_path(args.mp4, output_ts.with_suffix(".mp4").name)
            ok = optional_ffmpeg_mp4(output_ts, mp4_path)
            if ok:
                print(f"Wrote MP4 {mp4_path}")
            else:
                print("ffmpeg not available or failed to remux; kept .ts", file=sys.stderr)
    finally:
        if not args.keep_temp and segments_dir.exists():
            try:
                shutil.rmtree(segments_dir)
            except Exception:
                pass


if __name__ == "__main__":
    main()



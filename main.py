import argparse
import sys
import shutil
import json
import re
import time
import subprocess
import os
import threading
from pathlib import Path
from typing import List, Dict, Any

from src.ts import find_m3u8_from_page, guess_m3u8_from_episode_url
from src.download import (
    build_headers,
    derive_output_basename_from_m3u8,
    detect_encryption_in_playlist_chain,
    ffmpeg_download_m3u8,
    resolve_segments_from_m3u8,
    augment_browser_headers,
    download_segments_concurrent,
    concat_segments_to_ts,
    optional_ffmpeg_mp4,
    ensure_output_path,
    DEFAULT_UA,
    yt_dlp_download_m3u8,
)
def start_progress_server(download_file: str, host: str = "0.0.0.0", port: int = None) -> None:
    """Start a tiny Flask server to serve download progress JSON."""
    try:
        from flask import Flask, jsonify
    except Exception as e:
        print("⚠️ Flask not installed. Run: pip install flask")
        return

    app = Flask(__name__)

    @app.get("/")
    def index():
        return (
            "<h3>MovieFlix Progress</h3>"
            f"<p>Download JSON: <a href='/progress' target='_blank'>/progress</a></p>"
        )

    @app.get("/progress")
    def progress():
        try:
            if Path(download_file).exists():
                with open(download_file, "r", encoding="utf-8") as f:
                    return jsonify(json.load(f))
            return jsonify({"error": "download.json not found"}), 404
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # Respect Render/Heroku-style PORT if available
    bind_port = port if port is not None else int(os.environ.get("PORT", "5000"))
    # Blocking call; intended to run in a daemon thread
    app.run(host=host, port=bind_port, debug=False, use_reloader=False, threaded=True)



class AnimeDownloader:
    def __init__(self):
        self.data_file = "data/data.json"
        self.download_file = "data/download.json"
        self.downloads_dir = Path("downloads")
        self.downloads_dir.mkdir(exist_ok=True)
        
        # Load existing download progress
        self.download_progress = self.load_download_progress()
        
    def load_download_progress(self) -> Dict[str, Any]:
        """Load existing download progress from download.json"""
        try:
            if Path(self.download_file).exists():
                with open(self.download_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            else:
                return {
                    "current_anime_index": 0,
                    "current_episode": 1,
                    "downloaded_anime": [],
                    "failed_downloads": [],
                    "completed_anime": [],
                    "current_anime_details": {
                        "title": "",
                        "total_episodes": 0,
                        "downloaded_episodes": 0,
                        "anime_url": "",
                        "anime_number": 0,
                        "episodes_downloaded": []
                    }
                }
        except Exception as e:
            print(f"Error loading download progress: {e}")
            return {
                "current_anime_index": 0,
                "current_episode": 1,
                "downloaded_anime": [],
                "failed_downloads": [],
                "completed_anime": [],
                "current_anime_details": {
                    "title": "",
                    "total_episodes": 0,
                    "downloaded_episodes": 0,
                    "anime_url": "",
                    "anime_number": 0,
                    "episodes_downloaded": []
                }
            }
    
    def save_download_progress(self):
        """Save download progress to download.json"""
        try:
            Path(self.download_file).parent.mkdir(parents=True, exist_ok=True)
            with open(self.download_file, 'w', encoding='utf-8') as f:
                json.dump(self.download_progress, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Error saving download progress: {e}")
    
    def load_anime_data(self) -> List[Dict[str, Any]]:
        """Load anime data from data.json"""
        try:
            with open(self.data_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading anime data: {e}")
            return []
    
    def is_complete_anime(self, anime: Dict[str, Any]) -> bool:
        """Check if anime has complete episodes (e.g., 24/24, 10/10)"""
        episodes = anime.get('episodes', '')
        if not episodes:
            return False
        
        # Match patterns like "24/24", "10/10", "12/12"
        match = re.match(r'(\d+)/(\d+)', episodes)
        if match:
            current_ep = int(match.group(1))
            total_ep = int(match.group(2))
            return current_ep == total_ep
        
        return False
    
    def get_total_episodes(self, anime: Dict[str, Any]) -> int:
        """Extract total episode count from anime data"""
        episodes = anime.get('episodes', '')
        match = re.match(r'(\d+)/(\d+)', episodes)
        if match:
            return int(match.group(2))
        return 0
    
    def generate_episode_url(self, anime_url: str, episode_num: int) -> str:
        """Generate episode URL by adding /ep{num} to anime URL"""
        base_url = anime_url.rstrip('/')
        return f"{base_url}/ep{episode_num}"
    
    def is_episode_downloaded(self, anime_title: str, episode_num: int) -> bool:
        """Check if episode is already downloaded"""
        clean_title = re.sub(r'[<>:"/\\|?*]', '_', anime_title)
        clean_title = clean_title.replace(' ', '_')
        
        anime_dir = self.downloads_dir / clean_title
        
        # Check multiple possible filename patterns
        episode_files = [
            # Pattern 1: Anime_Name_EpXX.mp4/ts
            anime_dir / f"{clean_title}_Ep{episode_num:02d}.mp4",
            anime_dir / f"{clean_title}_Ep{episode_num:02d}.ts",
            # Pattern 2: Ep X Anime Name.mp4/ts
            anime_dir / f"Ep {episode_num} {anime_title.replace(' ', ' ')}.mp4",
            anime_dir / f"Ep {episode_num} {anime_title.replace(' ', ' ')}.ts",
            # Pattern 3: Ep X Anime Name 720p.mp4/ts
            anime_dir / f"Ep {episode_num} {anime_title.replace(' ', ' ')} 720p.mp4",
            anime_dir / f"Ep {episode_num} {anime_title.replace(' ', ' ')} 720p.ts",
        ]
        
        # Also check for any file containing the episode number
        if anime_dir.exists():
            for file in anime_dir.glob("*"):
                if file.is_file():
                    filename = file.name.lower()
                    # Check if filename contains episode number
                    if f"ep{episode_num}" in filename or f"ep {episode_num}" in filename:
                        return True
        
        return any(f.exists() for f in episode_files)
    
    def get_next_episode_to_download(self) -> tuple:
        """Get the next episode to download"""
        anime_data = self.load_anime_data()
        complete_anime = [anime for anime in anime_data if self.is_complete_anime(anime)]
        
        if not complete_anime:
            return None, None, None
        
        current_index = self.download_progress.get("current_anime_index", 0)
        current_episode = self.download_progress.get("current_episode", 1)
        
        # If we've gone through all anime, start over
        if current_index >= len(complete_anime):
            current_index = 0
            current_episode = 1
        
        anime = complete_anime[current_index]
        total_episodes = self.get_total_episodes(anime)
        
        # If current episode is beyond total episodes, move to next anime
        if current_episode > total_episodes:
            current_index += 1
            current_episode = 1
            
            # If we've gone through all anime, start over
            if current_index >= len(complete_anime):
                current_index = 0
                current_episode = 1
            
            anime = complete_anime[current_index]
            total_episodes = self.get_total_episodes(anime)
        
        # Check if this episode is already downloaded
        anime_title = anime.get('title', '')
        if self.is_episode_downloaded(anime_title, current_episode):
            # Move to next episode
            current_episode += 1
            if current_episode > total_episodes:
                current_index += 1
                current_episode = 1
                if current_index >= len(complete_anime):
                    current_index = 0
                    current_episode = 1
                anime = complete_anime[current_index]
                total_episodes = self.get_total_episodes(anime)
        
        # Update current anime details
        self.download_progress["current_anime_details"] = {
            "title": anime.get('title', ''),
            "total_episodes": total_episodes,
            "downloaded_episodes": self.count_downloaded_episodes(anime_title, total_episodes),
            "anime_url": anime.get('url', ''),
            "anime_number": anime.get('anime_number', 0),
            "episodes_downloaded": self.get_downloaded_episodes_list(anime_title, total_episodes)
        }
        
        return anime, current_episode, (current_index, current_episode)
    
    def count_downloaded_episodes(self, anime_title: str, total_episodes: int) -> int:
        """Count how many episodes are already downloaded"""
        count = 0
        for episode_num in range(1, total_episodes + 1):
            if self.is_episode_downloaded(anime_title, episode_num):
                count += 1
        return count
    
    def get_downloaded_episodes_list(self, anime_title: str, total_episodes: int) -> List[int]:
        """Get list of downloaded episode numbers"""
        downloaded = []
        for episode_num in range(1, total_episodes + 1):
            if self.is_episode_downloaded(anime_title, episode_num):
                downloaded.append(episode_num)
        return downloaded


def download_single_episode(anime: Dict[str, Any], episode_num: int, args) -> bool:
    """Download a single episode using main.py functionality"""
    try:
        anime_title = anime.get('title', '')
        anime_url = anime.get('url', '')
        
        # Clean anime title for filename
        clean_title = re.sub(r'[<>:"/\\|?*]', '_', anime_title)
        clean_title = clean_title.replace(' ', '_')
        
        # Create output directory for this anime
        downloads_dir = Path("downloads")
        downloads_dir.mkdir(exist_ok=True)
        anime_dir = downloads_dir / clean_title
        anime_dir.mkdir(exist_ok=True)
        
        # Generate episode URL
        episode_url = f"{anime_url.rstrip('/')}/ep{episode_num}"
        
        # Output filename
        output_name = f"{clean_title}_Ep{episode_num:02d}"
        
        print(f"🎬 Downloading: {anime_title}")
        print(f"📺 Episode: {episode_num}")
        print(f"🔗 URL: {episode_url}")
        
        # Build headers
        headers = build_headers(args.user_agent, args.referer, [])
        
        # Resolve m3u8 from episode URL
        m3u8 = find_m3u8_from_page(episode_url, args.timeout, args.user_agent)
        if not m3u8:
            m3u8 = guess_m3u8_from_episode_url(episode_url, args.timeout, args.user_agent)
        
        if not m3u8:
            print("❌ Could not resolve m3u8 from episode URL", file=sys.stderr)
            return False
        
        # Derive friendly name
        base_name = derive_output_basename_from_m3u8(m3u8) or output_name
        output_ts = ensure_output_path(str(anime_dir / f"{base_name}.ts"), f"{base_name}.ts")
        
        # Fast path via yt-dlp
        use_ytdlp = args.yt_dlp or True  # Always use yt-dlp for anime downloads
        if use_ytdlp:
            yt_headers = augment_browser_headers(build_headers(args.user_agent, "https://play.bunnycdn.to/", []))
            target_mp4 = str(anime_dir / f"{base_name}.mp4")
            out_path = ensure_output_path(target_mp4, f"{base_name}.mp4")
            ok = yt_dlp_download_m3u8(m3u8, yt_headers, out_path, prefer_mp4=True)
            if ok:
                try:
                    if output_ts.exists():
                        output_ts.unlink(missing_ok=True)
                except Exception:
                    pass
                print(f"✅ Successfully downloaded: {anime_title} Episode {episode_num}")
                return True
            else:
                print("yt-dlp failed; falling back to ffmpeg/manual.", file=sys.stderr)
        
        # Fallback to ffmpeg/manual
        is_encrypted = False
        try:
            is_encrypted = detect_encryption_in_playlist_chain(m3u8, headers, args.timeout)
        except Exception:
            is_encrypted = False
        
        if is_encrypted:
            ok = ffmpeg_download_m3u8(m3u8, headers, output_ts)
            if not ok:
                print("ffmpeg failed on encrypted stream", file=sys.stderr)
                return False
        else:
            # Manual path
            segments = resolve_segments_from_m3u8(m3u8, headers, args.timeout)
            test_headers = augment_browser_headers(headers)
            download_dir = output_ts.parent / f"{output_ts.stem}_segments"
            download_dir.mkdir(parents=True, exist_ok=True)
            download_segments_concurrent(segments, test_headers, args.timeout, retries=3, out_dir=download_dir, concurrency=8)
            concat_segments_to_ts(download_dir, output_ts)
            # Cleanup segment dir
            try:
                shutil.rmtree(download_dir)
            except Exception:
                pass
        
        # MP4 remux: write alongside .ts (project downloads folder)
        mp4_path = output_ts.with_suffix(".mp4")
        ok = optional_ffmpeg_mp4(output_ts, mp4_path)
        if ok:
            try:
                output_ts.unlink(missing_ok=True)
            except Exception:
                pass
            print(f"✅ Successfully downloaded: {anime_title} Episode {episode_num}")
            return True
        else:
            print("ffmpeg remux failed; kept .ts", file=sys.stderr)
            return True  # Still consider it successful if we have .ts
        
    except Exception as e:
        print(f"❌ Error downloading episode: {e}")
        return False


def run_telegram_uploader() -> None:
    """Run the Telegram uploader script to send any files in downloads."""
    try:
        script_path = Path("src") / "telegram.py"
        if script_path.exists():
            # -u for unbuffered output (better live progress)
            subprocess.run([sys.executable, "-u", str(script_path)], check=False)
        else:
            print("⚠️ Telegram uploader not found at src/telegram.py")
    except Exception as e:
        print(f"⚠️ Error running telegram uploader: {e}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Orchestrator: resolve m3u8/name then download video")
    src = parser.add_mutually_exclusive_group(required=False)
    src.add_argument("--page", help="AnimixPlay episode URL, e.g. https://animixplay.name/v1/.../epXXX")
    src.add_argument("--m3u8", help="Direct m3u8 URL, e.g. https://.../index.m3u8")
    parser.add_argument("--referer", default="https://animixplay.name/", help="Referer for page requests")
    parser.add_argument("--user-agent", default=DEFAULT_UA)
    parser.add_argument("--output", "-o", default="", help="Optional override for output .ts name (default: Downloads)")
    parser.add_argument("--mp4", default=None, help="MP4 output path. If omitted and ffmpeg exists, MP4 is created automatically and .ts is deleted")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--yt-dlp", action="store_true", help="Use yt-dlp to download (faster). If omitted, yt-dlp is auto-used for --page when available.")
    parser.add_argument("--anime-mode", action="store_true", help="Auto-download anime episodes one by one")
    
    args = parser.parse_args()
    
    # If anime mode is enabled or no specific URL provided, use anime downloader
    if args.anime_mode or (not args.page and not args.m3u8):
        print("🎬 Anime Downloader Mode")
        print("=" * 40)
        
        downloader = AnimeDownloader()

        # Start Flask progress server in background
        server_thread = threading.Thread(
            target=start_progress_server,
            args=(downloader.download_file, "0.0.0.0", int(os.environ.get("PORT", "5000"))),
            daemon=True,
        )
        server_thread.start()
        print(f"🌐 Progress web link: http://127.0.0.1:{os.environ.get('PORT', '5000')}/")
        
        # Continuous loop: download → upload → advance → repeat
        while True:
            anime, episode_num, progress_info = downloader.get_next_episode_to_download()
            if not anime:
                print("❌ No complete anime found to download!")
                break

            anime_title = anime.get('title', '')
            total_episodes = downloader.get_total_episodes(anime)
            
            # Retry logic: try up to 3 times per episode
            max_retries = 3
            success = False
            for attempt in range(1, max_retries + 1):
                print(f"▶ Download Ep {episode_num}: {anime_title} (attempt {attempt}/{max_retries})")
                success = download_single_episode(anime, episode_num, args)
                
                if success:
                    break
                elif attempt < max_retries:
                    print(f"⚠️ Attempt {attempt} failed, retrying in 2 seconds...")
                    time.sleep(2)
                else:
                    print(f"❌ Failed Ep {episode_num} after {max_retries} attempts, skipping to next episode...")

            if success:
                print("⬆ Uploading queue via Telegram...")
                run_telegram_uploader()

                # Advance progress to next episode
                current_index, current_episode = progress_info
                downloader.download_progress["current_anime_index"] = current_index
                downloader.download_progress["current_episode"] = current_episode + 1

                # Update detail snapshot for UI
                downloaded_count = downloader.count_downloaded_episodes(anime_title, total_episodes)
                downloaded_list = downloader.get_downloaded_episodes_list(anime_title, total_episodes)
                downloader.download_progress["current_anime_details"] = {
                    "title": anime_title,
                    "total_episodes": total_episodes,
                    "downloaded_episodes": downloaded_count,
                    "anime_url": anime.get('url', ''),
                    "anime_number": anime.get('anime_number', 0),
                    "episodes_downloaded": downloaded_list
                }

                # If finished series, move to next anime index
                if current_episode >= total_episodes:
                    downloader.download_progress["completed_anime"] = downloader.download_progress.get("completed_anime", [])
                    if not any(comp.get("anime_number") == anime.get('anime_number', 0) for comp in downloader.download_progress["completed_anime"]):
                        downloader.download_progress["completed_anime"].append({
                            "anime_number": anime.get('anime_number', 0),
                            "title": anime_title,
                            "episodes": f"{total_episodes}/{total_episodes}",
                            "url": anime.get('url', ''),
                            "completed_date": time.strftime("%Y-%m-%d %H:%M:%S")
                        })
                    downloader.download_progress["current_anime_index"] = downloader.download_progress.get("current_anime_index", 0) + 1
                    downloader.download_progress["current_episode"] = 1
            else:
                # After 3 failed attempts, skip to next episode anyway
                current_index, current_episode = progress_info
                downloader.download_progress["current_anime_index"] = current_index
                downloader.download_progress["current_episode"] = current_episode + 1
                
                # If this was the last episode, move to next anime
                if current_episode >= total_episodes:
                    downloader.download_progress["current_anime_index"] = downloader.download_progress.get("current_anime_index", 0) + 1
                    downloader.download_progress["current_episode"] = 1

            downloader.save_download_progress()
            time.sleep(1)  # Small delay before next episode
            continue

        return
    
    # Original functionality for specific URLs
    headers = build_headers(args.user_agent, args.referer, [])

    # 1) Resolve m3u8 from page or use provided
    if args.page:
        m3u8 = find_m3u8_from_page(args.page, args.timeout, args.user_agent)
        if not m3u8:
            m3u8 = guess_m3u8_from_episode_url(args.page, args.timeout, args.user_agent)
    else:
        m3u8 = args.m3u8
    if not m3u8:
        print("Could not resolve m3u8 from page", file=sys.stderr)
        sys.exit(1)

    # 2) Derive friendly name if not provided
    base_name = derive_output_basename_from_m3u8(m3u8) or "output"
    output_ts = ensure_output_path(args.output, f"{base_name}.ts")

    # 3) Fast path via yt-dlp. Auto-use for --page if available, or when explicitly requested.
    use_ytdlp = args.yt_dlp or bool(args.page)
    if use_ytdlp:
        # Prefer Bunny referer for playlist/segments
        yt_headers = augment_browser_headers(build_headers(args.user_agent, "https://play.bunnycdn.to/", []))
        target_mp4 = args.mp4 or f"{base_name}.mp4"
        out_path = ensure_output_path(target_mp4, f"{base_name}.mp4")
        ok = yt_dlp_download_m3u8(m3u8, yt_headers, out_path, prefer_mp4=True)
        if ok:
            try:
                if output_ts.exists():
                    output_ts.unlink(missing_ok=True)
            except Exception:
                pass
            return
        else:
            print("yt-dlp failed; falling back to ffmpeg/manual.", file=sys.stderr)

    # 4) Prefer ffmpeg if encrypted; else try manual with fallback
    is_encrypted = False
    try:
        is_encrypted = detect_encryption_in_playlist_chain(m3u8, headers, args.timeout)
    except Exception:
        is_encrypted = False

    if is_encrypted:
        ok = ffmpeg_download_m3u8(m3u8, headers, output_ts)
        if not ok:
            print("ffmpeg failed on encrypted stream", file=sys.stderr)
            sys.exit(1)
    else:
        # Manual path first
        segments = resolve_segments_from_m3u8(m3u8, headers, args.timeout)
        test_headers = augment_browser_headers(headers)
        download_dir = output_ts.parent / f"{output_ts.stem}_segments"
        download_dir.mkdir(parents=True, exist_ok=True)
        download_segments_concurrent(segments, test_headers, args.timeout, retries=3, out_dir=download_dir, concurrency=8)
        concat_segments_to_ts(download_dir, output_ts)
        # Cleanup segment dir
        try:
            shutil.rmtree(download_dir)
        except Exception:
            pass

    # 5) MP4 remux by default (if ffmpeg), then delete .ts on success
    target_mp4_name = (args.mp4 if args.mp4 is not None else output_ts.with_suffix(".mp4").name)
    if target_mp4_name:
        mp4_path = ensure_output_path(target_mp4_name, output_ts.with_suffix(".mp4").name)
        ok = optional_ffmpeg_mp4(output_ts, mp4_path)
        if ok:
            try:
                output_ts.unlink(missing_ok=True)
            except Exception:
                pass
        else:
            print("ffmpeg remux failed; kept .ts", file=sys.stderr)


if __name__ == "__main__":
    main()



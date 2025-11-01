import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional
import time

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
                return {"downloaded_anime": [], "failed_downloads": [], "partial_downloads": {}}
        except Exception as e:
            print(f"Error loading download progress: {e}")
            return {"downloaded_anime": [], "failed_downloads": [], "partial_downloads": {}}
    
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
        # Remove trailing slash if exists
        base_url = anime_url.rstrip('/')
        return f"{base_url}/ep{episode_num}"
    
    def download_episode(self, episode_url: str, anime_title: str, episode_num: int) -> bool:
        """Download a single episode using main.py functionality"""
        try:
            # Clean anime title for filename
            clean_title = re.sub(r'[<>:"/\\|?*]', '_', anime_title)
            clean_title = clean_title.replace(' ', '_')
            
            # Create output directory for this anime
            anime_dir = self.downloads_dir / clean_title
            anime_dir.mkdir(exist_ok=True)
            
            # Output filename
            output_name = f"{clean_title}_Ep{episode_num:02d}"
            
            # Run main.py with the episode URL
            cmd = [
                sys.executable, "main.py",
                "--page", episode_url,
                "--output", str(anime_dir / f"{output_name}.ts"),
                "--mp4", str(anime_dir / f"{output_name}.mp4"),
                "--yt-dlp"
            ]
            
            print(f"📥 Downloading: {anime_title} Episode {episode_num}")
            print(f"🔗 URL: {episode_url}")
            
            # Run the download command
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)  # 30 min timeout
            
            if result.returncode == 0:
                print(f"✅ Successfully downloaded: {anime_title} Episode {episode_num}")
                return True
            else:
                print(f"❌ Failed to download: {anime_title} Episode {episode_num}")
                if result.stderr:
                    print(f"   Error: {result.stderr[:200]}...")  # Show first 200 chars
                return False
                
        except subprocess.TimeoutExpired:
            print(f"⏰ Timeout downloading: {anime_title} Episode {episode_num}")
            return False
        except Exception as e:
            print(f"❌ Error downloading: {anime_title} Episode {episode_num} - {e}")
            return False
    
    def download_anime_series(self, anime: Dict[str, Any], max_episodes: int = None) -> Dict[str, Any]:
        """Download complete anime series"""
        anime_title = anime.get('title', '')
        anime_url = anime.get('url', '')
        total_episodes = self.get_total_episodes(anime)
        
        if not anime_url or total_episodes == 0:
            return {"success": False, "reason": "Invalid anime data"}
        
        # Limit episodes for testing
        if max_episodes:
            total_episodes = min(total_episodes, max_episodes)
        
        print(f"\n🎬 Starting download for: {anime_title}")
        print(f"📺 Total Episodes: {total_episodes}")
        print(f"🔗 Base URL: {anime_url}")
        
        downloaded_episodes = []
        failed_episodes = []
        
        for episode_num in range(1, total_episodes + 1):
            episode_url = self.generate_episode_url(anime_url, episode_num)
            
            # Check if already downloaded
            if self.is_episode_downloaded(anime_title, episode_num):
                print(f"⏭️  Skipping Episode {episode_num} (already downloaded)")
                downloaded_episodes.append(episode_num)
                continue
            
            # Download episode
            if self.download_episode(episode_url, anime_title, episode_num):
                downloaded_episodes.append(episode_num)
                # Save progress after each successful download
                self.update_download_progress(anime_title, episode_num, True)
            else:
                failed_episodes.append(episode_num)
                self.update_download_progress(anime_title, episode_num, False)
            
            # Small delay between downloads
            time.sleep(1)
        
        return {
            "success": len(failed_episodes) == 0,
            "downloaded_episodes": downloaded_episodes,
            "failed_episodes": failed_episodes,
            "total_episodes": total_episodes
        }
    
    def is_episode_downloaded(self, anime_title: str, episode_num: int) -> bool:
        """Check if episode is already downloaded"""
        clean_title = re.sub(r'[<>:"/\\|?*]', '_', anime_title)
        clean_title = clean_title.replace(' ', '_')
        
        anime_dir = self.downloads_dir / clean_title
        episode_files = [
            anime_dir / f"{clean_title}_Ep{episode_num:02d}.mp4",
            anime_dir / f"{clean_title}_Ep{episode_num:02d}.ts"
        ]
        
        return any(f.exists() for f in episode_files)
    
    def update_download_progress(self, anime_title: str, episode_num: int, success: bool):
        """Update download progress in memory"""
        anime_key = anime_title
        
        if success:
            if anime_key not in self.download_progress["downloaded_anime"]:
                self.download_progress["downloaded_anime"].append(anime_key)
            
            # Track partial downloads
            if anime_key not in self.download_progress["partial_downloads"]:
                self.download_progress["partial_downloads"][anime_key] = []
            
            if episode_num not in self.download_progress["partial_downloads"][anime_key]:
                self.download_progress["partial_downloads"][anime_key].append(episode_num)
        else:
            failed_key = f"{anime_title}_Ep{episode_num:02d}"
            if failed_key not in self.download_progress["failed_downloads"]:
                self.download_progress["failed_downloads"].append(failed_key)
    
    def get_complete_anime_list(self, limit: int = None) -> List[Dict[str, Any]]:
        """Get list of anime with complete episodes"""
        anime_data = self.load_anime_data()
        complete_anime = []
        
        for anime in anime_data:
            if self.is_complete_anime(anime):
                # Check if not already fully downloaded
                anime_title = anime.get('title', '')
                if anime_title not in self.download_progress["downloaded_anime"]:
                    complete_anime.append(anime)
                    
                    # Limit for testing
                    if limit and len(complete_anime) >= limit:
                        break
        
        return complete_anime
    
    def run_downloader(self, test_mode: bool = False):
        """Main function to run the downloader"""
        print("🎬 Anime Downloader")
        print("=" * 50)
        
        # Get complete anime list
        limit = 5 if test_mode else None
        complete_anime = self.get_complete_anime_list(limit)
        
        if not complete_anime:
            print("❌ No complete anime found to download!")
            print("Complete anime means episodes like 24/24, 10/10, etc.")
            return
        
        print(f"📋 Found {len(complete_anime)} complete anime series:")
        for i, anime in enumerate(complete_anime):
            episodes = anime.get('episodes', '')
            print(f"{i+1}. {anime.get('title', '')} - {episodes}")
        
        # Ask user confirmation
        mode_text = "TEST MODE (5 anime)" if test_mode else f"{len(complete_anime)} anime series"
        print(f"\n🚀 Ready to download {mode_text}")
        
        if test_mode:
            print("⚠️  TEST MODE: Only downloading first 2 episodes of each anime")
        
        confirm = input("Continue? (y/N): ").strip().lower()
        
        if confirm != 'y':
            print("❌ Download cancelled")
            return
        
        # Start downloading
        successful_downloads = 0
        failed_downloads = 0
        
        for i, anime in enumerate(complete_anime):
            print(f"\n📺 Progress: {i+1}/{len(complete_anime)}")
            
            # Limit episodes in test mode
            max_episodes = 2 if test_mode else None
            result = self.download_anime_series(anime, max_episodes)
            
            if result["success"]:
                successful_downloads += 1
                print(f"✅ Completed: {anime.get('title', '')}")
            else:
                failed_downloads += 1
                print(f"❌ Failed: {anime.get('title', '')}")
                print(f"   Downloaded: {len(result['downloaded_episodes'])}/{result['total_episodes']}")
                if result['failed_episodes']:
                    print(f"   Failed episodes: {result['failed_episodes']}")
        
        # Save final progress
        self.save_download_progress()
        
        # Final summary
        print(f"\n🎉 Download Summary:")
        print(f"✅ Successful: {successful_downloads}")
        print(f"❌ Failed: {failed_downloads}")
        print(f"📁 Downloads saved in: {self.downloads_dir.absolute()}")

def main():
    """Main function"""
    print("Choose mode:")
    print("1. Test Mode (5 anime, 2 episodes each)")
    print("2. Full Mode (all complete anime)")
    
    choice = input("Enter choice (1-2): ").strip()
    
    downloader = AnimeDownloader()
    
    if choice == "1":
        downloader.run_downloader(test_mode=True)
    else:
        downloader.run_downloader(test_mode=False)

if __name__ == "__main__":
    main()

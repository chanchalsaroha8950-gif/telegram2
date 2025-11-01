import json
import time
import requests
from typing import List, Dict, Any
from pathlib import Path

class AnimixPlayFetcher:
    def __init__(self):
        self.base_url = "https://animixplay.name/api/search"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "*/*",
            "Accept-Encoding": "identity",
            "Connection": "keep-alive",
            "DNT": "1"
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)
        
    def fetch_anime_page(self, page_num: int) -> List[Dict[str, Any]]:
        """Fetch anime data for a specific page number"""
        payload = {"seasonaldub": str(page_num)}
        
        try:
            response = self.session.post(self.base_url, data=payload, timeout=30)
            response.raise_for_status()
            
            data = response.json()
            if isinstance(data, list):
                return data
            elif isinstance(data, dict) and 'result' in data:
                return data['result']
            else:
                print(f"Unexpected response format for page {page_num}")
                return []
                
        except requests.exceptions.RequestException as e:
            print(f"Error fetching page {page_num}: {e}")
            return []
        except json.JSONDecodeError as e:
            print(f"Error parsing JSON for page {page_num}: {e}")
            return []
    
    def process_anime_data(self, anime_list: List[Dict[str, Any]], start_count: int = 0) -> List[Dict[str, Any]]:
        """Process and clean anime data"""
        processed_anime = []
        
        for idx, anime in enumerate(anime_list):
            try:
                # Extract episode info from infotext
                infotext = anime.get('infotext', '')
                episode_info = self.extract_episode_info(infotext)
                
                # Convert relative URL to full URL
                url = anime.get('url', '')
                if url.startswith('/'):
                    full_url = f"https://animixplay.name{url}"
                else:
                    full_url = url
                
                processed_anime.append({
                    'anime_number': start_count + idx + 1,  # Global anime number across all pages
                    'title': anime.get('title', ''),
                    'episodes': episode_info,
                    'score': anime.get('score', ''),
                    'url': full_url,
                    'picture': anime.get('picture', ''),
                    'timetop': anime.get('timetop', ''),
                    'infotext': infotext
                })
                
            except Exception as e:
                print(f"Error processing anime {idx}: {e}")
                continue
                
        return processed_anime
    
    def extract_episode_info(self, infotext: str) -> str:
        """Extract episode information from infotext"""
        if not infotext:
            return ""
        
        # Look for patterns like "EP 3/?" or "EP 12/24"
        import re
        episode_match = re.search(r'EP\s+(\d+)/(\d+|\?)', infotext)
        if episode_match:
            current_ep = episode_match.group(1)
            total_ep = episode_match.group(2)
            return f"{current_ep}/{total_ep}"
        
        # Fallback: return the infotext as is
        return infotext
    
    def fetch_all_anime(self, start_page: int = 0, max_pages: int = 100) -> List[Dict[str, Any]]:
        """Fetch all anime data by paginating through pages"""
        all_anime = []
        page_num = start_page
        total_anime_count = 0
        
        print("Starting anime data fetch...")
        print(f"Starting from page {start_page}")
        
        while page_num <= max_pages:
            print(f"Fetching page {page_num}...")
            
            anime_data = self.fetch_anime_page(page_num)
            
            if not anime_data:
                print(f"No data found on page {page_num}. Stopping.")
                break
            
            processed_data = self.process_anime_data(anime_data, total_anime_count)
            all_anime.extend(processed_data)
            total_anime_count += len(processed_data)
            
            print(f"Found {len(processed_data)} anime on page {page_num} (Total so far: {total_anime_count})")
            
            # Add delay to be respectful to the server
            time.sleep(1)
            page_num += 1
        
        print(f"Total anime fetched: {total_anime_count}")
        return all_anime
    
    def save_to_json(self, anime_data: List[Dict[str, Any]], filename: str = "data/data.json"):
        """Save anime data to JSON file"""
        try:
            # Ensure directory exists
            Path(filename).parent.mkdir(parents=True, exist_ok=True)
            
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(anime_data, f, indent=2, ensure_ascii=False)
            
            print(f"Data saved to {filename}")
            
        except Exception as e:
            print(f"Error saving to JSON: {e}")

def main():
    """Main function to run the anime fetcher"""
    fetcher = AnimixPlayFetcher()
    
    # Ask user for starting page
    print("Anime Data Fetcher")
    print("=" * 50)
    
    try:
        start_page = int(input("Enter starting page (0 or 1): ") or "0")
        max_pages = int(input("Enter maximum pages to fetch (default 100): ") or "100")
    except ValueError:
        print("Invalid input. Using default values: start_page=0, max_pages=100")
        start_page = 0
        max_pages = 100
    
    print(f"\nStarting from page {start_page}, maximum {max_pages} pages")
    
    # Fetch all anime data
    anime_data = fetcher.fetch_all_anime(start_page=start_page, max_pages=max_pages)
    
    if anime_data:
        # Save to JSON file
        fetcher.save_to_json(anime_data)
        
        # Print summary
        print(f"\nSummary:")
        print(f"Total anime fetched: {len(anime_data)}")
        print(f"Last anime number: {anime_data[-1]['anime_number']}")
        print(f"First few anime:")
        for i, anime in enumerate(anime_data[:5]):
            print(f"{anime['anime_number']}. {anime['title']} - {anime['episodes']} - Score: {anime['score']}")
        
        print(f"\nLast few anime:")
        for anime in anime_data[-3:]:
            print(f"{anime['anime_number']}. {anime['title']} - {anime['episodes']} - Score: {anime['score']}")
    else:
        print("No anime data fetched!")

if __name__ == "__main__":
    main()

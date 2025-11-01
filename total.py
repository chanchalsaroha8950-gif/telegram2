import json
import re

# Path to your data.json
INPUT_FILE = r"data/data.json"

def main():
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        items = json.load(f)
    
    total_episodes = 0
    complete_list = []

    # Only include infotext with 'EP N/N'
    for entry in items:
        infotext = entry.get("infotext", "")
        match = re.match(r"EP\s+(\d+)/(\d+)", infotext)
        if match and match.group(1) == match.group(2):
            ep_count = int(match.group(2))
            total_episodes += ep_count
            complete_list.append({
                "anime_number": entry.get("anime_number"),
                "title": entry.get("title"),
                "infotext": infotext,
                "episodes": ep_count
            })
    
    print(f"Total Completed Anime Series: {len(complete_list)}")
    print(f"Total Episodes (complete only): {total_episodes}")
    print("\nSample (first 10):")
    for item in complete_list[:10]:
        print(f"{item['title']} - {item['infotext']}")

if __name__ == "__main__":
    main()
import json
import re
from pathlib import Path

INPUT_FILE = r"data/data.json"
GROUP_COUNT = 8
OUTPUT_DIR = Path("json")

def extract_episode_count(entry):
    infotext = entry.get("infotext", "")
    match = re.match(r"EP\s+(\d+)/(\d+)", infotext)
    if match:
        return int(match.group(2))
    return 0

def main():
    # Ensure output directory exists
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Load data
    with open(INPUT_FILE, encoding="utf-8") as f:
        all_anime = json.load(f)
    
    # Get (entry, episode_count)
    anime_list = [(entry, extract_episode_count(entry)) for entry in all_anime]
    anime_list = [a for a in anime_list if a[1] > 0]  # Only entries with episode counts

    # Sort for greedy balancing
    anime_list.sort(key=lambda x: -x[1])
    groups = [[] for _ in range(GROUP_COUNT)]
    group_totals = [0] * GROUP_COUNT

    for entry, epcount in anime_list:
        min_index = group_totals.index(min(group_totals))
        groups[min_index].append(entry)
        group_totals[min_index] += epcount

    # Save to ./json/group_X.json
    for i, group in enumerate(groups, 1):
        out_path = OUTPUT_DIR / f'group_{i}.json'
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(group, f, ensure_ascii=False, indent=2)

    print("Done!")
    for i, total in enumerate(group_totals, 1):
        print(f"Group {i}: {total} episodes, {len(groups[i-1])} entries")

if __name__ == "__main__":
    main()
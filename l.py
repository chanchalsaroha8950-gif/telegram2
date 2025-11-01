import requests

# Base API URL
url = "https://animixplay.name/api/search"

# Function to search by seasonaldub ID
def search_anime(season_id):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
                      " AppleWebKit/537.36 (KHTML, like Gecko)"
                      " Chrome/141.0.0.0 Safari/537.36",
        "X-Requested-With": "XMLHttpRequest",
    }

    # Modify this payload dynamically
    payload = {
        "seasonaldub": str(season_id)
    }

    print(f"Fetching data for seasonaldub={season_id} ...")
    response = requests.post(url, data=payload, headers=headers)

    # If request is successful
    if response.status_code == 200:
        try:
            data = response.json()
            print("✅ Request successful! Here’s partial data preview:")
            print(data[:3])  # Prints first 3 results only for preview
        except Exception as e:
            print("Response is not JSON, raw output:")
            print(response.text)
    else:
        print(f"❌ Failed (status {response.status_code})")

# Example: run with ID 115
search_anime(156)

# You can change it easily:
# search_anime(120)
# search_anime(200)

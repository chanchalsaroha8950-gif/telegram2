import asyncio
import os
from getpass import getpass
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

# --- CONFIG ---
API_ID = None
API_HASH = None
PHONE = None
TARGET_CHAT_ID = -1003134647670  # channel/chat id

async def main():
    global API_ID, API_HASH, PHONE

    # Input credentials
    if not API_ID:
        API_ID = int(input("Enter your api_id: ").strip())
    if not API_HASH:
        API_HASH = input("Enter your api_hash: ").strip()
    if not PHONE:
        PHONE = input("Enter your phone (with country code, e.g. +91XXXXXXXXXX): ").strip()

    session_name = "my_telegram_session"
    client = TelegramClient(session_name, API_ID, API_HASH)

    await client.start(phone=PHONE)

    if not await client.is_user_authorized():
        try:
            await client.send_code_request(PHONE)
            code = input("Enter the code you received: ").strip()
            await client.sign_in(PHONE, code)
        except SessionPasswordNeededError:
            pwd = getpass("Two-step password detected. Enter your Telegram password: ")
            await client.sign_in(password=pwd)

    print("✅ Logged in successfully!")

    # File path input
    file_path = input("Enter video file path to send: ").strip()
    if not os.path.isfile(file_path):
        print("❌ Error: File not found:", file_path)
        await client.disconnect()
        return

    caption = input("Enter caption (or press Enter to skip): ").strip() or None

    # --- Progress bar callback ---
    def progress(current, total):
        percent = (current / total) * 100
        print(f"\r📤 Uploading: {percent:.2f}% ({current // (1024*1024)}MB / {total // (1024*1024)}MB)", end="")

    print(f"\nSending file '{os.path.basename(file_path)}' to chat id {TARGET_CHAT_ID} as document...\n")

    try:
        await client.send_file(
            TARGET_CHAT_ID,
            file=file_path,
            caption=caption,
            force_document=True,   # 👈 sends as FILE, not video
            progress_callback=progress
        )
        print("\n✅ File sent successfully!")
    except Exception as e:
        print("\n❌ Failed to send file:", e)
    finally:
        await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())

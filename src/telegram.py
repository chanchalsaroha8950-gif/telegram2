import asyncio
import os
import sys
from pathlib import Path
from getpass import getpass
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.tl.types import DocumentAttributeFilename

# Add the src directory to Python path for imports
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from src.parallel_file_transfer import upload_file, ParallelTransferrer

# --- CONFIG ---
API_ID = 22971299
API_HASH = "7594fa2a0f6351de56fd5fddfecc07ac"
PHONE = "+918607400917"
TARGET_CHAT_ID = -1003134647670  # channel/chat id
SESSION_NAME = "data/telegram_session"

async def main():
    # Ensure data directory exists
    Path("data").mkdir(exist_ok=True)
    
    # Initialize client with session in data folder and optimized settings for faster uploads
    client = TelegramClient(
        SESSION_NAME, 
        API_ID, 
        API_HASH,
        connection_retries=10,
        retry_delay=0.5,
        timeout=60,
        request_retries=10,
        flood_sleep_threshold=120,
        auto_reconnect=True,
        sequential_updates=False,  # Disable for faster uploads
        use_ipv6=False,  # Use IPv4 for better speed
        system_version="4.16.30-vxCUSTOM",  # Optimized version string
        device_model="Desktop",  # Simple device model
        app_version="1.0"  # Simple app version
    )

    try:
        await client.start(phone=PHONE)
    except Exception as e:
        print(f"Error starting client: {e}")
        print("Please run the script interactively to create session first.")
        return

    if not await client.is_user_authorized():
        print("User not authorized. Please run interactively to create session.")
        await client.disconnect()
        return

    print("Logged in successfully!")
    print(f"Session saved to: {SESSION_NAME}.session")

    # Tune parallel connections for stability
    try:
        import math
        def _faster_conn_count(file_size: int, max_count: int = 8, full_size: int = 80 * 1024 * 1024) -> int:
            if file_size > full_size:
                return max_count
            return math.ceil((file_size / full_size) * max_count)
        ParallelTransferrer._get_connection_count = staticmethod(_faster_conn_count)
    except Exception:
        pass

    # Find all video files in downloads folder
    downloads_path = Path("downloads")
    if not downloads_path.exists():
        print("❌ Downloads folder not found!")
        await client.disconnect()
        return

    video_files = []
    for file_path in downloads_path.rglob("*"):
        if file_path.is_file() and file_path.suffix.lower() in ['.mp4', '.mkv', '.avi', '.mov', '.ts']:
            video_files.append(file_path)

    if not video_files:
        print("No video files found in downloads folder!")
        await client.disconnect()
        return

    print(f"Found {len(video_files)} video files")

    # Import time for upload statistics
    import time

    def format_duration(seconds: float) -> str:
        seconds_int = int(seconds)
        hrs = seconds_int // 3600
        mins = (seconds_int % 3600) // 60
        secs = seconds_int % 60
        if hrs > 0:
            return f"{hrs:02d}:{mins:02d}:{secs:02d}"
        return f"{mins:02d}:{secs:02d}"

    sent_count = 0
    failed_count = 0

    for video_file in video_files:
        # Use filename as caption
        caption = video_file.name
        file_size_mb = video_file.stat().st_size / (1024 * 1024)
        
        print(f"\nSending: {video_file.name} ({file_size_mb:.1f} MB)")
        
        # Track upload time for statistics
        file_start_time = time.time()

        try:
            max_retries = 3
            retry_delay = 2
            attempt = 0
            input_file = None
            while attempt < max_retries:
                # Open file for parallel upload (reopen each attempt)
                file_handle = open(video_file, "rb")
                print(f"⚡ Uploading {video_file.name} using parallel transfer... (attempt {attempt+1}/{max_retries})")

                # Create a better progress callback with smoother updates
                last_progress = 0
                last_update_time = time.time()

                def progress_callback(sent, total):
                    nonlocal last_progress, last_update_time
                    current_progress = sent / total * 100
                    current_time = time.time()

                    # Update if progress changed by at least 0.3% OR every 0.5 seconds
                    time_diff = current_time - last_update_time
                    progress_diff = current_progress - last_progress

                    if progress_diff >= 0.3 or time_diff >= 0.5 or current_progress >= 99.9:
                        print(f'\rProgress: {current_progress:.1f}% ({sent//1024//1024}MB / {total//1024//1024}MB)', end='', flush=True)
                        last_progress = current_progress
                        last_update_time = current_time

                try:
                    # Upload using parallel transfer with improved progress callback and a hard timeout
                    # Large safety timeout (in seconds); adjust if needed
                    UPLOAD_TIMEOUT = 60 * 15
                    input_file = await asyncio.wait_for(
                        upload_file(
                            client,
                            file_handle,
                            progress_callback=progress_callback
                        ),
                        timeout=UPLOAD_TIMEOUT
                    )
                    print()  # New line after progress
                    break
                except Exception as e:
                    print(f"\nUpload error: {e}")
                    attempt += 1
                    if attempt < max_retries:
                        await asyncio.sleep(retry_delay)
                    input_file = None
                finally:
                    # Close file handle every attempt
                    try:
                        file_handle.close()
                    except Exception:
                        pass
            if input_file is None:
                print("⚠️ Parallel upload failed after retries, falling back to standard upload...")
                # Fallback: Telethon's built-in uploader (single connection, more stable)
                def fallback_progress(cur, total):
                    pct = (cur / total) * 100 if total else 0
                    print(f"\rFallback upload: {pct:.1f}% ({cur//1024//1024}MB / {total//1024//1024}MB)", end='', flush=True)
                await client.send_file(
                    TARGET_CHAT_ID,
                    file=str(video_file),
                    caption=caption,
                    force_document=True,
                    file_name=video_file.name,
                    attributes=[DocumentAttributeFilename(file_name=video_file.name)],
                    progress_callback=fallback_progress
                )
                print()
                # After fallback send, consider it done and skip the normal send below
                upload_time = time.time() - file_start_time
                avg_speed = file_size_mb / upload_time if upload_time > 0 else 0
                print(f"\nFile sent successfully! (fallback)")
                print(f"Upload time: {upload_time:.1f} seconds")
                print(f"Average speed: {avg_speed:.1f} MB/s")
                try:
                    video_file.unlink()
                    print(f"Deleted: {video_file.name}")
                except Exception as e:
                    print(f"Could not delete file: {e}")
                sent_count += 1
                # Continue to next file
                continue
            
            # Send the uploaded file as document, preserving original filename
            await client.send_file(
                TARGET_CHAT_ID,
                file=input_file,
                caption=caption,
                force_document=True,  # sends as FILE, not video
                file_name=video_file.name,
                attributes=[DocumentAttributeFilename(file_name=video_file.name)]
            )
            # Calculate upload statistics
            upload_time = time.time() - file_start_time
            avg_speed = file_size_mb / upload_time if upload_time > 0 else 0
            
            print(f"\nFile sent successfully!")
            print(f"Upload time: {upload_time:.1f} seconds")
            print(f"Average speed: {avg_speed:.1f} MB/s")
            print(f"Sent '{video_file.name}' in {format_duration(upload_time)} ({avg_speed:.1f} MB/s)")
            
            # Delete file after successful send
            try:
                video_file.unlink()
                print(f"Deleted: {video_file.name}")
            except Exception as e:
                print(f"Could not delete file: {e}")
            
            sent_count += 1
            
        except Exception as e:
            print(f"\nFailed to send file: {e}")
            failed_count += 1
        
        # Small delay between sends
        await asyncio.sleep(1)  # Reduced delay for faster processing

    print(f"\nSummary: {sent_count} sent, {failed_count} failed")
    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())


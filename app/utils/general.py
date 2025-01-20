# app/util.py
"""
General utility functions.
"""
import asyncio
import os
from datetime import datetime
from pydub import AudioSegment


async def _convert_to_wav(src_path: str, dst_path: str) -> None:
    """Async utility to convert audio file to WAV (mono)."""
    def do_convert():
        audio = AudioSegment.from_file(src_path)
        audio = audio.set_channels(1)
        audio.export(dst_path, format="wav", bitrate="16k", parameters=["-ar", "16000"])

    await asyncio.to_thread(do_convert)
    if not os.path.exists(dst_path):
        raise FileNotFoundError(f"WAV conversion failed for {src_path}")

def generate_meeting_room_name() -> str:
    """Generates a name for a meeting room based on the current time."""
    current_time = datetime.now().strftime("%H%M%S")
    return f"{os.getenv("DISCORD_MEETING_ROOM_NAME")}-{current_time}"

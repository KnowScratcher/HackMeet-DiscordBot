# app/utils.py
"""
General utility functions.
"""

import os
from datetime import datetime

def generate_meeting_room_name() -> str:
    """Generates a name for a meeting room based on the current time."""
    current_time = datetime.now().strftime("%H%M%S")
    return f"{os.getenv("DISCORD_MEETING_ROOM_NAME")}-{current_time}"

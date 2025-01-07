# app/utils.py
"""
General utility functions.
"""

import random
import string

def generate_meeting_room_name() -> str:
    """Generates a name for a meeting room."""
    random_suffix = ''.join(random.choices(string.digits, k=6))
    return f"MeetingRoom-{random_suffix}"

# app/stt_service/stt_select.py
"""
STT select module to select the appropriate STT service based on environment variables.
"""

import os
from app.stt_service.azure_stt import azure_stt_with_timeline
from app.stt_service.google_stt import google_stt_with_timeline

def select_stt_function():
    """Selects the STT service based on the environment variable STT_SERVICE."""
    service = os.getenv("STT_SERVICE", "azure").lower()
    match service:
        case "azure":
            return azure_stt_with_timeline
        case "google":
            return google_stt_with_timeline
        case _:
            raise ValueError(f"No such STT service: {service}")

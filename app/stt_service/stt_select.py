# app/stt_service/stt_select.py
"""
STT select module to select the appropriate STT service based on environment variables.
"""

import os
from app.stt_service.azure_stt import azure_stt_with_timeline
from app.stt_service.google_stt import google_stt_with_timeline_batch


def select_stt_function(batch: bool = False):
    """
    Selects the STT service based on the environment variable STT_SERVICE.

    Args:
        batch (bool): Whether we want the batch version for Google STT.

    Returns:
        Callable: The selected STT function.
    """
    service = os.getenv("STT_SERVICE", "azure").lower()
    match service:
        case "azure":
            return azure_stt_with_timeline
        case "google":
            if batch:
                # Batch version for Google STT
                return google_stt_with_timeline_batch
            else:
                # Non-batch version for Google STT (TODO)
                return google_stt_with_timeline_batch
        case _:
            raise ValueError(f"No such STT service: {service}")

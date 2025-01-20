# app/azure_stt.py
"""
Azure STT utility functions with async approach.
"""

import os
import asyncio
import logging
from typing import List, Dict
import azure.cognitiveservices.speech as speechsdk

from app.utils.general import _convert_to_wav

logger = logging.getLogger(__name__)



async def azure_stt_with_timeline(audio_file_path: str) -> List[Dict]:
    """Asynchronously converts an MP3 file to text using Azure Speech Service."""
    speech_key = os.getenv("AZURE_SPEECH_KEY")
    service_region = os.getenv("AZURE_SPEECH_REGION")
    if not speech_key or not service_region:
        logger.error("Missing Azure credentials.")
        return []

    wav_path = f"{audio_file_path}.wav"
    try:
        await _convert_to_wav(audio_file_path, wav_path)
    except Exception as error:
        logger.error("Failed to convert MP3 to WAV: %s", error)
        return []

    speech_config = speechsdk.SpeechConfig(subscription=speech_key, region=service_region)
    speech_config.speech_recognition_language = os.getenv("SPEECH_LANGUAGE", "en-US")
    audio_input = speechsdk.AudioConfig(filename=wav_path)
    recognizer = speechsdk.SpeechRecognizer(
        speech_config=speech_config,
        audio_config=audio_input
    )

    results = []
    done = False

    def recognized_handler(evt: speechsdk.SpeechRecognitionEventArgs):
        """Handles recognized speech event."""
        if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
            start_sec = evt.result.offset / 10_000_000
            dur_sec = evt.result.duration / 10_000_000
            results.append({
                "offset": start_sec,
                "duration": dur_sec,
                "text": evt.result.text
            })

    def stop_cb(_evt):
        nonlocal done
        done = True

    recognizer.recognized.connect(recognized_handler)
    recognizer.session_stopped.connect(stop_cb)
    recognizer.canceled.connect(stop_cb)

    recognizer.start_continuous_recognition()
    while not done:
        await asyncio.sleep(0.5)
    recognizer.stop_continuous_recognition()

    # Clean up the temp WAV
    try:
        if os.path.exists(wav_path):
            await asyncio.to_thread(os.remove, wav_path)
    except Exception as ex:
        logger.error("Failed to remove temp WAV: %s", ex)

    return results

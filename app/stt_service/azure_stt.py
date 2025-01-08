# app/azure_stt.py
"""
Azure STT utility functions.
"""

import os
import time
import logging
from typing import List, Dict

from pydub import AudioSegment
import azure.cognitiveservices.speech as speechsdk

logger = logging.getLogger(__name__)

def azure_stt_with_timeline(audio_file_path: str) -> List[Dict]:
    """Converts an MP3 file to text using Azure Speech Service."""
    speech_key = os.getenv("AZURE_SPEECH_KEY")
    service_region = os.getenv("AZURE_SPEECH_REGION")
    if not speech_key or not service_region:
        logger.error("Missing Azure credentials.")
        return []

    # Convert MP3 to WAV
    wav_path = audio_file_path + ".wav"
    try:
        sound = AudioSegment.from_file(audio_file_path, format="mp3")
        sound.export(wav_path, format="wav")
    except Exception as error:
        logger.error("Failed to convert MP3 to WAV: %s", error)
        return []

    speech_config = speechsdk.SpeechConfig(subscription=speech_key, region=service_region)
    speech_config.speech_recognition_language = os.getenv("AZURE_SPEECH_LANGUAGE", "en-US")
    audio_input = speechsdk.AudioConfig(filename=wav_path)
    recognizer = speechsdk.SpeechRecognizer(
        speech_config=speech_config, audio_config=audio_input
    )

    results = []

    def recognized_handler(evt: speechsdk.SpeechRecognitionEventArgs):
        """Handle recognized speech event."""
        if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
            start_sec = evt.result.offset / 10_000_000
            dur_sec = evt.result.duration / 10_000_000
            results.append({
                "offset": start_sec,
                "duration": dur_sec,
                "text": evt.result.text
            })

    done = False

    def stop_cb(_evt):
        """Stop callback when speech session ends."""
        nonlocal done
        done = True

    recognizer.recognized.connect(recognized_handler)
    recognizer.session_stopped.connect(stop_cb)
    recognizer.canceled.connect(stop_cb)

    recognizer.start_continuous_recognition()
    while not done:
        time.sleep(0.5)
    recognizer.stop_continuous_recognition()

    # Clean up the temp WAV
    try:
        if os.path.exists(wav_path):
            os.remove(wav_path)
    except Exception as ex:
        logger.error("Failed to remove temp WAV: %s", ex)

    return results

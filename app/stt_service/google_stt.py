# app/google_stt.py
"""
Google Speech-to-Text service module.
"""
import os
import re
import asyncio
import logging
from typing import List, Dict

# Google Cloud imports
from google.cloud import storage
from google.cloud import speech_v2
from google.cloud.speech_v2.types import cloud_speech

from app.utils import _convert_to_wav

logger = logging.getLogger(__name__)


async def google_stt_with_timeline(audio_file_path: str) -> List[Dict]:
    """Asynchronously converts an audio file to text using Google Speech-to-Text."""

    language_code = os.getenv("SPEECH_LANGUAGE", "en-US")
    bucket_name = os.getenv("GCS_BUCKET_NAME")
    if not bucket_name:
        logger.error("No GCS bucket name provided.")
        return []

    # Convert MP3 to WAV
    wav_path = f"{audio_file_path}.wav"
    try:
        await _convert_to_wav(audio_file_path, wav_path)
    except Exception as error:
        logger.error("Convert MP3 to WAV failed: %s", error)
        return []

    # Upload to GCS
    storage_client = await asyncio.to_thread(storage.Client)
    bucket = storage_client.bucket(bucket_name)
    file_name_in_gcs = os.path.basename(wav_path)

    try:
        blob = bucket.blob(file_name_in_gcs)
        await asyncio.to_thread(blob.upload_from_filename, wav_path)
        gcs_uri = f"gs://{bucket_name}/{file_name_in_gcs}"
        logger.info("Uploaded to GCS: %s", gcs_uri)
    except Exception as ex:
        logger.error("Image upload to GCS failed: %s", ex)
        return []

    # Create SpeechClient
    try:
        client = await asyncio.to_thread(speech_v2.SpeechClient)
    except Exception as ex:
        logger.error("Create SpeechClient failed: %s", ex)
        return []

    project_id = os.getenv("GCP_PROJECT_ID", "your-gcp-project")
    location = "global"
    recognizer_parent = f"projects/{project_id}/locations/{location}"
    recognizer_id = "long-audio-recognizer"
    recognizer_name = f"{recognizer_parent}/recognizers/{recognizer_id}"

    # Try to get an existing Recognizer
    try:
        recognizer = await asyncio.to_thread(client.get_recognizer, name=recognizer_name)
        logger.info("Caught Recognizer: %s", recognizer.name)
    except Exception as get_ex:
        logger.info("Could not get Recognizer: %s", get_ex)
        try:
            recognizer = await asyncio.to_thread(
                client.create_recognizer,
                parent=recognizer_parent,
                recognizer_id=recognizer_id,
                recognizer={
                    "default_recognition_config": {
                        "language_codes": [language_code],
                        "model": "long",
                    }
                },
            )
            logger.info("Created Recognizer: %s", recognizer.name)
        except Exception as create_ex:
            logger.error("Create Recognizer failed: %s", create_ex)
            return []

    # Batch Recognize
    output_gcs_uri = f"gs://{bucket_name}/transcripts/{file_name_in_gcs}_results/"
    request = cloud_speech.BatchRecognizeRequest(
        recognizer=recognizer.name,
        config=cloud_speech.RecognitionConfig(
            auto_decoding_config=cloud_speech.AutoDetectDecodingConfig(),
            language_codes=[language_code],
            model="long",
            features=cloud_speech.RecognitionFeatures(
                enable_word_time_offsets=True
            ),
        ),
        files=[cloud_speech.BatchRecognizeFileMetadata(uri=gcs_uri)],
        recognition_output_config=cloud_speech.RecognitionOutputConfig(
            gcs_output_config=cloud_speech.GcsOutputConfig(uri=output_gcs_uri)
        ),
    )

    try:
        operation = await asyncio.to_thread(client.batch_recognize, request=request, timeout=int(os.getenv("MAX_WAIT_SECONDS", 86400)))
        logger.info("Batch Recognize operation...")
        response = await asyncio.to_thread(operation.result)
    except Exception as ex:
        logger.error("Batch Recognize failed: %s", ex)
        return []

    results_list = []

    try:
        for input_uri, file_result in response.results.items():
            recognized_output_uri = file_result.uri
            logger.info(f"For input URI: {input_uri}, recognized output URI: {recognized_output_uri}")

            match = re.match(r"gs://([^/]+)/(.+)", recognized_output_uri)
            if not match:
                logger.error(f"Cannot parse GCS URI: {recognized_output_uri}")
                continue

            output_bucket, output_object = match.groups()

            # Download the result file
            result_blob = storage_client.bucket(output_bucket).blob(output_object)
            results_bytes = await asyncio.to_thread(result_blob.download_as_bytes)

            # Parse the result
            batch_recognize_results = cloud_speech.BatchRecognizeResults.from_json(
                results_bytes, ignore_unknown_fields=True
            )

            # Extract the words and timings
            for result in batch_recognize_results.results:
                for alternative in result.alternatives:
                    words = alternative.words
                    if not words:
                        results_list.append({
                            "offset": 0.0,
                            "duration": 0.0,
                            "text": alternative.transcript
                        })
                    else:
                        first_word = words[0]
                        last_word = words[-1]
                        # Calculate the offset and duration in seconds
                        start_seconds = first_word.start_offset.total_seconds()
                        end_seconds = last_word.end_offset.total_seconds()

                        offset_val = start_seconds
                        duration_val = max(0.0, end_seconds - start_seconds)

                        results_list.append({
                            "offset": offset_val,
                            "duration": duration_val,
                            "text": alternative.transcript
                        })


    except Exception as ex:
        logger.error("Process Batch Recognize results failed: %s", ex)

    # Clean up
    try:
        blob = bucket.blob(file_name_in_gcs)
        await asyncio.to_thread(blob.delete)
        logger.info("Cleaned up GCS temp file: %s", file_name_in_gcs)
    except Exception as ex:
        logger.error("Clean up GCS temp file failed: %s", ex)

    try:
        if os.path.exists(wav_path):
            await asyncio.to_thread(os.remove, wav_path)
    except Exception as ex:
        logger.error("Clean up local temp file failed: %s", ex)

    return results_list

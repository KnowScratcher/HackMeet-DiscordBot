# app/stt_service/google_stt.py
"""
Google Speech-to-Text service module for batch files.
This module demonstrates how to convert multiple MP3 files to WAV,
upload them all to GCS at once, and then invoke a single Batch Recognize
call for improved performance.
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


async def google_stt_with_timeline_batch(audio_file_dict: Dict[str, str]) -> Dict[str, List[Dict]]:
    """
    Asynchronously converts multiple audio files to text using Google Speech-to-Text in a single batch.

    Args:
        audio_file_dict (Dict[str, str]):
            A mapping of user ID (or any unique identifier) to the local MP3 file path.

    Returns:
        Dict[str, List[Dict]]:
            A dictionary where the key is the same user ID (or unique ID) and
            the value is a list of STT segments (offset, duration, text).
    """

    language_code = os.getenv("SPEECH_LANGUAGE", "en-US")
    bucket_name = os.getenv("GCS_BUCKET_NAME")
    if not bucket_name:
        logger.error("No GCS bucket name provided.")
        return {}

    # Prepare local WAV paths
    wav_paths = {}
    for user_id, audio_file_path in audio_file_dict.items():
        wav_path = f"{audio_file_path}.wav"
        try:
            await _convert_to_wav(audio_file_path, wav_path)
            wav_paths[user_id] = wav_path
        except Exception as error:
            logger.error("Convert MP3 to WAV failed for %s: %s", user_id, error)

    # Upload all WAV files to GCS
    storage_client = await asyncio.to_thread(storage.Client)
    bucket = storage_client.bucket(bucket_name)

    gcs_uri_dict = {}
    for user_id, wav_path in wav_paths.items():
        file_name_in_gcs = os.path.basename(wav_path)
        try:
            blob = bucket.blob(file_name_in_gcs)
            await asyncio.to_thread(blob.upload_from_filename, wav_path)
            gcs_uri = f"gs://{bucket_name}/{file_name_in_gcs}"
            gcs_uri_dict[user_id] = gcs_uri
            logger.info("Uploaded user %s file to GCS: %s", user_id, gcs_uri)
        except Exception as ex:
            logger.error("WAV upload to GCS failed for %s: %s", user_id, ex)

    # Create SpeechClient
    try:
        client = await asyncio.to_thread(speech_v2.SpeechClient)
    except Exception as ex:
        logger.error("Create SpeechClient failed: %s", ex)
        return {}

    project_id = os.getenv("GCP_PROJECT_ID", "your-gcp-project")
    location = "global"
    recognizer_parent = f"projects/{project_id}/locations/{location}"
    recognizer_id = "long-audio-recognizer"
    recognizer_name = f"{recognizer_parent}/recognizers/{recognizer_id}"

    # Try to get or create a Recognizer
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
            return {}

    # Prepare files for BatchRecognizeRequest
    batch_files = []
    for user_id, gcs_uri in gcs_uri_dict.items():
        batch_files.append(cloud_speech.BatchRecognizeFileMetadata(uri=gcs_uri))

    # If no files to process, return immediately
    if not batch_files:
        logger.error("No files to process in batch.")
        return {}

    # Batch Recognize
    # We set a single output_gcs_uri to store all results under the same folder,
    # each file result will produce a separate JSON file.
    output_gcs_uri = f"gs://{bucket_name}/transcripts/batch_results/"
    request = cloud_speech.BatchRecognizeRequest(
        recognizer=recognizer.name,
        config=cloud_speech.RecognitionConfig(
            auto_decoding_config=cloud_speech.AutoDetectDecodingConfig(),
            language_codes=[language_code],
            model="long",
            features=cloud_speech.RecognitionFeatures(enable_word_time_offsets=True),
        ),
        files=batch_files,
        recognition_output_config=cloud_speech.RecognitionOutputConfig(
            gcs_output_config=cloud_speech.GcsOutputConfig(uri=output_gcs_uri)
        ),
        processing_strategy=cloud_speech.BatchRecognizeRequest.ProcessingStrategy.DYNAMIC_BATCHING,
    )

    try:
        operation = await asyncio.to_thread(client.batch_recognize, request=request, timeout=int(os.getenv("MAX_WAIT_SECONDS", 86400)))
        logger.info("Batch Recognize operation initiated...")
        response = await asyncio.to_thread(operation.result)
    except Exception as ex:
        logger.error("Batch Recognize failed: %s", ex)
        return {}

    # Dictionary to hold final results: { user_id: [ {offset, duration, text}, ... ] }
    final_results: Dict[str, List[Dict]] = {}

    # Processing the results
    try:
        # response.results is a map of input_uri -> BatchRecognizeFileResult
        for input_uri, file_result in response.results.items():
            # Find which user_id belongs to this input_uri
            matched_user_id = None
            for user_id, uri in gcs_uri_dict.items():
                if uri == input_uri:
                    matched_user_id = user_id
                    break
            if not matched_user_id:
                logger.warning("Unmatched input URI found: %s", input_uri)
                continue

            recognized_output_uri = file_result.uri
            logger.info(f"For user_id: {matched_user_id}, recognized output URI: {recognized_output_uri}")

            match = re.match(r"gs://([^/]+)/(.+)", recognized_output_uri)
            if not match:
                logger.error("Cannot parse GCS URI: %s", recognized_output_uri)
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
            user_results_list = []
            for result in batch_recognize_results.results:
                for alternative in result.alternatives:
                    words = alternative.words
                    if not words:
                        # If no word offsets, entire chunk is 0.0
                        user_results_list.append({
                            "offset": 0.0,
                            "duration": 0.0,
                            "text": alternative.transcript
                        })
                    else:
                        first_word = words[0]
                        last_word = words[-1]
                        # Convert offsets to seconds
                        start_seconds = first_word.start_offset.total_seconds()
                        end_seconds = last_word.end_offset.total_seconds()

                        offset_val = start_seconds
                        duration_val = max(0.0, end_seconds - start_seconds)

                        user_results_list.append({
                            "offset": offset_val,
                            "duration": duration_val,
                            "text": alternative.transcript
                        })

            final_results[matched_user_id] = user_results_list

    except Exception as ex:
        logger.error("Process Batch Recognize results failed: %s", ex)

    # Clean up GCS temp WAV files
    for user_id, wav_path in wav_paths.items():
        # Delete from GCS
        file_name_in_gcs = os.path.basename(wav_path)
        try:
            blob = bucket.blob(file_name_in_gcs)
            await asyncio.to_thread(blob.delete)
            logger.info("Cleaned up GCS temp file for user %s: %s", user_id, file_name_in_gcs)
        except Exception as ex:
            logger.error("Clean up GCS temp file failed for user %s: %s", user_id, ex)

        # Delete local temp WAV file
        try:
            if os.path.exists(wav_path):
                await asyncio.to_thread(os.remove, wav_path)
        except Exception as ex:
            logger.error("Clean up local temp WAV file failed for user %s: %s", user_id, ex)

    return final_results

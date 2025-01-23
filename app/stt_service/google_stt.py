# app/stt_service/google_stt.py
"""
Google Speech-to-Text service module for batch files.
This module demonstrates how to convert multiple MP3 files to WAV,
upload them all to GCS at once, and then invoke a single Batch Recognize
call for improved performance.
"""
import asyncio
import logging
import os
import re
from typing import Dict, List
from itertools import islice

from google.cloud import speech_v2, storage
from google.cloud.speech_v2.types import cloud_speech

from app.utils.general import _convert_to_wav

logger = logging.getLogger(__name__)

def chunks(data: dict, size: int):
    """Split dictionary into chunks of specified size."""
    it = iter(data.items())
    for i in range(0, len(data), size):
        chunk = dict(islice(it, size))
        yield chunk

async def google_stt_with_timeline_batch(audio_file_dict: Dict[str, List[str]]) -> Dict[str, List[Dict]]:
    language_code = os.getenv("SPEECH_LANGUAGE", "en-US")
    bucket_name = os.getenv("GCS_BUCKET_NAME")
    if not bucket_name:
        logger.error("No GCS bucket name provided.")
        return {}

    # let each user_id have a unique flat_id
    flat_audio_file_dict = {}
    user_id_map = {}  # flat_id -> original user_id
    for user_id, file_list in audio_file_dict.items():
        if isinstance(file_list, list):
            for i, file_path in enumerate(file_list):
                flat_id = f"{user_id}_part{i}"
                flat_audio_file_dict[flat_id] = file_path
                user_id_map[flat_id] = user_id
        else:
            flat_audio_file_dict[user_id] = file_list
            user_id_map[user_id] = user_id

    # Convert MP3 files to WAV
    wav_paths = {}
    for flat_id, audio_file_path in flat_audio_file_dict.items():
        wav_path = f"{audio_file_path}.wav"
        try:
            await _convert_to_wav(audio_file_path, wav_path)
            wav_paths[flat_id] = wav_path
        except Exception as error:
            logger.error("Convert MP3 to WAV failed for %s: %s", flat_id, error)

    storage_client = await asyncio.to_thread(storage.Client)
    bucket = storage_client.bucket(bucket_name)

    gcs_uri_dict = {}
    for flat_id, wav_path in wav_paths.items():
        file_name_in_gcs = os.path.basename(wav_path)
        try:
            blob = bucket.blob(file_name_in_gcs)
            await asyncio.to_thread(blob.upload_from_filename, wav_path)
            gcs_uri = f"gs://{bucket_name}/{file_name_in_gcs}"
            gcs_uri_dict[flat_id] = gcs_uri
            logger.info("Uploaded file for %s to GCS: %s", flat_id, gcs_uri)
        except Exception as ex:
            logger.error("WAV upload to GCS failed for %s: %s", flat_id, ex)

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

    final_results: Dict[str, List[Dict]] = {}
    BATCH_SIZE = 10  # Process 10 files at a time

    async def process_batch(batch_dict, batch_number):
        """Process a single batch of files."""
        logger.info("Processing batch %d with %d files", batch_number + 1, len(batch_dict))
        batch_files = []
        for flat_id, gcs_uri in batch_dict.items():
            batch_files.append(cloud_speech.BatchRecognizeFileMetadata(uri=gcs_uri))

        if not batch_files:
            return {}

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
            operation = await asyncio.to_thread(
                client.batch_recognize,
                request=request,
                timeout=int(os.getenv("MAX_WAIT_SECONDS", 86400))
            )
            logger.info("Batch %d: Recognize operation initiated...", batch_number + 1)
        except Exception as ex:
            logger.error("Batch %d: Recognize initiation failed: %s", batch_number + 1, ex)
            return {}

        response = None
        while response is None:
            try:
                response = await asyncio.to_thread(
                    operation.result,
                    timeout=int(os.getenv("MAX_WAIT_SECONDS", 86400))
                )
            except Exception as ex:
                logger.warning("Batch %d: Recognize NOT ready yet: %s", batch_number + 1, ex)
                await asyncio.sleep(30)

        batch_results = {}
        try:
            for input_uri, file_result in response.results.items():
                matched_flat_id = None
                for flat_id, uri in batch_dict.items():
                    if uri == input_uri:
                        matched_flat_id = flat_id
                        break
                if not matched_flat_id:
                    logger.warning("Unmatched input URI found: %s", input_uri)
                    continue

                # Get the original user_id
                original_user_id = user_id_map.get(matched_flat_id, matched_flat_id)

                recognized_output_uri = file_result.uri
                logger.info(f"For flat_id: {matched_flat_id}, recognized output URI: {recognized_output_uri}")

                match = re.match(r"gs://([^/]+)/(.+)", recognized_output_uri)
                if not match:
                    logger.error("Cannot parse GCS URI: %s", recognized_output_uri)
                    continue

                output_bucket, output_object = match.groups()
                result_blob = storage_client.bucket(output_bucket).blob(output_object)
                results_bytes = await asyncio.to_thread(result_blob.download_as_bytes)

                batch_recognize_results = cloud_speech.BatchRecognizeResults.from_json(
                    results_bytes, ignore_unknown_fields=True
                )

                user_results_list = []
                for result in batch_recognize_results.results:
                    for alternative in result.alternatives:
                        words = alternative.words
                        if not words:
                            user_results_list.append({
                                "offset": 0.0,
                                "duration": 0.0,
                                "text": alternative.transcript,
                                "file_path": input_uri
                            })
                        else:
                            first_word = words[0]
                            last_word = words[-1]
                            start_seconds = first_word.start_offset.total_seconds()
                            end_seconds = last_word.end_offset.total_seconds()
                            offset_val = start_seconds
                            duration_val = max(0.0, end_seconds - start_seconds)
                            user_results_list.append({
                                "offset": offset_val,
                                "duration": duration_val,
                                "text": alternative.transcript,
                                "file_path": input_uri
                            })

                if original_user_id in batch_results:
                    batch_results[original_user_id].extend(user_results_list)
                else:
                    batch_results[original_user_id] = user_results_list

        except Exception as ex:
            logger.error("Batch %d: Process results failed: %s", batch_number + 1, ex)
            return {}

        return batch_results

    # Try parallel batch processing first
    try:
        batch_tasks = []
        for batch_number, batch_dict in enumerate(chunks(gcs_uri_dict, BATCH_SIZE)):
            task = asyncio.create_task(process_batch(batch_dict, batch_number))
            batch_tasks.append(task)

        # Process all batches in parallel
        batch_results_list = await asyncio.gather(*batch_tasks, return_exceptions=True)
        
        # Check if any batch failed
        any_failed = False
        for result in batch_results_list:
            if isinstance(result, Exception) or not result:
                any_failed = True
                break

        if any_failed:
            logger.warning("Parallel batch processing failed, falling back to sequential processing")
            # Fall back to sequential processing
            for batch_number, batch_dict in enumerate(chunks(gcs_uri_dict, 1)):  # Process one file at a time
                batch_results = await process_batch(batch_dict, batch_number)
                # Merge results
                for user_id, results in batch_results.items():
                    if user_id in final_results:
                        final_results[user_id].extend(results)
                    else:
                        final_results[user_id] = results
                # Add a small delay between files
                await asyncio.sleep(2)
        else:
            # Merge successful parallel batch results
            for batch_results in batch_results_list:
                for user_id, results in batch_results.items():
                    if user_id in final_results:
                        final_results[user_id].extend(results)
                    else:
                        final_results[user_id] = results

    except Exception as e:
        logger.error("Error during batch processing: %s", e)
        # Fall back to sequential processing
        for batch_number, batch_dict in enumerate(chunks(gcs_uri_dict, 1)):
            batch_results = await process_batch(batch_dict, batch_number)
            # Merge results
            for user_id, results in batch_results.items():
                if user_id in final_results:
                    final_results[user_id].extend(results)
                else:
                    final_results[user_id] = results
            await asyncio.sleep(2)

    # Clean up temp files
    for flat_id, wav_path in wav_paths.items():
        file_name_in_gcs = os.path.basename(wav_path)
        try:
            blob = bucket.blob(file_name_in_gcs)
            await asyncio.to_thread(blob.delete)
            logger.info("Cleaned up GCS temp file for %s: %s", flat_id, file_name_in_gcs)
        except Exception as ex:
            logger.error("Clean up GCS temp file failed for %s: %s", flat_id, ex)

        try:
            if os.path.exists(wav_path):
                await asyncio.to_thread(os.remove, wav_path)
        except Exception as ex:
            logger.error("Clean up local temp WAV file failed for %s: %s", flat_id, ex)

    return final_results


# app/record_meeting.py
"""
Recording logic using Py-cord's Sinks for each user track.
"""
import json
import os
import asyncio
import logging
import subprocess
import tempfile
import time
import shutil
from datetime import datetime
from typing import List, Dict, Any

import discord
from discord.sinks import MP3Sink

from app.stt_service.stt_select import select_stt_function
from app.summary.agents.summary import generate_summary
from app.summary.agents.todolist import generate_todolist
from app.summary.agents.meeting_title import generate_meeting_title
from app.utils.google_drive import upload_to_drive, upload_meeting_files
from app.utils.retry import async_retry

logger = logging.getLogger(__name__)


async def export_audio_async(user_id: int,
                             recorded_audio,
                             output_folder: str,
                             max_segment_duration: int = 3600) -> List[str]:
    """Exports a user's MP3 data to one or multiple MP3 files on disk,
    splitting large audio if needed via FFmpeg.

    This function first writes the recorded MP3 data to a local temp file,
    then uses FFmpeg segment mode to split the file if it exceeds 'max_segment_duration'.

    Args:
        user_id: The Discord user ID.
        recorded_audio: The recorded BytesIO-like audio data (already in MP3 format).
        output_folder: The directory where output files will be saved.
        max_segment_duration: Maximum duration (in seconds) for one segment.
            Files longer than this duration will be split into multiple segments.

    Returns:
        A list of file paths for the exported segments for this user.
    """
    loop = asyncio.get_running_loop()

    def do_export() -> List[str]:
        """Write the recorded audio to a temp file, then use FFmpeg to split it."""
        try:
            # 1) Build a temp file to store the recorded audio
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmpfile:
                recorded_audio.file.seek(0)
                tmpfile.write(recorded_audio.file.read())
                tmpfile.flush()
                tmp_input_path = tmpfile.name

            # 2) Build FFmpeg command to split the audio
            #    -f segment：use segment muxer to split the file
            #    -segment_time：set the duration of each segment
            #    -c copy：copy the audio stream without re-encoding
            #    -y：overwrite existing files
            output_pattern = os.path.join(output_folder, f"{user_id}_part_%03d.mp3")
            command = [
                "ffmpeg",
                "-hide_banner",
                "-y",
                "-i", tmp_input_path,
                "-f", "segment",
                "-segment_time", str(max_segment_duration),
                "-c", "copy",
                output_pattern
            ]

            # 3) Execute FFmpeg command
            logger.info("Running FFmpeg command: %s", " ".join(command))
            ffmpeg_result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False  # Use check=False to handle return code manually
            )

            # Check if FFmpeg failed
            if ffmpeg_result.returncode != 0:
                logger.error(
                    "FFmpeg failed with return code %d.\nSTDOUT: %s\nSTDERR: %s",
                    ffmpeg_result.returncode,
                    ffmpeg_result.stdout.decode("utf-8", errors="ignore"),
                    ffmpeg_result.stderr.decode("utf-8", errors="ignore"),
                )
                return []

            # 4) Collect the output files
            out_paths = []
            for fname in os.listdir(output_folder):
                if fname.startswith(f"{user_id}_part_") and fname.endswith(".mp3"):
                    full_path = os.path.join(output_folder, fname)
                    out_paths.append(full_path)

            out_paths.sort()
            logger.info("Exported user %s audio to %d segment(s): %s",
                        user_id, len(out_paths), out_paths)

            # 5) Clean up the temp input file
            try:
                os.remove(tmp_input_path)
            except OSError:
                pass

            return out_paths

        except Exception as exc:
            logger.error("Failed to export audio for user %s: %s", user_id, exc)
            return []

    # Run the export in a separate thread
    return await loop.run_in_executor(None, do_export)


async def record_meeting_audio(bot, voice_channel_id: int):
    """
    Handles the recording of a voice channel using MP3Sink in an async manner.
    Once recording is finished, it exports all user tracks, then performs
    batch STT (if using Google STT).
    """

    meeting_info = bot.meeting_voice_channel_info.get(voice_channel_id, {})

    guild = bot.guilds[0] if bot.guilds else None
    if not guild:
        logger.error("Guild not found for recording.")
        return

    voice_channel = guild.get_channel(voice_channel_id)
    if not voice_channel or not isinstance(voice_channel, discord.VoiceChannel):
        logger.error("Voice channel %s not found or invalid type.", voice_channel_id)
        return

    # Disconnect if already connected
    for vc in bot.voice_clients:
        if vc.guild == guild:
            try:
                if vc.recording:
                    vc.stop_recording()
                await vc.disconnect(force=True)
            except Exception as e:
                logger.error("Error disconnecting existing voice client: %s", e)

    voice_client = None
    try:
        voice_client = await voice_channel.connect()
        logger.info("Bot %s joined %s for recording.", bot.user.name, voice_channel.name)
    except Exception as error:
        logger.error("Failed to connect to %s: %s", voice_channel.name, error)
        return

    sink = MP3Sink()

    async def finished_callback(sink: MP3Sink, channel_id: int, local_info: dict):
        logger.info("Recording callback triggered for channel: %s", channel_id)

        # Check if generation is already completed
        if local_info.get("generation_completed"):
            logger.info("Generation already completed for channel %s, skipping.", channel_id)
            return

        # Mark generation as completed
        local_info["generation_completed"] = True

        guild_local = bot.guilds[0] if bot.guilds else None
        output_folder = f"recordings_{channel_id}_{int(time.time())}"
        os.makedirs(output_folder, exist_ok=True)

        exported_files = {}
        stt_results = {}
        timeline_segments = []

        # Export all user audio data
        async def export_with_retry(user_id: int, recorded_audio: Any) -> List[str]:
            try:
                # Get user's actual recording time range
                user_join_time = local_info.get("user_join_time", {}).get(user_id)
                user_leave_time = local_info.get("user_leave_time", {}).get(user_id, time.time())
                
                if not user_join_time:
                    logger.warning("No join time found for user %s, using meeting start time", user_id)
                    user_join_time = local_info.get("start_time", time.time())
                
                # Calculate actual recording duration (seconds)
                actual_duration = user_leave_time - user_join_time
                
                # Check recording data
                try:
                    # Check AudioData object content
                    if hasattr(recorded_audio, 'file'):
                        # Save current position
                        current_pos = recorded_audio.file.tell()
                        # Move to end to get size
                        recorded_audio.file.seek(0, 2)
                        audio_size = recorded_audio.file.tell()
                        # Restore original position
                        recorded_audio.file.seek(current_pos)
                        
                        logger.info("Audio data for user %s: size=%d bytes", user_id, audio_size)
                        if audio_size > 0:
                            logger.info("User %s has valid audio data", user_id)
                        else:
                            logger.warning("Audio data for user %s is empty", user_id)
                    else:
                        logger.error("Invalid audio data format for user %s: %s", user_id, type(recorded_audio))
                except Exception as e:
                    logger.error("Error checking audio data for user %s: %s", user_id, e)
                
                # Try to export audio regardless, let FFmpeg handle the actual audio data
                result = await async_retry(
                    export_audio_async,
                    user_id, recorded_audio, output_folder,
                    max_attempts=5,
                    delay=10.0
                )
                
                if result:
                    logger.info("Successfully exported audio for user %s: %s", user_id, result)
                    return result
                else:
                    logger.error("Failed to export audio for user %s", user_id)
                    return []
                    
            except Exception as e:
                logger.error("Error processing audio for user %s: %s", user_id, e)
                return []

        # Create export tasks, ensure all valid recordings are included
        export_tasks = {}
        for user_id, recorded_audio in sink.audio_data.items():
            logger.info("Processing audio data for user %s", user_id)
            # Check if recording data is valid
            try:
                has_audio = bool(recorded_audio and hasattr(recorded_audio, 'file') and recorded_audio.file)
                if has_audio:
                    # Get BytesIO object size
                    try:
                        # Save current position
                        current_pos = recorded_audio.file.tell()
                        # Move to end to get size
                        recorded_audio.file.seek(0, 2)
                        audio_size = recorded_audio.file.tell()
                        # Restore original position
                        recorded_audio.file.seek(current_pos)
                        
                        logger.info("Valid audio data found for user %s with size %d bytes", user_id, audio_size)
                        if audio_size > 0:  # If there is actual audio data
                            export_tasks[user_id] = export_with_retry(user_id, recorded_audio)
                            logger.info("Added export task for user %s with audio size %d bytes", user_id, audio_size)
                        else:
                            logger.warning("Audio data for user %s is empty (size=0)", user_id)
                    except Exception as e:
                        logger.error("Error getting audio size for user %s: %s", user_id, e)
                        # If unable to get size, still try to process the audio
                        export_tasks[user_id] = export_with_retry(user_id, recorded_audio)
                        logger.info("Added export task for user %s (size unknown)", user_id)
                else:
                    logger.warning("Invalid or empty audio data for user %s", user_id)
            except Exception as e:
                logger.error("Error checking audio data for user %s: %s", user_id, e)

        logger.info("Starting export tasks for %d users", len(export_tasks))
        export_results = await asyncio.gather(*export_tasks.values(), return_exceptions=True)
        
        success_count = 0
        for user_id, result in zip(export_tasks.keys(), export_results):
            if isinstance(result, Exception):
                logger.error("Error exporting audio for user %s: %s", user_id, result)
            else:
                exported_files[user_id] = result
                success_count += 1
        
        logger.info("Successfully exported audio for %d/%d users", success_count, len(export_tasks))

        # Save meeting metadata
        async def save_metadata():
            meeting_metadata = {
                "channel_id": channel_id,
                "guild_id": guild_local.id if guild_local else None,
                "start_time": local_info.get("start_time", time.time()),
                "end_time": time.time(),
                "participants": list(sink.audio_data.keys()),
            }
            
            metadata_path = os.path.join(output_folder, "metadata.json")
            def _save():
                with open(metadata_path, "w", encoding="utf-8") as f:
                    json.dump(meeting_metadata, f, ensure_ascii=False, indent=4)
            
            await async_retry(
                lambda: asyncio.to_thread(_save),
                max_attempts=3,
                delay=10.0
            )
            return metadata_path

        metadata_path = await save_metadata()

        # Perform batch STT
        async def perform_stt():
            try:
                stt_func = select_stt_function(batch=True)
                return await async_retry(
                    stt_func,
                    exported_files,
                    max_attempts=3,
                    delay=60.0
                )
            except Exception as e:
                logger.error("Error in STT processing: %s", e)
                return {}

        raw_stt_outputs = await perform_stt()
        for user_id, stt_output in raw_stt_outputs.items():
            stt_results[user_id] = stt_output

        # Generate timeline
        async def generate_timeline():
            try:
                logger.info("Starting timeline generation with %d STT results", len(stt_results))
                for user_id, segments in stt_results.items():
                    if guild_local:
                        member = guild_local.get_member(user_id)
                        user_name = member.display_name if member else str(user_id)
                    else:
                        user_name = str(user_id)

                    # Get user's join time
                    user_join_time = local_info.get("user_join_time", {}).get(user_id)
                    if not user_join_time:
                        user_join_time = local_info.get("start_time", time.time())

                    # Extract part number and calculate time offset
                    for segment in segments:
                        # Skip empty segments
                        if not segment["text"].strip():
                            continue

                        # Calculate the actual time offset
                        segment_offset = segment["offset"]
                        # Check if this is from a split file
                        file_path = segment.get("file_path", "")
                        if file_path:
                            # Extract part number from file name (e.g., user_id_part_001.mp3)
                            import re
                            match = re.search(r'_part_(\d+)\.', file_path)
                            if match:
                                part_num = int(match.group(1))
                                # Add the offset for previous parts (3600 seconds per part)
                                segment_offset += part_num * 3600

                        absolute_time = datetime.fromtimestamp(
                            user_join_time + segment_offset
                        ).strftime("%Y-%m-%d %H:%M:%S")
                        timeline_segments.append((absolute_time, user_name, segment["text"]))
                        logger.debug("Added segment: [%s] %s: %s (offset: %f)", 
                                   absolute_time, user_name, segment["text"], segment_offset)

                # Sort segments by time
                timeline_segments.sort(key=lambda x: x[0])
                logger.info("Generated %d timeline segments", len(timeline_segments))

                # Build the transcript
                lines = []
                for t, uid, text in timeline_segments:
                    if not text.strip():
                        continue
                    lines.append(f"[{t}] <@{uid}>: {text}")
                meeting_transcript = "\n".join(lines)
                
                if meeting_transcript.strip():
                    logger.info("Generated transcript with %d lines", len(lines))
                else:
                    logger.warning("Generated transcript is empty")

                # Save timeline segments
                timeline_path = os.path.join(output_folder, "timeline.json")
                def _save_timeline():
                    with open(timeline_path, "w", encoding="utf-8") as f:
                        json.dump(timeline_segments, f, ensure_ascii=False, indent=4)
                
                await async_retry(
                    lambda: asyncio.to_thread(_save_timeline),
                    max_attempts=3,
                    delay=10.0
                )

                return meeting_transcript, timeline_path

            except Exception as e:
                logger.error("Error generating timeline: %s", e)
                return "", None

        meeting_transcript, timeline_path = await generate_timeline()

        # Generate meeting title
        meeting_start_time = datetime.fromtimestamp(local_info.get("start_time", time.time()))
        
        # Check if there is a valid transcript
        has_transcript = bool(meeting_transcript and meeting_transcript.strip() and 
                            meeting_transcript != os.getenv("NO_TRANSCRIPT_MESSAGE", "No transcript available."))
        
        if has_transcript:
            meeting_title = await async_retry(
                generate_meeting_title,
                meeting_transcript,
                meeting_start_time,
                max_attempts=3,
                delay=10.0
            )
            if not meeting_title:
                meeting_title = f"[{meeting_start_time.strftime('%Y%m%d%H%M%S')}] " + os.getenv("NO_MEETING_TITLE_MESSAGE", "Meeting")
        else:
            meeting_title = f"[{meeting_start_time.strftime('%Y%m%d%H%M%S')}] " + os.getenv("NO_MEETING_TITLE_MESSAGE", "Meeting")
        
        # Update forum thread title if exists
        thread_id = local_info.get("forum_thread_id")
        if thread_id and bot.meeting_forum_thread_info.get(thread_id):
            try:
                thread = bot.meeting_forum_thread_info[thread_id]
                await thread.edit(name=meeting_title)
                logger.info("Updated forum thread title to: %s", meeting_title)
            except Exception as e:
                logger.error("Failed to update forum thread title: %s", e)

        # Save meeting transcript
        async def save_transcript():
            transcript_path = os.path.join(output_folder, "transcript.txt")
            def _save():
                with open(transcript_path, "w", encoding="utf-8") as f:
                    f.write(meeting_transcript or os.getenv("NO_TRANSCRIPT_MESSAGE", "No transcript available."))
            
            await async_retry(
                lambda: asyncio.to_thread(_save),
                max_attempts=3,
                delay=10.0
            )
            return transcript_path

        transcript_path = await save_transcript()

        # Do summary and to-do list generation
        async def generate_summary_and_todo():
            if meeting_transcript.strip():
                summary = await async_retry(
                    generate_summary,
                    meeting_transcript,
                    max_attempts=3,
                    delay=60.0
                )
                todolist = await async_retry(
                    generate_todolist,
                    meeting_transcript,
                    max_attempts=3,
                    delay=60.0
                )
            else:
                no_message = os.getenv("NO_TRANSCRIPT_MESSAGE", "No transcript available.")
                summary = todolist = no_message

            # Save summary and to-do list
            summary_path = os.path.join(output_folder, "summary.txt")
            todolist_path = os.path.join(output_folder, "todolist.txt")
            
            def _save_summary():
                with open(summary_path, "w", encoding="utf-8") as f:
                    f.write(summary)
                
            def _save_todolist():
                with open(todolist_path, "w", encoding="utf-8") as f:
                    f.write(todolist)

            await asyncio.gather(
                async_retry(lambda: asyncio.to_thread(_save_summary), max_attempts=5, delay=10.0),
                async_retry(lambda: asyncio.to_thread(_save_todolist), max_attempts=5, delay=10.0)
            )

            return summary, todolist, summary_path, todolist_path

        meeting_summary, meeting_todolist, summary_path, todolist_path = await generate_summary_and_todo()

        # Upload files to Google Drive if configured
        drive_folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID")
        if drive_folder_id:
            try:
                # Prepare file paths
                file_paths = {}
                
                # Only include files that exist and have content
                if has_transcript:
                    file_paths.update({
                        "transcript": transcript_path,
                        "summary": summary_path,
                        "todolist": todolist_path,
                    })
                
                # Always include metadata and timeline if they exist
                if os.path.exists(metadata_path):
                    file_paths["metadata"] = metadata_path
                if os.path.exists(timeline_path):
                    file_paths["timeline"] = timeline_path
                
                # Get user names
                user_names = {}
                for user_id in exported_files.keys():
                    if guild_local:
                        member = guild_local.get_member(user_id)
                        user_names[user_id] = member.display_name if member else str(user_id)
                    else:
                        user_names[user_id] = str(user_id)
                
                # Upload all files with cleanup
                success = await async_retry(
                    upload_meeting_files,
                    meeting_title,
                    file_paths,
                    exported_files,
                    user_names,
                    drive_folder_id,
                    output_folder,  # Pass local folder for cleanup
                    max_attempts=6,
                    delay=20.0
                )
                
                if success:
                    logger.info("Successfully uploaded all meeting files to Google Drive folder: %s", meeting_title)
                    
                    # Clean up local files after successful upload
                    try:
                        if os.path.exists(output_folder):
                            await asyncio.to_thread(shutil.rmtree, output_folder)
                            logger.info("Successfully cleaned up local folder: %s", output_folder)
                    except Exception as e:
                        logger.error("Failed to clean up local folder %s: %s", output_folder, e)
                else:
                    logger.error("Failed to upload meeting files to Google Drive after all retries")
                    
            except Exception as e:
                logger.error("Error during Google Drive upload: %s", e)

        # Update local info
        local_info.update({
            "meeting_transcript": meeting_transcript or os.getenv("NO_TRANSCRIPT_MESSAGE", "No transcript available."),
            "meeting_summary": meeting_summary,
            "meeting_todolist": meeting_todolist
        })

        if channel_id in bot.meeting_voice_channel_info:
            bot.meeting_voice_channel_info[channel_id].update(local_info)

    # Start recording with error handling
    try:
        voice_client.start_recording(sink, finished_callback, voice_channel_id, meeting_info, sync_start=True)
        
        if voice_channel_id in bot.meeting_voice_channel_info:
            bot.meeting_voice_channel_info[voice_channel_id]["recording_task"] = asyncio.current_task()
            bot.meeting_voice_channel_info[voice_channel_id]["voice_client"] = voice_client

        # Keep the recording task alive without blocking other tasks
        while True:
            if not voice_client.is_connected():
                logger.info("Voice client disconnected, stopping recording.")
                break
            await asyncio.sleep(1)

    except asyncio.CancelledError:
        logger.info("Recording task was canceled. Stopping recording.")
        if voice_client and voice_client.is_connected():
            try:
                if voice_client.recording:
                    voice_client.stop_recording()
                await voice_client.disconnect(force=True)
            except Exception as e:
                logger.error("Error during cleanup after cancellation: %s", e)
        raise

    except Exception as exc:
        logger.error("Error in recording task: %s", exc)

    finally:
        # Clean up resources
        try:
            if voice_client:
                if voice_client.is_connected():
                    if voice_client.recording:
                        voice_client.stop_recording()
                    await voice_client.disconnect(force=True)
                
            # Clean up meeting info
            if voice_channel_id in bot.meeting_voice_channel_info:
                info = bot.meeting_voice_channel_info[voice_channel_id]
                info["recording_task"] = None
                info["voice_client"] = None
        except Exception as e:
            logger.error("Error during final cleanup: %s", e)

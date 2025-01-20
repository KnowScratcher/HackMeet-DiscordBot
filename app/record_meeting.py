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
from datetime import datetime
from typing import List

import discord
from discord.sinks import MP3Sink

from app.stt_service.stt_select import select_stt_function
from app.summary.agents.summary import generate_summary
from app.summary.agents.todolist import generate_todolist
# from app.utils.google_drive import upload_to_drive

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
            await vc.disconnect(force=True)

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
        export_tasks = {
            user_id: export_audio_async(user_id, recorded_audio, output_folder)
            for user_id, recorded_audio in sink.audio_data.items()
        }
        export_results = await asyncio.gather(*export_tasks.values(), return_exceptions=True)

        for user_id, result in zip(export_tasks.keys(), export_results):
            if isinstance(result, Exception) or result is None:
                logger.error("Error exporting audio for user %s: %s", user_id, result)
            else:
                exported_files[user_id] = result

        # Save meeting metadata
        meeting_metadata = {
            "channel_id": channel_id,
            "guild_id": guild_local.id if guild_local else None,
            "start_time": local_info.get("start_time", time.time()),
            "end_time": time.time(),
            "participants": list(sink.audio_data.keys()),
        }

        metadata_path = os.path.join(output_folder, "metadata.json")
        try:
            with open(metadata_path, "w", encoding="utf-8") as f:
                json.dump(meeting_metadata, f, ensure_ascii=False, indent=4)
            logger.info("Saved meeting metadata to %s", metadata_path)
        except Exception as e:
            logger.error("Failed to save meeting metadata: %s", e)

        # Perform batch STT
        try:
            # 1) Select STT function
            stt_func = select_stt_function(batch=True)

            # 2) Call STT function with exported files
            raw_stt_outputs = await stt_func(exported_files)

            # 3) Process STT results
            for user_id, stt_output in raw_stt_outputs.items():
                stt_results[user_id] = stt_output

        except Exception as e:
            logger.error("Error processing STT (batch) for channel %s: %s", channel_id, e)

        # Combine segments for a timeline
        try:
            for user_id, segments in stt_results.items():
                # Get user display name
                if guild_local:
                    member = guild_local.get_member(user_id)
                    if member:
                        user_name = member.display_name
                    else:
                        user_obj = bot.get_user(user_id)
                        user_name = user_obj.name if user_obj else str(user_id)
                else:
                    user_name = str(user_id)

                for segment in segments:
                    # Skip empty segments
                    if not segment["text"].strip():
                        continue
                    absolute_time = datetime.fromtimestamp(
                        local_info.get("start_time", time.time()) + segment["offset"]
                    ).strftime("%Y-%m-%d %H:%M:%S")
                    timeline_segments.append((absolute_time, user_name, segment["text"]))

            # Sort segments by time
            timeline_segments.sort(key=lambda x: x[0])

            # Build the transcript
            lines = []
            for t, uid, text in timeline_segments:
                if not text.strip():
                    continue
                lines.append(f"[{t}] <@{uid}>: {text}")
            meeting_transcript = "\n".join(lines)

        except Exception as e:
            logger.error("Error constructing timeline segments: %s", e)
            meeting_transcript = ""

        # Save timeline segments
        try:
            timeline_path = os.path.join(output_folder, "timeline.json")
            with open(timeline_path, "w", encoding="utf-8") as f:
                json.dump(timeline_segments, f, ensure_ascii=False, indent=4)
            logger.info("Saved timeline segments to %s", timeline_path)
        except Exception as e:
            logger.error("Failed to save timeline segments: %s", e)

        # Save meeting transcript
        try:
            transcript_path = os.path.join(output_folder, "transcript.txt")
            with open(transcript_path, "w", encoding="utf-8") as f:
                f.write(meeting_transcript or os.getenv("NO_TRANSCRIPT_MESSAGE", "No transcript available."))
            logger.info("Saved meeting transcript to %s", transcript_path)
        except Exception as e:
            logger.error("Failed to save meeting transcript: %s", e)

        # Do summary and to-do list generation
        try:
            if meeting_transcript.strip():
                meeting_summary = await generate_summary(meeting_transcript)
                meeting_todolist = await generate_todolist(meeting_transcript)
            else:
                no_message = os.getenv("NO_TRANSCRIPT_MESSAGE", "No transcript available.")
                meeting_summary = no_message
                meeting_todolist = no_message

            # Save summary and to-do list
            summary_path = os.path.join(output_folder, "summary.txt")
            todolist_path = os.path.join(output_folder, "todolist.txt")
            with open(summary_path, "w", encoding="utf-8") as f:
                f.write(meeting_summary)
            with open(todolist_path, "w", encoding="utf-8") as f:
                f.write(meeting_todolist)
            logger.info("Saved meeting summary to %s and to-do list to %s", summary_path, todolist_path)

            # Update local info
            local_info["meeting_transcript"] = meeting_transcript or os.getenv("NO_TRANSCRIPT_MESSAGE",
                                                                               "No transcript available.")
            local_info["meeting_summary"] = meeting_summary
            local_info["meeting_todolist"] = meeting_todolist

        except Exception as e:
            logger.error("Error generating summary or to-do list: %s", e)
        #
        # # Upload files to Google Drive if configured
        # drive_folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID")
        # if drive_folder_id:
        #     try:
        #         # Upload transcript
        #         if os.path.exists(transcript_path):
        #             await upload_to_drive(transcript_path, drive_folder_id)
        #
        #         # Upload summary
        #         if os.path.exists(summary_path):
        #             await upload_to_drive(summary_path, drive_folder_id)
        #
        #         # Upload todolist
        #         if os.path.exists(todolist_path):
        #             await upload_to_drive(todolist_path, drive_folder_id)
        #
        #         # Upload metadata
        #         if os.path.exists(metadata_path):
        #             await upload_to_drive(metadata_path, drive_folder_id)
        #
        #         # Upload timeline
        #         if os.path.exists(timeline_path):
        #             await upload_to_drive(timeline_path, drive_folder_id)
        #
        #         logger.info("Successfully uploaded meeting files to Google Drive")
        #     except Exception as e:
        #         logger.error("Failed to upload files to Google Drive: %s", e)

        # Update local info
        if channel_id in bot.meeting_voice_channel_info:
            bot.meeting_voice_channel_info[channel_id].update(local_info)

    # Start recording
    voice_client.start_recording(sink, finished_callback, voice_channel_id, meeting_info, sync_start=True)

    if voice_channel_id in bot.meeting_voice_channel_info:
        bot.meeting_voice_channel_info[voice_channel_id]["recording_task"] = asyncio.current_task()

    # Keep the recording task alive without blocking other tasks
    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        logger.info("Recording task was canceled. Stopping recording.")
        voice_client.stop_recording()
        raise
    except Exception as exc:
        logger.error("Error in recording task: %s", exc)
    finally:
        if voice_client.is_connected():
            await voice_client.disconnect(force=True)

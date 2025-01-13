# app/record_meeting.py
"""
Recording logic using Py-cord's Sinks for each user track.
"""

import os
import asyncio
import logging
import time
from datetime import datetime

import discord
from pydub import AudioSegment
from discord.sinks import MP3Sink

from app.stt_service.stt_select import select_stt_function
from app.summary.agents.summary import generate_summary
from app.summary.agents.todolist import generate_todolist

logger = logging.getLogger(__name__)


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

        guild_local = bot.guilds[0] if bot.guilds else None
        output_folder = f"recordings_{channel_id}"
        os.makedirs(output_folder, exist_ok=True)

        exported_files = {}
        stt_results = {}

        # export audio files
        async def export_audio_async(user_id, recorded_audio):
            """
            Export the user's MP3 data to an actual MP3 file on disk.
            """
            loop = asyncio.get_running_loop()

            def do_export():
                try:
                    logger.info("User %s recorded file: %s", user_id, recorded_audio.file)
                    user_segment = AudioSegment.from_file(recorded_audio.file, format="mp3")
                    out_path = os.path.join(output_folder, f"{user_id}.mp3")
                    user_segment.export(out_path, format="mp3")
                    logger.info("Exported user %s audio to %s", user_id, out_path)
                    return out_path
                except Exception as e:
                    raise e

            return await loop.run_in_executor(None, do_export)

        # Export audio for each user
        export_tasks = {
            user_id: export_audio_async(user_id, recorded_audio)
            for user_id, recorded_audio in sink.audio_data.items()
        }
        export_results = await asyncio.gather(*export_tasks.values(), return_exceptions=True)

        for user_id, result in zip(export_tasks.keys(), export_results):
            if isinstance(result, Exception):
                logger.error("Error exporting audio for user %s: %s", user_id, result)
            else:
                exported_files[user_id] = result

        # STT: Batch or single?
        try:
            # 1) Select STT function
            stt_func = select_stt_function(batch=True)

            # 2) Call STT function with exported files
            #    key: user_id, value: mp3_file_path
            raw_stt_outputs = await stt_func(exported_files)

            # raw_stt_outputs are dict: { user_id: [ {offset, duration, text}, ... ] }
            for user_id, stt_output in raw_stt_outputs.items():
                stt_results[user_id] = stt_output

        except Exception as e:
            logger.error("Error processing STT (batch) for channel %s: %s", channel_id, e)

        # Combine segments for a timeline
        timeline_segments = []
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

        # Put into dict so forum thread can fetch
        if not meeting_transcript.strip():
            local_info["meeting_transcript"] = os.getenv("NO_TRANSCRIPT_MESSAGE", "No transcript available.")
            local_info["meeting_summary"] = os.getenv("NO_TRANSCRIPT_MESSAGE", "No transcript available.")
            local_info["meeting_todolist"] = os.getenv("NO_TRANSCRIPT_MESSAGE", "No transcript available.")
        else:
            local_info["meeting_transcript"] = meeting_transcript
            local_info["meeting_summary"] = await generate_summary(meeting_transcript)
            local_info["meeting_todolist"] = await generate_todolist(meeting_transcript)

        # Debug logs
        logger.info("Meeting transcript: %s", local_info["meeting_transcript"])
        logger.info("Meeting summary: %s", local_info["meeting_summary"])
        logger.info("Meeting to-do list: %s", local_info["meeting_todolist"])

        # Update the meeting info
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

# app/record_meeting.py
"""
Recording logic using Py-cord's Sinks for each user track.
"""

import os
import asyncio
import logging
from datetime import datetime

import discord
from pydub import AudioSegment
from discord.sinks import MP3Sink

from app.stt_service.stt_select import select_stt_function
from app.summary.agents.summary import generate_summary
from app.summary.agents.todolist import generate_todolist

logger = logging.getLogger(__name__)

async def record_meeting_audio(bot, voice_channel_id: int):
    """Handles the recording of a voice channel using MP3Sink."""
    guild = bot.guilds[0] if bot.guilds else None
    if not guild:
        logger.error("Guild not found for recording.")
        return

    voice_channel = guild.get_channel(voice_channel_id)
    if not voice_channel or not isinstance(voice_channel, discord.VoiceChannel):
        logger.error("Voice channel %s not found or invalid type.", voice_channel_id)
        return

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

    async def finished_callback(sink: MP3Sink, channel_id: int):
        logger.info("Recording callback triggered for channel: %s", channel_id)
        info = bot.meeting_voice_channel_info.get(channel_id)
        if not info:
            return

        guild = bot.guilds[0] if bot.guilds else None  # for get username
        output_folder = f"recordings_{channel_id}"
        os.makedirs(output_folder, exist_ok=True)

        stt_results = {}
        for user_id, recorded_audio in sink.audio_data.items():
            logger.info("User %s recorded file: %s", user_id, recorded_audio.file)
            user_segment = AudioSegment.from_file(recorded_audio.file, format="mp3")
            out_path = os.path.join(output_folder, f"{user_id}.mp3")
            user_segment.export(out_path, format="mp3")
            logger.info("Exported user %s audio to %s", user_id, out_path)

            stt_func = select_stt_function()
            stt_text = stt_func(out_path)

            stt_results[user_id] = stt_text

        # Integrate all paragraphs and sort them by time
        timeline_segments = []
        for user_id, segments in stt_results.items():
            for segment in segments:
                # Calculate the absolute time
                absolute_time = datetime.fromtimestamp(info["start_time"] + segment["offset"]).strftime("%Y-%m-%d %H:%M:%S")
                timeline_segments.append((absolute_time, user_id, segment["text"]))

        timeline_segments = []
        for user_id, segments in stt_results.items():
            # get username from user_id
            if not guild:
                user_name = user_id
            else:
                member = guild.get_member(user_id)
                if member:
                    user_name = member.display_name
                else:
                    user = bot.get_user(user_id)
                    user_name = user.name if user else user_id

            for segment in segments:
                absolute_time = datetime.fromtimestamp(info["start_time"] + segment["offset"]).strftime("%Y-%m-%d %H:%M:%S")
                timeline_segments.append((absolute_time, user_name, segment["text"]))


        # Sort the timeline segments by time
        timeline_segments.sort(key=lambda x: x[0])

        # Combination all segments into a single summary text
        lines = []
        for t, uid, text in timeline_segments:
            lines.append(f"[{t}] <@{uid}>: {text}")
        meeting_transcript = "\n".join(lines)

        info["meeting_transcript"] = meeting_transcript
        info["meeting_summary"] = await generate_summary(meeting_transcript)
        info["meeting_todolist"] = await generate_todolist(meeting_transcript)

        print(info["meeting_transcript"])
        print(info["meeting_summary"])
        print(info["meeting_todolist"])

        # thread_id = info.get("forum_thread_id")
        # thread = bot.meeting_forum_thread_info.get(thread_id) if thread_id else None
        #
        # if thread:
        #     for uid, transcript in stt_results.items():
        #         # TODO: Add padding or remove this logic
        #         padded_path = os.path.join(output_folder, f"{uid}_padded.mp3")
        #
        #         if not os.path.exists(padded_path):
        #             padded_path = os.path.join(output_folder, f"{uid}.mp3")
        #
        #         await thread.send(
        #             content=f"User <@{uid}> audio file:\nSTT result: {transcript}",
        #             file=discord.File(padded_path, filename=f"{uid}.mp3")
        #         )

    voice_client.start_recording(
        sink,
        finished_callback,
        voice_channel_id,
        sync_start=True
    )

    if voice_channel_id in bot.meeting_voice_channel_info:
        bot.meeting_voice_channel_info[voice_channel_id]["recording_task"] = asyncio.current_task()

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

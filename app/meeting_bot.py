# app/meeting_bot.py
"""
A single Bot instance that handles meeting operations.
"""
import os
import time
import logging
import asyncio
import discord

from datetime import timedelta
from typing import Dict
from discord.ext import commands
from discord import VoiceState, Intents, Member, ForumChannel

from app.forum import post_with_file
from app.utils import generate_meeting_room_name

logger = logging.getLogger(__name__)

class MeetingBot(commands.Bot):
    """A single Bot instance for meeting and recording."""

    def __init__(
        self,
        bot_token: str,
        manager,
        command_prefix: str = "!",
        intents: discord.Intents = Intents.all(),
    ):
        super().__init__(command_prefix=command_prefix, intents=intents)
        self.bot_token = bot_token
        self.manager = manager
        self.meeting_voice_channel_info: Dict[int, dict] = {}
        self.meeting_forum_thread_info: Dict[int, discord.Thread] = {}

    async def on_ready(self):
        logger.info("Bot %s started. ID: %s", self.user, self.user.id)

    async def on_voice_state_update(
        self,
        member: Member,
        before: VoiceState,
        after: VoiceState
    ):
        if member.bot:
            return

        if after.channel and (not before.channel or before.channel.id != after.channel.id):
            if os.getenv("DISCORD_MEETING_ROOM_NAME") == after.channel.name:
                # Only the first Bot in manager creates a new meeting room
                if self != self.manager.bots[0]:
                    return

                category = after.channel.category
                new_channel_name = generate_meeting_room_name()
                try:
                    overwrites = {
                        member.guild.default_role: discord.PermissionOverwrite(
                            connect=True, speak=True
                        )
                    }

                    # Create a new voice channel
                    meeting_channel = await category.create_voice_channel(
                        name=new_channel_name,
                        overwrites=overwrites
                    )
                    await member.move_to(meeting_channel)

                    forum_channel = None
                    forum_channel_name = os.getenv("DISCORD_MEETING_NOTE_FORUM_NAME")
                    for ch in category.channels:
                        if isinstance(ch, ForumChannel) and ch.name == forum_channel_name:
                            forum_channel = ch
                            break

                    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
                    thread = None
                    if forum_channel:
                        content_template = os.getenv(
                            "MEETING_FORUM_CONTENT",
                            "**Initiator**: {initiator}\n**Start Time**: {time}\n**Channel**: {channel}\n\nParticipant {initiator} joined the meeting."
                        )
                        content = content_template.format(
                            initiator=member.mention,
                            time=now_str,
                            channel=meeting_channel.mention
                        )
                        post = await self.manager.create_forum_post_override(
                            forum_channel=forum_channel,
                            title=new_channel_name,
                            content=content
                        )
                        thread = post if post else None

                    self.meeting_voice_channel_info[meeting_channel.id] = {
                        "start_time": time.time(),
                        "active_participants": {member.id},
                        "all_participants": {member.id},
                        "forum_thread_id": thread.id if thread else None,
                        "summary_message_id": None,
                        "recording_task": None,
                        "user_join_time": {
                            member.id: time.time()
                        },
                    }
                    if thread:
                        self.meeting_forum_thread_info[thread.id] = thread

                    logger.info("Created new meeting room: %s", new_channel_name)
                    await self.manager.handle_new_meeting(meeting_channel.id)

                except Exception as error:
                    logger.error("Failed to create new meeting room: %s", error)
                    return

            elif after.channel.id in self.meeting_voice_channel_info:
                info = self.meeting_voice_channel_info[after.channel.id]
                info["active_participants"].add(member.id)
                info["all_participants"].add(member.id)
                if member.id not in info["user_join_time"]:
                    info["user_join_time"][member.id] = time.time()

                thread_id = info["forum_thread_id"]
                if thread_id:
                    thread = self.meeting_forum_thread_info.get(thread_id)
                    if thread:
                        try:
                            join_message_template = os.getenv(
                                "MEETING_JOIN_MESSAGE",
                                "{member} joined the meeting."
                            )
                            await thread.send(join_message_template.format(member=member.mention))
                        except Exception as exc:
                            logger.error("Cannot update forum thread (join): %s", exc)

        if before.channel and before.channel.id in self.meeting_voice_channel_info:
            info = self.meeting_voice_channel_info[before.channel.id]
            if member.id in info["active_participants"]:
                info["active_participants"].remove(member.id)

            thread_id = info.get("forum_thread_id")
            if thread_id:
                thread = self.meeting_forum_thread_info.get(thread_id)
                if thread:
                    try:
                        leave_message_template = os.getenv(
                            "MEETING_LEAVE_MESSAGE",
                            "{member} left the meeting."
                        )
                        await thread.send(leave_message_template.format(member=member.mention))
                    except Exception as exc:
                        logger.error("Cannot update forum thread (leave): %s", exc)

            voice_channel = before.channel
            if not any(m for m in voice_channel.members if not m.bot):
                logger.info(
                    "Channel %s has no human participants. "
                    "Will close in 5 seconds.",
                    voice_channel.name
                )
                await self.close_meeting_after_delay(voice_channel.id, delay_seconds=5)

    async def close_meeting_after_delay(self, channel_id: int, delay_seconds: int = 300):
        """Wait a given number of seconds, then close the meeting if no users remain."""
        await asyncio.sleep(delay_seconds)
        guild = self.guilds[0] if self.guilds else None
        if not guild:
            return

        voice_channel = guild.get_channel(channel_id)
        if not voice_channel:
            return

        if not any(m for m in voice_channel.members if not m.bot):
            info = self.meeting_voice_channel_info.get(channel_id)
            if not info:
                return

            start_time_ts = info["start_time"]
            all_participants = info["all_participants"]
            thread_id = info["forum_thread_id"]
            thread = self.meeting_forum_thread_info.get(thread_id) if thread_id else None

            end_time = time.time()
            duration_sec = end_time - start_time_ts
            duration_str = str(timedelta(seconds=int(duration_sec)))

            recording_task = info.get("recording_task")
            if recording_task and not recording_task.done():
                recording_task.cancel()
                logger.info("Recording task stopped.")

            if thread:
                ended_message_template = os.getenv(
                    "MEETING_ENDED_MESSAGE",
                    "### Meeting Ended\n**Duration**: {duration}\n**Participants**: {participants}\n"
                )
                msg = ended_message_template.format(
                    duration=duration_str,
                    participants=' '.join([f'<@{pid}>' for pid in all_participants])
                )
                await thread.send(msg)

                generating_summary_message = os.getenv(
                    "GENERATING_SUMMARY_MESSAGE",
                    "Generating meeting summary..."
                )
                processing_msg = await thread.send(generating_summary_message)
                info["summary_message_id"] = processing_msg.id

            try:
                await voice_channel.delete(reason="Meeting ended.")
            except Exception as error:
                logger.error("Failed to delete voice channel: %s", error)

            meeting_transcript = info.get("meeting_transcript", "No transcript available")
            meeting_summary = info.get("meeting_summary", "No summary available")
            meeting_todolist = info.get("meeting_todolist", "No to-do list available")

            if thread:
                summary_msg_id = info.get("summary_message_id")
                if summary_msg_id:
                    try:
                        msg_to_delete = await thread.fetch_message(summary_msg_id)
                        await msg_to_delete.delete()
                    except Exception as exc:
                        logger.warning("Failed to delete 'processing' message: %s", exc)

                await post_with_file(thread, meeting_transcript, message_template=os.getenv("TRANSCRIBING_MESSAGE"))
                await post_with_file(thread, meeting_summary, message_template=os.getenv("SUMMARY_MESSAGE"))
                await post_with_file(thread, meeting_todolist, message_template=os.getenv("TODOLIST_MESSAGE"))

            if channel_id in self.meeting_voice_channel_info:
                del self.meeting_voice_channel_info[channel_id]
            if thread_id in self.meeting_forum_thread_info:
                del self.meeting_forum_thread_info[thread_id]
        else:
            logger.info(
                "Channel %s had new participants within %d seconds. Cancel closing.",
                voice_channel.name,
                delay_seconds
            )

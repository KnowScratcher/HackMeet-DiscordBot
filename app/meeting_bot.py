# app/meeting_bot.py
"""
A single Bot instance that handles meeting and recording.
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
        # Ignore if it's a bot
        if member.bot:
            return

        # A user joins a voice channel
        if after.channel and (not before.channel or before.channel.id != after.channel.id):
            # Check if the user joined the "trigger channel"
            trigger_channel_name = os.getenv("DISCORD_MEETING_ROOM_NAME")
            if trigger_channel_name == after.channel.name:
                # Check if the user is in a valid voice channel
                if any(ch.id == after.channel.id for ch in member.guild.voice_channels):
                    pass

                assigned_bot = self.manager.assign_bot_for_meeting()
                if assigned_bot is not self:
                    # if the bot is not assigned to the user, return
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
                        "user_join_time": {member.id: time.time()},
                    }
                    if thread:
                        self.meeting_forum_thread_info[thread.id] = thread

                    logger.info("Created new meeting room: %s", new_channel_name)
                    await self.manager.handle_new_meeting(meeting_channel.id)

                except Exception as error:
                    logger.error("Failed to create new meeting room: %s", error)
                    return

            # If the user joins an existing meeting channel
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

        # A user leaves a voice channel
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
            # Close the channel if no human participants left
            if not any(m for m in voice_channel.members if not m.bot):
                logger.info(
                    "Channel %s has no human participants. Will close in 5 seconds.",
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

        # If still no human participants, close the meeting
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

            # Send meeting ended message to the forum thread
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

                # Delete voice channel
                try:
                    await voice_channel.delete(reason="Meeting ended.")
                except Exception as error:
                    logger.error("Failed to delete voice channel: %s", error)

                # Wait for transcript, summary, and todolist generation
                await self.wait_for_transcript_and_summary(channel_id, max_wait_seconds=3600)

                # Retrieve generated data
                transcript = info.get("meeting_transcript")
                summary = info.get("meeting_summary")
                todolist = info.get("meeting_todolist")

                # Prepare final text or placeholders
                final_transcript = transcript if transcript else "(Transcript not available)"
                final_summary = summary if summary else "(Summary not available)"
                final_todolist = todolist if todolist else "(To-do list not available)"

                # Use post_with_file to send each attachment with a preceding message
                await post_with_file(thread, final_transcript, message_template=os.getenv("TRANSCRIBING_MESSAGE"))
                await post_with_file(thread, final_summary, message_template=os.getenv("SUMMARY_MESSAGE"))
                await post_with_file(thread, final_todolist, message_template=os.getenv("TODOLIST_MESSAGE"))

            # Clean up meeting information
            if channel_id in self.meeting_voice_channel_info:
                del self.meeting_voice_channel_info[channel_id]
            if thread_id in self.meeting_forum_thread_info:
                del self.meeting_forum_thread_info[thread_id]

            self.manager.finish_meeting(channel_id)
            await self.manager.schedule_bots()
        else:
            logger.info(
                "Channel %s had new participants within %d seconds. Cancel closing.",
                voice_channel.name,
                delay_seconds
            )

    async def wait_for_transcript_and_summary(self, channel_id: int, max_wait_seconds: int = 180):
        """Wait for transcript, summary, and to-do list to be generated within a reasonable time."""
        start_ts = time.time()
        info = self.meeting_voice_channel_info.get(channel_id, {})
        while time.time() - start_ts < max_wait_seconds:
            # If all three values are non-empty, generation is complete
            if (info.get("meeting_transcript") or "") and \
               (info.get("meeting_summary") or "") and \
               (info.get("meeting_todolist") or ""):
                return
            await asyncio.sleep(5)
        logger.warning(
            "Timed out waiting for STT/summary/todolist for channel %d after %d seconds.",
            channel_id,
            max_wait_seconds
        )

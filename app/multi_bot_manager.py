# app/multi_bot_manager.py
"""
Manages multiple Bots and schedules them to record new meetings.
"""
import asyncio
import logging
from typing import List

from app.meeting_bot import MeetingBot
from app.record_meeting import record_meeting_audio
from app.forum import create_forum_post

logger = logging.getLogger(__name__)

class MultiBotManager:
    """Manages multiple MeetingBot instances and handles scheduling."""

    def __init__(self, bot_tokens: List[str]):
        self.bots = []
        self.meetings_in_progress = []
        self.loop = asyncio.get_event_loop()

        for token in bot_tokens:
            bot = MeetingBot(bot_token=token, manager=self)
            self.bots.append(bot)

        # Define a helper to override forum creation
        async def create_forum_post_override(forum_channel, title: str, content: str):
            """Helper method to ensure we have a unified create_forum_post."""
            return await create_forum_post(
                forum_channel=forum_channel,
                title=title,
                content=content
            )

        for bot in self.bots:
            bot.manager.create_forum_post_override = create_forum_post_override

    def finish_meeting(self, voice_channel_id: int):
        """Remove a finished meeting from the in-progress list."""
        if voice_channel_id in self.meetings_in_progress:
            self.meetings_in_progress.remove(voice_channel_id)
            logger.info("Meeting %d removed from in-progress list.", voice_channel_id)

    async def handle_new_meeting(self, voice_channel_id: int) -> None:
        """Called when a new meeting is created."""
        self.meetings_in_progress.append(voice_channel_id)
        await self.schedule_bots()

    async def schedule_bots(self):
        """Schedules free bots to record ongoing meetings."""
        logger.info("Scheduling bots for meetings...")

        used_bot_ids = set()
        for bot in self.bots:
            for vc_id, vc_info in bot.meeting_voice_channel_info.items():
                if vc_id in self.meetings_in_progress:
                    if vc_info.get("recording_task") is not None:
                        if bot.user:
                            used_bot_ids.add(bot.user.id)

        free_bots = [
            bot for bot in self.bots
            if bot.user and bot.user.id not in used_bot_ids
        ]

        for vc_id in self.meetings_in_progress:
            logger.info("Checking if meeting %s already has a bot...", vc_id)
            already_has_bot = False

            # 檢查是否有 Bot 已在錄製
            for bot in self.bots:
                vc_info = bot.meeting_voice_channel_info.get(vc_id)
                if vc_info and vc_info.get("recording_task") is not None:
                    already_has_bot = True
                    break

            if not already_has_bot and free_bots:
                chosen_bot = free_bots.pop(0)
                logger.info("Assigning Bot %s to record meeting %s", chosen_bot.user.name, vc_id)

                # Sync meeting info from origin bot
                origin_bot = next((b for b in self.bots if vc_id in b.meeting_voice_channel_info), None)
                if origin_bot is not None:
                    origin_info = origin_bot.meeting_voice_channel_info.get(vc_id, {})
                    chosen_bot.meeting_voice_channel_info[vc_id] = dict(origin_info)
                else:
                    chosen_bot.meeting_voice_channel_info[vc_id] = {}

                guild = chosen_bot.guilds[0] if chosen_bot.guilds else None
                if not guild:
                    logger.error("Bot %s not in any guild. Cannot record.", chosen_bot.user.name)
                    continue

                voice_channel = guild.get_channel(vc_id)
                if voice_channel:
                    try:
                        recording_task = self.loop.create_task(
                            record_meeting_audio(chosen_bot, vc_id)
                        )
                        chosen_bot.meeting_voice_channel_info[vc_id]["recording_task"] = recording_task

                        logger.info(
                            "Assigned Bot %s to record voice channel %s.",
                            chosen_bot.user.name,
                            voice_channel.name
                        )
                    except Exception as error:
                        logger.error("Failed to assign bot to meeting: %s", error)

    def run_bots(self):
        """Runs all the bots forever."""
        for bot in self.bots:
            self.loop.create_task(bot.start(bot.bot_token))
        self.loop.run_forever()

    def assign_bot_for_meeting(self) -> MeetingBot:
        """Tries to assign a free bot for a new meeting."""
        # Find all the bots that are currently recording
        used_bot_ids = set()
        for bot in self.bots:
            for vc_id, vc_info in bot.meeting_voice_channel_info.items():
                if vc_info.get("recording_task") is not None:
                    if bot.user:
                        used_bot_ids.add(bot.user.id)

        free_bots = [
            bot for bot in self.bots
            if bot.user and bot.user.id not in used_bot_ids
        ]

        if not free_bots:
            logger.warning("No free bots available to create a new meeting.")
            return None

        # Choose the first free bot
        chosen_bot = free_bots[0]
        logger.info("Assign bot %s to create new meeting room.", chosen_bot.user.name)
        return chosen_bot

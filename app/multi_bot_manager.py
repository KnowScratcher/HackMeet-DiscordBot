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

        # Override to help pass forum creation calls to the first bot
        # or have a fallback approach
        for bot in self.bots:
            async def create_forum_post_override(
                forum_channel,
                title: str,
                content: str
            ):
                """Helper method to ensure we have a unified create_forum_post."""
                return await create_forum_post(forum_channel=forum_channel, title=title, content=content)

            bot.manager.create_forum_post_override = create_forum_post_override

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

        free_bots = [bot for bot in self.bots if bot.user and bot.user.id not in used_bot_ids]

        for vc_id in self.meetings_in_progress:
            logger.info("Checking if meeting %s already has a bot...", vc_id)
            already_has_bot = False
            for bot in self.bots:
                vc_info = bot.meeting_voice_channel_info.get(vc_id)
                if vc_info and vc_info.get("recording_task") is not None:
                    already_has_bot = True
                    break

            if not already_has_bot and free_bots:
                logger.info("Meeting %s needs a bot to record.", vc_id)
                bot = free_bots.pop(0)

                guild = bot.guilds[0] if bot.guilds else None
                if not guild:
                    logger.error("Bot %s not in any guild. Cannot record.", bot.user.name)
                    continue

                voice_channel = guild.get_channel(vc_id)
                if voice_channel:
                    try:
                        recording_task = self.loop.create_task(
                            record_meeting_audio(bot, vc_id)
                        )
                        bot.meeting_voice_channel_info[vc_id]["recording_task"] = recording_task

                        logger.info(
                            "Assigned Bot %s to record voice channel %s.",
                            bot.user.name,
                            voice_channel.name
                        )
                    except Exception as error:
                        logger.error("Failed to assign bot to meeting: %s", error)

    def run_bots(self):
        """Runs all the bots forever."""
        for bot in self.bots:
            self.loop.create_task(bot.start(bot.bot_token))
        self.loop.run_forever()

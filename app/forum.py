# app/forum.py
"""
Forum-related utility functions.
"""

import logging
import discord
from discord import ForumChannel

logger = logging.getLogger(__name__)

async def create_forum_post(
    forum_channel: ForumChannel,
    title: str,
    content: str
) -> discord.Thread:
    """Creates a thread post in the given forum channel."""
    try:
        post = await forum_channel.create_thread(name=title, content=content)
        return post
    except Exception as error:
        logger.error("Failed to create forum post: %s", error)
        return None

async def post_final_summary(thread: discord.Thread, summary_text: str) -> None:
    """Posts final summary text in a thread with a text file."""
    try:
        file_name = "meeting_summary.txt"
        with open(file_name, "w", encoding="utf-8") as file:
            file.write(summary_text)

        await thread.send(
            content="Here is the final meeting summary:",
            file=discord.File(file_name, filename=file_name)
        )
        logger.info("Uploaded the final summary in the thread.")
    except Exception as error:
        logger.error("Failed to upload final summary: %s", error)

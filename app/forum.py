# app/forum.py
"""
Forum-related utility functions.
"""
import io
import os
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


# app/forum.py（更新後的部分）
async def post_with_file(
        thread: discord.Thread,
        text: str,
        message_template: str = None
) -> None:
    """
    Posts final summary text in a thread with a text file.
    """
    try:
        if message_template is None:
            message_template = os.getenv(
                "SUMMARY_MESSAGE",
                "Here is the final meeting summary:"
            )

        file_stream = io.StringIO(text)

        await thread.send(
            content=message_template,
            file=discord.File(fp=file_stream, filename="meeting.txt")
        )
        logger.info("Uploaded the final summary in the thread.")
    except Exception as error:
        logger.error("Failed to upload final summary: %s", error)

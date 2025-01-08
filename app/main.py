# app/main.py
"""
The main entry point for creating MultiBotManager and running the bots.
"""

import os
import logging
from dotenv import load_dotenv

from app.multi_bot_manager import MultiBotManager
from app.summary.ai_select import ai_select_init

logger = logging.getLogger(__name__)

def main():
    """Reads bot tokens and runs the MultiBotManager."""
    load_dotenv()

    ai_select_init()

    bot_tokens_str = os.getenv("BOT_TOKENS", "")
    if not bot_tokens_str:
        logger.error("No Bot Tokens found. Please set BOT_TOKENS.")
        return

    bot_tokens = [token.strip() for token in bot_tokens_str.split(",") if token.strip()]
    if not bot_tokens:
        logger.error("No Bot Tokens found. Please set BOT_TOKENS.")
        return

    manager = MultiBotManager(bot_tokens)
    manager.run_bots()

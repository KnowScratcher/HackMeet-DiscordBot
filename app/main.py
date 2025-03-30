# app/main.py
"""
The main entry point for creating MultiBotManager and running the bots.
"""

import os
import logging
import asyncio
from dotenv import load_dotenv

from app.multi_bot_manager import MultiBotManager
from app.summary.ai_select import ai_select_init
from app.utils.oauth_drive import reset_drive_service
from app.utils.memory_monitor import memory_monitor_task, log_memory_usage

logger = logging.getLogger(__name__)

async def periodic_service_reset(interval_hours=4):
    """
    Periodically reset service connections to prevent memory leaks.
    
    Args:
        interval_hours (int): Hours between service resets
    """
    while True:
        try:
            # Wait for the specified interval
            await asyncio.sleep(interval_hours * 3600)
            
            # Log memory usage before reset
            await log_memory_usage(force_gc=True)
            
            # Reset Google Drive service
            logger.info("Performing periodic service reset")
            await reset_drive_service()
            
            # Force garbage collection
            import gc
            gc.collect()
            
            # Log memory usage after reset
            await log_memory_usage()
            
            logger.info("Periodic service reset completed")
            
        except asyncio.CancelledError:
            logger.info("Periodic service reset task cancelled")
            break
        except Exception as e:
            logger.error("Error in periodic service reset: %s", e)
            # Wait a shorter time before retrying if there was an error
            await asyncio.sleep(600)  # 10 minutes

def main():
    """Reads bot tokens and runs the MultiBotManager."""
    load_dotenv(override=True)

    ai_select_init()

    bot_tokens_str = os.getenv("BOT_TOKENS", "")
    if not bot_tokens_str:
        logger.error("No Bot Tokens found. Please set BOT_TOKENS.")
        return

    bot_tokens = [token.strip() for token in bot_tokens_str.split(",") if token.strip()]
    if not bot_tokens:
        logger.error("No Bot Tokens found. Please set BOT_TOKENS.")
        return

    # Create and start the event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        # Start the memory monitoring task (check every hour, force GC every 4 hours)
        memory_task = loop.create_task(memory_monitor_task(3600, 14400))
        
        # Start the periodic service reset task (every 4 hours)
        reset_task = loop.create_task(periodic_service_reset(4))
        
        # Log initial memory usage
        loop.run_until_complete(log_memory_usage())
        
        # Create and run the bot manager
        manager = MultiBotManager(bot_tokens)
        manager.run_bots()
        
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, shutting down...")
    except Exception as e:
        logger.error("Error in main: %s", e)
    finally:
        # Clean up tasks
        tasks = asyncio.all_tasks(loop)
        for task in tasks:
            task.cancel()
        
        # Run until all tasks are cancelled
        loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
        loop.close()

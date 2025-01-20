# app/utils/retry.py
"""
Retry utility for handling temporary failures.
"""
import asyncio
import logging
from typing import Callable, TypeVar, Optional
from functools import wraps

logger = logging.getLogger(__name__)

T = TypeVar('T')

async def async_retry(
    func: Callable[..., T],
    *args,
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff_factor: float = 2.0,
    exceptions: tuple = (Exception,),
    **kwargs
) -> Optional[T]:
    """
    Retry the execution of an asynchronous function.

    Args:
        func: The asynchronous function to execute
        *args: Positional arguments for the function
        max_attempts: Maximum number of retry attempts
        delay: Initial delay time (seconds)
        backoff_factor: Multiplicative factor for delay time
        exceptions: Exception types that should trigger a retry
        **kwargs: Keyword arguments for the function

    Returns:
        The result of the function execution, or None if all attempts fail
    """
    attempt = 1
    current_delay = delay

    while attempt <= max_attempts:
        try:
            return await func(*args, **kwargs)
        except exceptions as e:
            if attempt == max_attempts:
                logger.error(
                    "Final attempt %d/%d failed for %s: %s",
                    attempt, max_attempts, func.__name__, str(e)
                )
                return None

            logger.warning(
                "Attempt %d/%d failed for %s: %s. Retrying in %.1f seconds...",
                attempt, max_attempts, func.__name__, str(e), current_delay
            )

            await asyncio.sleep(current_delay)
            current_delay *= backoff_factor
            attempt += 1

    return None

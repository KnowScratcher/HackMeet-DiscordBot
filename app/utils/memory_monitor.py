"""
Memory monitoring utility to track memory usage and help identify memory leaks.
"""
import os
import gc
import logging
import platform
import asyncio
import time
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Global state for memory tracking
_MEMORY_STATS = {
    "start_time": time.time(),
    "last_check": time.time(),
    "peak_usage": 0,
    "history": [],
    "gc_collections": 0
}

async def get_memory_usage() -> Dict[str, float]:
    """
    Get current memory usage of the process.
    
    Returns:
        Dict with memory usage information in MB
    """
    result = {"rss": 0, "vms": 0}
    
    try:
        import psutil
        process = psutil.Process(os.getpid())
        mem_info = process.memory_info()
        
        # Convert to MB for readability
        result["rss"] = mem_info.rss / (1024 * 1024)  # Resident Set Size
        result["vms"] = mem_info.vms / (1024 * 1024)  # Virtual Memory Size
        
    except ImportError:
        logger.warning("psutil not installed. Memory monitoring limited.")
        
        # Fallback to platform-specific methods
        if platform.system() == "Linux":
            try:
                with open(f"/proc/{os.getpid()}/status", "r") as f:
                    for line in f:
                        if "VmRSS" in line:
                            result["rss"] = float(line.split()[1]) / 1024  # Convert KB to MB
                        elif "VmSize" in line:
                            result["vms"] = float(line.split()[1]) / 1024  # Convert KB to MB
            except Exception as e:
                logger.error("Failed to read memory info from /proc: %s", e)
    
    return result

async def force_garbage_collection() -> int:
    """
    Force a full garbage collection and return the number of objects collected.
    
    Returns:
        int: Number of objects collected
    """
    # Disable automatic garbage collection during manual collection
    was_enabled = gc.isenabled()
    if was_enabled:
        gc.disable()
    
    try:
        # Run garbage collection and get collection counts
        collected = gc.collect(2)  # Full collection (all generations)
        
        # Update stats
        _MEMORY_STATS["gc_collections"] += 1
        
        return collected
    finally:
        # Re-enable automatic garbage collection if it was enabled
        if was_enabled:
            gc.enable()

async def log_memory_usage(force_gc: bool = False) -> Dict[str, float]:
    """
    Log current memory usage and optionally force garbage collection.
    
    Args:
        force_gc (bool): Whether to force garbage collection before measuring
        
    Returns:
        Dict with memory usage information
    """
    collected = 0
    if force_gc:
        collected = await force_garbage_collection()
    
    mem_usage = await get_memory_usage()
    current_time = time.time()
    uptime = current_time - _MEMORY_STATS["start_time"]
    time_since_last = current_time - _MEMORY_STATS["last_check"]
    
    # Update peak usage
    if mem_usage["rss"] > _MEMORY_STATS["peak_usage"]:
        _MEMORY_STATS["peak_usage"] = mem_usage["rss"]
    
    # Add to history (keep last 100 entries)
    _MEMORY_STATS["history"].append({
        "timestamp": current_time,
        "rss": mem_usage["rss"],
        "vms": mem_usage["vms"]
    })
    if len(_MEMORY_STATS["history"]) > 100:
        _MEMORY_STATS["history"] = _MEMORY_STATS["history"][-100:]
    
    # Update last check time
    _MEMORY_STATS["last_check"] = current_time
    
    # Log memory usage
    if force_gc:
        logger.info(
            "Memory usage after GC: RSS=%.2f MB, VMS=%.2f MB, Peak=%.2f MB, Uptime=%.1f hours, Objects collected=%d",
            mem_usage["rss"], mem_usage["vms"], _MEMORY_STATS["peak_usage"],
            uptime / 3600, collected
        )
    else:
        logger.info(
            "Memory usage: RSS=%.2f MB, VMS=%.2f MB, Peak=%.2f MB, Uptime=%.1f hours",
            mem_usage["rss"], mem_usage["vms"], _MEMORY_STATS["peak_usage"],
            uptime / 3600
        )
    
    return mem_usage

async def memory_monitor_task(check_interval: int = 3600, gc_interval: int = 14400):
    """
    Background task to monitor memory usage periodically.
    
    Args:
        check_interval (int): Seconds between regular memory checks
        gc_interval (int): Seconds between forced garbage collections
    """
    last_gc = time.time()
    
    while True:
        try:
            current_time = time.time()
            
            # Determine if we should force GC
            should_force_gc = (current_time - last_gc) >= gc_interval
            
            # Log memory usage
            await log_memory_usage(force_gc=should_force_gc)
            
            # Update last GC time if we forced GC
            if should_force_gc:
                last_gc = current_time
            
            # Sleep until next check
            await asyncio.sleep(check_interval)
            
        except asyncio.CancelledError:
            logger.info("Memory monitor task cancelled")
            break
        except Exception as e:
            logger.error("Error in memory monitor: %s", e)
            await asyncio.sleep(60)  # Wait a minute before retrying

def get_memory_history() -> List[Dict]:
    """
    Get the history of memory usage measurements.
    
    Returns:
        List of memory usage records
    """
    return _MEMORY_STATS["history"]

def get_memory_summary() -> Dict:
    """
    Get a summary of memory usage statistics.
    
    Returns:
        Dict with memory usage summary
    """
    current_time = time.time()
    return {
        "start_time": datetime.fromtimestamp(_MEMORY_STATS["start_time"]).isoformat(),
        "uptime_hours": (current_time - _MEMORY_STATS["start_time"]) / 3600,
        "peak_usage_mb": _MEMORY_STATS["peak_usage"],
        "gc_collections": _MEMORY_STATS["gc_collections"],
        "last_check": datetime.fromtimestamp(_MEMORY_STATS["last_check"]).isoformat()
    } 
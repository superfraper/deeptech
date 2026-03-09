import asyncio
from collections.abc import Callable
from typing import Any


async def run_in_thread(func: Callable, *args, **kwargs) -> Any:
    """
    Run a blocking function in a thread to avoid blocking the event loop.
    Returns the function's result.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: func(*args, **kwargs))

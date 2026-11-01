#!/usr/bin/env python3
"""Entry point: python run.py"""

import asyncio

from kiro_bot.bot import main

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass

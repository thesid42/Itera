#!/usr/bin/env python3
"""
Itera — The Autonomous Cloud Lab Compiler
Entry point: launches the TUI and wires all three engines.
"""
from dotenv import load_dotenv
load_dotenv()  # loads OPENROUTER_API_KEY from .env if present

from utils.logger import setup_logging
setup_logging()  # must be called before any engine imports

import logging
logger = logging.getLogger(__name__)

from tui.app import IteraApp


def main():
    logger.info("Itera starting up")
    print("\033[2J\033[H", end="")  # clear terminal
    app = IteraApp()
    app.run()
    logger.info("Itera shut down")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Itera — The Autonomous Cloud Lab Compiler

Usage
-----
  python main.py              # Textual TUI (default)
  python main.py --web        # FastAPI web server  (http://localhost:8000)
  python main.py --web --port 9000
"""
import argparse

from dotenv import load_dotenv
load_dotenv()

from utils.logger import setup_logging
setup_logging()

import logging
logger = logging.getLogger(__name__)


def run_tui():
    from tui.app import IteraApp
    logger.info("Itera starting — TUI mode")
    print("\033[2J\033[H", end="")   # clear terminal
    IteraApp().run()
    logger.info("Itera shut down")


def run_web(host: str = "0.0.0.0", port: int = 8000):
    import uvicorn
    logger.info("Itera starting — web mode | http://%s:%d", host, port)
    print(f"\n  ITERA web interface → http://localhost:{port}\n")
    uvicorn.run(
        "web.app:app",
        host=host,
        port=port,
        reload=False,
        log_level="warning",   # uvicorn access logs stay quiet; our logger handles detail
    )


def main():
    parser = argparse.ArgumentParser(
        description="Itera — Autonomous Cloud Lab Compiler"
    )
    parser.add_argument(
        "--web",
        action="store_true",
        help="Launch the web interface instead of the TUI",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host for the web server (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for the web server (default: 8000)",
    )
    args = parser.parse_args()

    if args.web:
        run_web(host=args.host, port=args.port)
    else:
        run_tui()


if __name__ == "__main__":
    main()

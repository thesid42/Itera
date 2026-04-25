#!/usr/bin/env python3
"""
Itera — The Autonomous Cloud Lab Compiler
Entry point: launches the TUI and wires all three engines.
"""
import sys
from tui.app import IteraApp


def main():
    print("\033[2J\033[H", end="")  # clear terminal
    app = IteraApp()
    app.run()


if __name__ == "__main__":
    main()

"""Browser launch shared by PDF rendering and form filling.

Uses Playwright's bundled Chromium (`playwright install chromium`). Set
JOBPILOT_CHROMIUM to a Chrome/Chromium executable to use that instead.
"""

from __future__ import annotations

import os


def launch(playwright, headless: bool = True):
    executable = os.environ.get("JOBPILOT_CHROMIUM") or None
    return playwright.chromium.launch(headless=headless, executable_path=executable)

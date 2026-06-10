#!/usr/bin/env python3
"""
End-to-end runner for the WC 2026 betting page.

Usage:
  python3 run.py            # uses today's cached odds if present
  python3 run.py --force    # forces a fresh Odds API call (~2 credits)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from fetch_odds import fetch_and_cache
from render_html import main as render_main


def main() -> None:
    force = "--force" in sys.argv
    fetch_and_cache(force=force)
    render_main()


if __name__ == "__main__":
    main()

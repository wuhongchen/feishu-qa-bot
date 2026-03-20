#!/usr/bin/env python3
"""Shared .env bootstrap helpers."""

from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


def load_project_env(anchor_file: str) -> Optional[Path]:
    """
    Load .env near current file.

    Search order:
    1) sibling .env (same dir as anchor file)
    2) parent dir .env (for scripts/* entrypoints)
    """
    anchor = Path(anchor_file).resolve()
    candidates = [anchor.parent / ".env", anchor.parent.parent / ".env"]

    for candidate in candidates:
        if candidate.exists():
            load_dotenv(dotenv_path=candidate, override=False)
            return candidate

    # Best-effort fallback to default dotenv discovery.
    load_dotenv(override=False)
    return None

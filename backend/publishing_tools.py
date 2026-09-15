"""Locating the command-line publishing tools (Pandoc, Tectonic, rsvg-convert).

Each is found from its environment variable, then the user-space install made by
backend/scripts/setup_publishing_tools.sh, then PATH.
"""

import os
import shutil
from pathlib import Path

TOOLS_PREFIX = Path(os.getenv("OMNIREVIEW_TOOLS_PREFIX", str(Path.home() / ".local/share/omnireview/tools")))


def tool_path(env_var: str, name: str) -> str | None:
    configured = os.getenv(env_var, "").strip()
    if configured:
        return configured if Path(configured).is_file() else None
    bundled = TOOLS_PREFIX / "bin" / name
    if bundled.is_file():
        return str(bundled)
    return shutil.which(name)


def tools_status() -> dict[str, bool]:
    return {
        "pandoc": tool_path("PANDOC_PATH", "pandoc") is not None,
        "tectonic": tool_path("TECTONIC_PATH", "tectonic") is not None,
        "rsvg_convert": tool_path("RSVG_CONVERT_PATH", "rsvg-convert") is not None,
    }

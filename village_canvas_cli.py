"""Portable entry point for ``village-canvas``.

It keeps the source checkout and the portable runtime relocatable by deriving
the package path from this file instead of relying on a machine-wide install.
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from novelvideo.canvas_cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())

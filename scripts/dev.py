"""Development server with isolated local data, used by the Browser pane."""

from __future__ import annotations

import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
os.environ.setdefault("KNOWGRAPH_HOME", str(BASE / ".devdata"))
os.environ.setdefault("KNOWGRAPH_VAULT", str(BASE / ".devvault"))
os.environ.setdefault("KNOWGRAPH_TIMEZONE", "Asia/Shanghai")

import uvicorn  # noqa: E402

if __name__ == "__main__":
    uvicorn.run(
        "knowgraph.api:create_app",
        factory=True,
        host="127.0.0.1",
        port=8766,
        log_level="info",
    )

"""Uvicorn entry point for the HTTP API container."""

from __future__ import annotations

import os

import uvicorn

from classroom_sync.config import Settings
from classroom_sync.runtime import create_runtime_app


def main() -> None:
    """Serve the fully composed API after its entry command has migrated the database."""

    app = create_runtime_app(Settings.from_env())
    uvicorn.run(
        app,
        host=os.environ.get("CLASSROOM_API_HOST", "0.0.0.0"),
        port=int(os.environ.get("CLASSROOM_API_PORT", "8080")),
        log_level="info",
    )


if __name__ == "__main__":
    main()

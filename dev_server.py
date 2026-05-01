from __future__ import annotations

import uvicorn

from backend.dev_reload import build_uvicorn_reload_kwargs


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8000,
        **build_uvicorn_reload_kwargs(),
    )

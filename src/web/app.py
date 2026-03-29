from __future__ import annotations

import asyncio
import io
import os
import subprocess
import threading
import webbrowser
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from CLI.app import main as cli_main


def _run_cli_capture(argv: list[str]) -> dict[str, Any]:
    out = io.StringIO()
    err = io.StringIO()
    code = 0
    try:
        with redirect_stdout(out), redirect_stderr(err):
            cli_main(argv)
    except SystemExit as e:
        code = int(e.code) if isinstance(e.code, int) else 1
    return {
        "exit_code": code,
        "stdout": out.getvalue(),
        "stderr": err.getvalue(),
    }


app = FastAPI(title="Vertex Web", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # dev-friendly; tighten for prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/cli")
async def cli_endpoint(payload: dict[str, Any]) -> JSONResponse:
    argv = payload.get("argv")
    if not isinstance(argv, list) or not all(isinstance(x, str) for x in argv):
        raise HTTPException(400, "payload.argv must be a list[str]")
    result = await asyncio.to_thread(_run_cli_capture, list(argv))
    return JSONResponse(result)


def main() -> None:
    # Dev runner: starts uvicorn and opens browser to the frontend (once you add it).
    host = os.environ.get("VERTEX_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("VERTEX_WEB_PORT", "8787"))
    url = f"http://{host}:{port}"

    def _open() -> None:
        webbrowser.open(url)

    threading.Timer(0.8, _open).start()

    # uvicorn import here so it isn't required unless running the server
    import uvicorn

    uvicorn.run("web.app:app", host=host, port=port, reload=True)


if __name__ == "__main__":
    main()


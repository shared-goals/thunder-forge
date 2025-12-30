from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.mini_app import router as mini_app_router

try:
    from telegram import Update  # type: ignore
except Exception:  # pragma: no cover
    Update = None  # type: ignore

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = (Path(__file__).resolve().parents[1] / "static" / "mini_app").resolve()
if STATIC_DIR.exists():
    app.mount("/mini-app", StaticFiles(directory=str(STATIC_DIR), html=True), name="mini-app")

app.include_router(mini_app_router, prefix="/api/mini-app")


@app.get("/health")
async def health():
    return {"ok": True}


_bot_app = None


def setup_bot_app(bot_app) -> None:
    global _bot_app
    _bot_app = bot_app


@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    if _bot_app is None:
        raise HTTPException(status_code=503, detail="Bot not initialized")
    if Update is None:
        raise HTTPException(status_code=500, detail="python-telegram-bot missing")

    payload = await request.json()
    update = Update.de_json(payload, _bot_app.bot)
    await _bot_app.process_update(update)
    return {"ok": True}

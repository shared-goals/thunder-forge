from __future__ import annotations

import uvicorn

from api.webhook import setup_bot_app
from bot.app import create_bot_app
from services.config_service import load_config


def main() -> None:
    # Initialize bot for webhook processing.
    # Note: localhost dev won't receive Telegram webhooks unless you expose it.
    setup_bot_app(create_bot_app())

    cfg = load_config()
    host = cfg.server.bind
    port = cfg.server.port

    uvicorn.run(
        "api.webhook:app",
        host=host,
        port=port,
        reload=bool(cfg.server.reload),
    )


if __name__ == "__main__":
    main()

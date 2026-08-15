from __future__ import annotations

import argparse
import json

from core.family_document_sinks import TelegramActivation, SecretReference


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render family document worker private notification config.")
    parser.add_argument("--telegram-token-secret", default="skeleton.family_document.telegram.bot_token")
    parser.add_argument("--telegram-chat-secret", default="skeleton.family_document.telegram.chat_id")
    args = parser.parse_args(argv)
    activation = TelegramActivation(
        bot_token=SecretReference("runtime", args.telegram_token_secret),
        chat_id=SecretReference("runtime", args.telegram_chat_secret),
    )
    print(json.dumps(activation.to_public_config(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

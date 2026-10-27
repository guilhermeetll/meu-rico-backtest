"""Envio e leitura de chat id. O token não vai para o log."""

from __future__ import annotations

import json
import logging

from alertas_dividendo.http_client import get, post_json

log = logging.getLogger(__name__)


def send_message(token: str, chat_id: str, text: str) -> str:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = post_json(
        url,
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
    if not body.get("ok"):
        raise RuntimeError(f"Telegram recusou a mensagem: {body!r}"[:300])
    return str((body.get("result") or {}).get("message_id") or "")


def chat_ids_from_updates(token: str) -> list[tuple[str, str]]:
    """Chats que mandaram mensagem para o bot. O usuário precisa falar com ele antes."""
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    payload = json.loads(get(url, timeout=30, headers={"Accept": "application/json"}))
    if not payload.get("ok"):
        raise RuntimeError(f"getUpdates falhou: {payload!r}"[:300])
    found: dict[str, str] = {}
    for update in payload.get("result") or []:
        message = update.get("message") or update.get("edited_message") or {}
        chat = message.get("chat") or {}
        if "id" not in chat:
            continue
        label = chat.get("title") or chat.get("username") or chat.get("first_name") or ""
        found[str(chat["id"])] = str(label)
    return sorted(found.items())

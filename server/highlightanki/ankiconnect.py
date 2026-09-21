"""Best-effort push of freshly written cards into Anki via AnkiConnect.

The pending-folder file is the durable record; this is only the delivery
mechanism. Anki closed or the add-on missing → return None, cards stay in
pending/ for a manual File → Import.
"""
from __future__ import annotations

from typing import Any

import httpx

from .export import norm_tag

URL = "http://127.0.0.1:8765"


class AnkiConnectError(Exception):
    pass


def _call(action: str, timeout: float, **params) -> Any:
    body: dict = {"action": action, "version": 6}
    if params:
        body["params"] = params
    res = httpx.post(URL, json=body, timeout=timeout).json()
    if res.get("error"):
        raise AnkiConnectError(res["error"])
    return res.get("result")


def notes_for(cards: list[dict], source: str, deck: str) -> list[dict]:
    notes = []
    for c in cards:
        if c["notetype"] == "Cloze":
            fields = {"Text": c["front"], "Back Extra": c["back"], "Source": source}
        else:
            fields = {"Front": c["front"], "Back": c["back"], "Source": source}
        notes.append({
            "deckName": deck,
            "modelName": c["notetype"],
            "fields": fields,
            "tags": [t for t in dict.fromkeys(norm_tag(x) for x in c["tags"]) if t],
            "options": {"allowDuplicate": False},
        })
    return notes


def import_cards(cards: list[dict], source: str, deck: str) -> dict | None:
    """Returns {"imported": n, "failed": m} or None when AnkiConnect is unreachable."""
    try:
        _call("version", timeout=2.0)
    except (httpx.HTTPError, AnkiConnectError):
        return None
    try:
        ids = _call("addNotes", timeout=30.0, notes=notes_for(cards, source, deck))
    except (httpx.HTTPError, AnkiConnectError):
        # Reachable but the batch failed outright — pending/ file still has everything.
        return {"imported": 0, "failed": len(cards)}
    return {
        "imported": sum(1 for i in ids if i is not None),
        "failed": sum(1 for i in ids if i is None),
    }

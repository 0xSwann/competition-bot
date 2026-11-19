"""Posts messages to a Discord webhook."""
import time

import requests

GREEN = 0x2ECC71
GOLD = 0xF1C40F
BLUE = 0x3498DB
GREY = 0x95A5A6


def post(webhook_url: str, content: str = "", embeds: list | None = None, silent: bool = False) -> None:
    """silent=True posts without a push notification."""
    if not webhook_url:
        print(f"[discord] no webhook_url configured; skipped a message with {len(embeds or [])} item(s)")
        return
    embeds = embeds or []
    # Discord allows at most 10 embeds per message.
    chunks = [embeds[i:i + 10] for i in range(0, len(embeds), 10)] or [[]]
    for i, chunk in enumerate(chunks):
        payload = {"content": content if i == 0 else "", "embeds": chunk}
        if silent:
            payload["flags"] = 4096  # SUPPRESS_NOTIFICATIONS
        for _ in range(3):
            r = requests.post(webhook_url, json=payload, timeout=20)
            if r.status_code == 429:
                time.sleep(float(r.json().get("retry_after", 2)))
                continue
            r.raise_for_status()
            break


def comp_embed(comp: dict) -> dict:
    fields = []
    if comp.get("closing"):
        fields.append({"name": "Closes", "value": comp["closing"][:16].replace("T", " "), "inline": True})
    if comp.get("categories"):
        fields.append({"name": "Entry", "value": comp["categories"][:1000], "inline": True})
    fields.append({"name": "OzBargain", "value": f"[Comments and details]({comp['node_url']})", "inline": False})
    return {
        "title": comp["title"][:256],
        "url": comp.get("go_url") or comp["node_url"],
        "color": BLUE,
        "fields": fields,
    }

"""
Instagram (@oman_college_om) -> Odoo website News
Runs on GitHub Actions. Reads recent posts via Graph API (System User token, never expires)
and sends new ones to the Odoo webhook (Automation Rule: On webhook -> Execute Code).
"""
import base64
import json
import os
import sys

import requests

IG_TOKEN = os.environ["IG_TOKEN"]
IG_USER_ID = os.environ["IG_USER_ID"]
ODOO_WEBHOOK_URL = os.environ["ODOO_WEBHOOK_URL"]
ODOO_SECRET = os.environ["ODOO_SECRET"]

GRAPH_VERSION = "v27.0"
INCLUDE_REELS = os.environ.get("INCLUDE_REELS", "false").lower() == "true"  # Reels -> use thumbnail as image
BACKFILL = os.environ.get("BACKFILL", "false").lower() == "true"            # first run: publish existing posts too?
STATE_FILE = "seen.json"
FETCH_LIMIT = 10
KEEP_IDS = 300
MAX_IMAGE_BYTES = 8 * 1024 * 1024

IG_FIELDS = "id,caption,media_type,media_url,thumbnail_url,permalink,timestamp"


def load_seen():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return None


def save_seen(seen):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(seen[-KEEP_IDS:], f, indent=0)


def fetch_media():
    r = requests.get(
        f"https://graph.facebook.com/{GRAPH_VERSION}/{IG_USER_ID}/media",
        params={"fields": IG_FIELDS, "limit": FETCH_LIMIT},
        headers={"Authorization": f"Bearer {IG_TOKEN}"},
        timeout=30,
    )
    if r.status_code != 200:
        try:
            err = r.json().get("error", {})
            msg = f"{err.get('type')} {err.get('code')}: {err.get('message')}"
        except ValueError:
            msg = r.text[:300]
        print(f"Instagram API error {r.status_code}: {msg}", file=sys.stderr)
        sys.exit(1)
    return r.json().get("data", [])


def image_b64(url):
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    data = r.content
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError(f"image too large ({len(data)} bytes)")
    if not (data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n"):
        raise ValueError("not a JPEG/PNG image")
    return base64.b64encode(data).decode("ascii")


def main():
    seen = load_seen()
    first_run = seen is None
    seen = seen or []
    seen_set = set(seen)

    items = list(reversed(fetch_media()))  # oldest first
    failed = 0
    sent = 0

    def mark(mid):
        seen.append(mid)
        seen_set.add(mid)

    for m in items:
        mid = m["id"]
        if mid in seen_set:
            continue

        if first_run and not BACKFILL:
            mark(mid)
            continue

        mtype = m.get("media_type")
        if mtype == "VIDEO" and not INCLUDE_REELS:
            mark(mid)
            print(f"skip reel {mid}")
            continue

        img_url = m.get("thumbnail_url") if mtype == "VIDEO" else m.get("media_url")
        if not img_url:
            print(f"no image for {mid}, skipped", file=sys.stderr)
            mark(mid)
            continue

        try:
            payload = {
                "token": ODOO_SECRET,
                "ig_id": mid,
                "caption": m.get("caption", ""),
                "permalink": m.get("permalink", ""),
                "timestamp": m.get("timestamp", ""),
                "media_type": mtype,
                "image_b64": image_b64(img_url),
            }
            r = requests.post(ODOO_WEBHOOK_URL, json=payload, timeout=90)
            r.raise_for_status()
            mark(mid)
            sent += 1
            print(f"published {mid}")
        except Exception as e:  # leave unseen -> retried next run
            failed += 1
            print(f"FAILED {mid}: {type(e).__name__}: {e}", file=sys.stderr)

    save_seen(seen)
    print(f"done: sent={sent} failed={failed} first_run={first_run}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()

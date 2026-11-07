"""
Instagram (@oman_college_om) -> full archive (read-only).
Pages through ALL posts via the Graph API, downloads every image (albums included),
and writes: archive/posts.json, archive/posts.csv, archive/images/<date>_<id>/NN.jpg
Does NOT publish anything anywhere.
"""
import csv
import json
import os
import sys
import time

import requests

IG_TOKEN = os.environ["IG_TOKEN"]
IG_USER_ID = os.environ["IG_USER_ID"]

GRAPH = "https://graph.facebook.com/v27.0"
OUT = os.environ.get("OUT_DIR", "archive")
PAGE_SIZE = 50
MAX_IMAGE_BYTES = 15 * 1024 * 1024
SINCE = os.environ.get("SINCE", "").strip()                 # YYYY-MM-DD: stop at posts older than this
MAX_POSTS = int(os.environ.get("MAX_POSTS", "0") or 0)      # 0 = no limit (newest first)
HEADERS = {"Authorization": f"Bearer {IG_TOKEN}"}

IG_FIELDS = "id,caption,media_type,media_url,thumbnail_url,permalink,timestamp"
CHILD_FIELDS = "id,media_type,media_url,thumbnail_url"


def get_json(url, params=None):
    for attempt in range(4):
        r = requests.get(url, params=params, headers=HEADERS, timeout=60)
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 500, 502, 503, 504) or "rate" in r.text.lower():
            time.sleep(5 * (attempt + 1))
            continue
        try:
            err = r.json().get("error", {})
            msg = f"{err.get('type')} {err.get('code')}: {err.get('message')}"
        except ValueError:
            msg = r.text[:200]
        raise RuntimeError(f"API error {r.status_code}: {msg}")
    raise RuntimeError("API error: too many retries")


def all_media():
    data = get_json(f"{GRAPH}/{IG_USER_ID}/media", {"fields": IG_FIELDS, "limit": PAGE_SIZE})
    pages = 0
    while True:
        pages += 1
        for m in data.get("data", []):
            yield m
        nxt = data.get("paging", {}).get("next")
        if not nxt:
            break
        data = get_json(nxt)
    print(f"pages fetched: {pages}")


def image_url(item):
    return item.get("thumbnail_url") if item.get("media_type") == "VIDEO" else item.get("media_url")


def ext_of(data):
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    return None


def download(url, folder, index):
    r = requests.get(url, timeout=90)
    r.raise_for_status()
    data = r.content
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("image too large")
    ext = ext_of(data)
    if not ext:
        raise ValueError("not JPEG/PNG")
    name = f"{index:02d}.{ext}"
    with open(os.path.join(folder, name), "wb") as f:
        f.write(data)
    return name


def main():
    os.makedirs(os.path.join(OUT, "images"), exist_ok=True)
    posts, failed = [], 0
    for m in all_media():  # newest first
        mid = m["id"]
        day = (m.get("timestamp") or "")[:10] or "unknown"
        if SINCE and day != "unknown" and day < SINCE:
            print(f"reached {day} (older than SINCE={SINCE}), stopping")
            break
        if MAX_POSTS and len(posts) >= MAX_POSTS:
            print(f"reached MAX_POSTS={MAX_POSTS}, stopping")
            break
        rel = f"{day}_{mid}"
        folder = os.path.join(OUT, "images", rel)
        os.makedirs(folder, exist_ok=True)
        try:
            if m.get("media_type") == "CAROUSEL_ALBUM":
                kids = get_json(f"{GRAPH}/{mid}/children", {"fields": CHILD_FIELDS}).get("data", [])
                urls = [image_url(c) for c in kids]
            else:
                urls = [image_url(m)]
            urls = [u for u in urls if u]
            files = []
            for i, u in enumerate(urls, 1):
                files.append(f"images/{rel}/" + download(u, folder, i))
            status = "ok" if files else "no_image"
        except Exception as e:
            failed += 1
            files, status = [], f"error: {type(e).__name__}: {e}"
            print(f"FAILED {mid}: {status}", file=sys.stderr)
        posts.append({
            "id": mid,
            "date": m.get("timestamp", ""),
            "type": m.get("media_type"),
            "caption": m.get("caption", ""),
            "permalink": m.get("permalink", ""),
            "images": files,
            "status": status,
        })
        time.sleep(0.2)

    posts.sort(key=lambda p: p["date"])  # oldest first
    with open(os.path.join(OUT, "posts.json"), "w", encoding="utf-8") as f:
        json.dump(posts, f, ensure_ascii=False, indent=1)
    with open(os.path.join(OUT, "posts.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "date", "type", "images_count", "status", "permalink", "caption", "images"])
        for p in posts:
            w.writerow([p["id"], p["date"], p["type"], len(p["images"]), p["status"],
                        p["permalink"], p["caption"], " | ".join(p["images"])])

    videos = sum(1 for p in posts if p["type"] == "VIDEO")
    print(f"done: posts={len(posts)} (videos/reels={videos}) failed={failed}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()

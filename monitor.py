"""
مراقب الدورات: يفحص صفحات محددة، وإذا ظهر نص جديد فيها يرسل تنبيهًا على جوالك عبر ntfy.
"""
import json
import os
import re
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

STATE_FILE = Path("state.json")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; CourseMonitor/1.0)"}

# ------------------------------------------------------------------
# عدّل هذه القائمة: ضع رابط صفحة الدورات/التسجيل الفعلية لكل جهة.
# selector: اختياري، لتحديد جزء من الصفحة فقط (مثل "main" أو ".courses").
# keywords: لا يُنبَّه إلا إذا ظهر سطر جديد يحتوي إحدى هذه الكلمات.
# ------------------------------------------------------------------
KEYWORDS = ["تسجيل", "التقديم", "معسكر", "دورة", "برنامج", "bootcamp", "apply", "register"]

SITES = [
    {"name": "أكاديمية طويق", "url": "PUT_URL_HERE", "selector": None, "keywords": KEYWORDS},
    {"name": "الأكاديمية الرقمية السعودية", "url": "PUT_URL_HERE", "selector": None, "keywords": KEYWORDS},
    {"name": "أكاديمية سدايا", "url": "PUT_URL_HERE", "selector": None, "keywords": KEYWORDS},
    {"name": "أكاديمية مسك", "url": "PUT_URL_HERE", "selector": None, "keywords": KEYWORDS},
    {"name": "تمهير", "url": "PUT_URL_HERE", "selector": None, "keywords": KEYWORDS},
]


def fetch_lines(site):
    r = requests.get(site["url"], headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    root = soup.select_one(site["selector"]) if site.get("selector") else soup
    if root is None:
        raise ValueError(f"selector not found: {site['selector']}")

    lines = [re.sub(r"\s+", " ", l).strip() for l in root.get_text("\n").splitlines()]
    lines = [l for l in lines if len(l) > 3]

    kws = [k.lower() for k in site.get("keywords", [])]
    if kws:
        lines = [l for l in lines if any(k in l.lower() for k in kws)]

    seen, out = set(), []
    for l in lines:
        if l not in seen:
            seen.add(l)
            out.append(l)
    return out[:500]


def notify(site, new_lines):
    body = f"{site['name']}\n" + "\n".join(f"- {l[:140]}" for l in new_lines[:5])
    print("ALERT:\n" + body)
    if not NTFY_TOPIC:
        print("NTFY_TOPIC is not set; skipping push.")
        return
    requests.post(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=body.encode("utf-8"),
        headers={"Title": "New course update", "Click": site["url"], "Priority": "high"},
        timeout=30,
    )


def main():
    state = json.loads(STATE_FILE.read_text("utf-8")) if STATE_FILE.exists() else {}
    failures = 0

    for site in SITES:
        url = site["url"]
        if url.startswith("PUT_URL"):
            print(f"[skip] {site['name']}: no URL set")
            continue
        try:
            lines = fetch_lines(site)
        except Exception as e:  # keep going if one site fails
            failures += 1
            print(f"[error] {site['name']}: {e}")
            continue

        if not lines:
            print(f"[warn] {site['name']}: no matching text (page may be JavaScript-rendered)")

        if url not in state:
            print(f"[baseline] {site['name']}: saved {len(lines)} lines")
        else:
            old = set(state[url])
            new_lines = [l for l in lines if l not in old]
            if new_lines:
                notify(site, new_lines)
            else:
                print(f"[ok] {site['name']}: no changes")
        state[url] = lines

    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), "utf-8")
    if failures == len([s for s in SITES if not s["url"].startswith("PUT_URL")]) and failures:
        sys.exit(1)


if __name__ == "__main__":
    main()

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
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
TEST_NOTIFY = os.environ.get("TEST_NOTIFY", "").strip().lower() == "true"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; CourseMonitor/1.0)"}

# ------------------------------------------------------------------
# عدّل هذه القائمة: ضع رابط صفحة الدورات/التسجيل الفعلية لكل جهة.
# selector: اختياري، لتحديد جزء من الصفحة فقط (مثل "main" أو ".courses").
# keywords: لا يُنبَّه إلا إذا ظهر سطر جديد يحتوي إحدى هذه الكلمات.
# ------------------------------------------------------------------
KEYWORDS = ["تسجيل", "التقديم", "معسكر", "دورة", "برنامج", "bootcamp", "apply", "register"]

SITES = [
    # موقع طويق يحمّل المحتوى بالجافاسكربت، لذلك render=True (متصفح آلي)
    {"name": "أكاديمية طويق - الرئيسية", "url": "https://tuwaiq.edu.sa", "selector": None, "keywords": KEYWORDS, "render": True},
    {"name": "أكاديمية طويق - الأخبار", "url": "https://tuwaiq.edu.sa/news", "selector": None, "keywords": KEYWORDS, "render": True},
    # صفحات تجميع إعلانات تعمل بدون متصفح آلي (إعلانات الأكاديمية الرقمية وبرامج التدريب المنتهي بالتوظيف)
    {"name": "إعلانات الأكاديمية الرقمية (وظيفتك علينا)", "url": "https://www.wdeftksa.com/sa/company/611-alljobs-sda-edu-sa", "selector": None, "keywords": KEYWORDS},
    {"name": "برامج التدريب المنتهي بالتوظيف (وظيفتك علينا)", "url": "https://www.wdeftksa.com/sa/category/training-to-employment-jobs", "selector": None, "keywords": KEYWORDS},
    # أضف روابطك هنا (سدايا، مسك، تمهير...)
    {"name": "أكاديمية سدايا", "url": "PUT_URL_HERE", "selector": None, "keywords": KEYWORDS},
    {"name": "أكاديمية مسك", "url": "PUT_URL_HERE", "selector": None, "keywords": KEYWORDS},
    {"name": "تمهير", "url": "PUT_URL_HERE", "selector": None, "keywords": KEYWORDS},
]


def get_html(site):
    if site.get("render"):
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                ),
                locale="ar-SA",
            )
            page = context.new_page()
            page.goto(site["url"], wait_until="domcontentloaded", timeout=60000)
            try:
                page.wait_for_load_state("networkidle", timeout=30000)
            except Exception:
                pass
            page.wait_for_timeout(5000)
            html = page.content()
            print(f"[debug] {site['name']}: title={page.title()!r} html_len={len(html)}")
            browser.close()
            return html
    r = requests.get(site["url"], headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def fetch_lines(site):
    soup = BeautifulSoup(get_html(site), "html.parser")
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


def send_push(title, body, click=None):
    """يرسل إشعارًا عبر ntfy ويطبع نتيجة الإرسال في السجل. يرجع True عند النجاح."""
    if not NTFY_TOPIC:
        print("[push] NTFY_TOPIC is empty: the secret is missing or has the wrong name")
        return False
    headers = {"Title": title, "Priority": "high"}
    if click:
        headers["Click"] = click
    try:
        r = requests.post(
            f"{NTFY_SERVER}/{NTFY_TOPIC}",
            data=body.encode("utf-8"),
            headers=headers,
            timeout=30,
        )
        print(f"[push] sent to topic of length {len(NTFY_TOPIC)}, HTTP {r.status_code}")
        return r.ok
    except Exception as e:
        print(f"[push] failed: {e}")
        return False


def notify(site, new_lines):
    body = f"{site['name']}\n" + "\n".join(f"- {l[:140]}" for l in new_lines[:5])
    print("ALERT:\n" + body)
    send_push("New course update", body, click=site["url"])


def load_state():
    if not STATE_FILE.exists():
        return {}
    try:
        data = json.loads(STATE_FILE.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"[warn] state.json is not valid JSON ({e}); starting from a fresh baseline")
        return {}


def main():
    if TEST_NOTIFY:
        ok = send_push("Test from course monitor", "إذا وصلك هذا الإشعار فالتنبيهات تعمل.")
        print("[test] push OK" if ok else "[test] push FAILED")
        sys.exit(0 if ok else 1)

    state = load_state()
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

        if not state.get(url):
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

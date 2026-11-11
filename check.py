#!/usr/bin/env python3
"""Watch Biggs Camera used gear and email when new listings appear.

Usage:
  python check.py            normal run (first run just records current listings)
  python check.py --test     email the 3 newest listings to verify email setup
Env DRY_RUN=1 writes preview.html instead of sending email.
"""
import html
import json
import os
import re
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE = "https://www.biggscameraused.com"
LIST_PATH = os.environ.get("LIST_PATH", "used-equipment/")
LIMIT = int(os.environ.get("LIMIT", "24"))            # items per page
MIN_PRICE = os.environ.get("MIN_PRICE", "0")
MAX_PRICE = os.environ.get("MAX_PRICE", "100000")
CHECK_PAGES = int(os.environ.get("CHECK_PAGES", "1"))  # pages checked each run
SEED_PAGES = int(os.environ.get("SEED_PAGES", "5"))    # pages recorded on first run
STATE_FILE = Path(os.environ.get("STATE_FILE", "seen.json"))
MAX_SEEN = 5000
DRY_RUN = os.environ.get("DRY_RUN") == "1"

PRICE_RE = re.compile(r"\$\s?[\d,]+(?:\.\d{2})?")
CART_RE = re.compile(r"/cart/add/(\d+)")
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; personal-gear-watcher/1.0)"}


def page_url(n):
    path = LIST_PATH if n == 1 else f"{LIST_PATH}page{n}.html"
    return (f"{BASE}/{path}?mode=grid&limit={LIMIT}&sort=newest"
            f"&min={MIN_PRICE}&max={MAX_PRICE}")


def is_product_img(img):
    src = img.get("src") or img.get("data-src") or ""
    return "shoplightspeed" in src and "/files/" in src


def parse_products(page_html):
    soup = BeautifulSoup(page_html, "html.parser")
    items, urls = [], set()
    for img in soup.find_all("img"):
        if not is_product_img(img):
            continue
        a = img.find_parent("a")
        if not a or not a.get("href"):
            continue
        url = urljoin(BASE, a["href"])
        if url in urls:
            continue
        urls.add(url)

        price, cart_id, node = "", None, a
        for _ in range(6):  # climb to the card that holds this one product
            node = node.parent
            if node is None:
                break
            if sum(1 for i in node.find_all("img") if is_product_img(i)) > 1:
                break
            m = PRICE_RE.search(node.get_text(" ", strip=True))
            if m:
                price = m.group(0).replace(" ", "")
                c = node.find("a", href=CART_RE)
                if c:
                    cart_id = CART_RE.search(c["href"]).group(1)
                break

        title = (img.get("alt") or img.get("title") or a.get("title") or "").strip()
        items.append({
            "id": cart_id or url,
            "title": title or url,
            "url": url,
            "image": urljoin(BASE, img.get("src") or img.get("data-src")),
            "price": price,
        })
    return items


def fetch_pages(n_pages):
    items = []
    for n in range(1, n_pages + 1):
        r = requests.get(page_url(n), headers=HEADERS, timeout=30)
        r.raise_for_status()
        found = parse_products(r.text)
        if n == 1 and not found:
            raise RuntimeError("No products parsed from page 1 - site layout may have changed.")
        items.extend(found)
        if len(found) < LIMIT:
            break
    return items


def build_email(items):
    n = len(items)
    rows = []
    for it in items:
        t, u = html.escape(it["title"]), html.escape(it["url"])
        rows.append(f"""
<tr><td style="padding:14px 0;border-bottom:1px solid #e5e5e5;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
    <td width="140" valign="top" style="padding-right:14px;">
      <a href="{u}"><img src="{html.escape(it['image'])}" width="130" alt="{t}"
         style="display:block;border:0;border-radius:6px;max-width:130px;height:auto;"></a>
    </td>
    <td valign="middle" style="font-family:Arial,sans-serif;">
      <a href="{u}" style="color:#111;font-size:16px;font-weight:bold;text-decoration:none;">{t}</a>
      <div style="color:#0a7d33;font-size:18px;font-weight:bold;margin:6px 0 10px;">{html.escape(it['price'])}</div>
      <a href="{u}" style="background:#111;color:#fff;padding:7px 14px;border-radius:4px;
         font-size:13px;text-decoration:none;">View listing</a>
    </td>
  </tr></table>
</td></tr>""")
    body = f"""<!doctype html><html><body style="margin:0;background:#f4f4f4;padding:20px 0;">
<table role="presentation" align="center" width="620" cellpadding="0" cellspacing="0"
 style="background:#fff;border-radius:8px;padding:20px 24px;max-width:100%;">
<tr><td style="font-family:Arial,sans-serif;">
  <h2 style="margin:0 0 4px;">New at Biggs Camera</h2>
  <div style="color:#666;font-size:14px;">{n} new used listing{'s' if n != 1 else ''}</div>
</td></tr>{''.join(rows)}
<tr><td style="font-family:Arial,sans-serif;color:#999;font-size:12px;padding-top:14px;">
  <a href="{BASE}/{LIST_PATH}?sort=newest" style="color:#999;">Browse all newest used gear</a>
</td></tr></table></body></html>"""
    text = "\n\n".join(f"{i['title']} - {i['price']}\n{i['url']}" for i in items)
    first = items[0]["title"]
    subject = f"Biggs: {first}" if n == 1 else f"Biggs: {n} new listings ({first[:40]}...)"
    return subject, text, body


def send_email(items):
    subject, text, body = build_email(items)
    if DRY_RUN:
        Path("preview.html").write_text(body, encoding="utf-8")
        print(f"[dry run] would send: {subject} -> preview.html")
        return
    user, pw, to = os.environ["SMTP_USER"], os.environ["SMTP_PASS"], os.environ["EMAIL_TO"]
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(body, "html", "utf-8"))
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    with smtplib.SMTP_SSL(host, int(os.environ.get("SMTP_PORT", "465")), timeout=30) as s:
        s.login(user, pw)
        s.sendmail(user, [a.strip() for a in to.split(",")], msg.as_string())
    print(f"Sent: {subject}")


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return None


def save_state(ids):
    STATE_FILE.write_text(json.dumps({"seen": ids[-MAX_SEEN:]}, indent=0))


def main():
    if "--test" in sys.argv:
        send_email(fetch_pages(1)[:3])
        return

    state = load_state()
    if state is None:
        items = fetch_pages(SEED_PAGES)
        save_state([i["id"] for i in reversed(items)])
        print(f"First run: recorded {len(items)} existing listings. No email sent.")
        return

    seen = state["seen"]
    seen_set = set(seen)
    new = [i for i in fetch_pages(CHECK_PAGES) if i["id"] not in seen_set]
    if not new:
        print("No new listings.")
        return
    send_email(new)  # if this raises, state isn't saved and we retry next run
    save_state(seen + [i["id"] for i in reversed(new)])


if __name__ == "__main__":
    main()

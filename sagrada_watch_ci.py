#!/usr/bin/env python3
"""Egyszeri ellenőrzés (GitHub Actions-höz). Ha nov. 3-6. között van szabad nap, e-mailt küld.
Titkos adatok környezeti változókból (GitHub Secrets): SMTP_USER, SMTP_PASS, MAIL_TO
"""
import os
import re
import smtplib
from email.message import EmailMessage

from playwright.sync_api import sync_playwright

URL = "https://tickets.sagradafamilia.org/en/1-individual/4375-sagrada-familia"
TARGET_MONTH = "November"
TARGET_DAYS = {3, 4, 5, 6}

# --- Ha nem találja a naptárat, ezeket kell hangolni ---
DAY_SELECTOR = "[class*='day'], [role='gridcell'], td button, td"
NEXT_MONTH_SELECTOR = "button[aria-label*='next' i], [class*='next' i]"
DISABLED_HINTS = ("disabled", "sold", "unavailable", "soldout", "no-avail", "outside")
# -------------------------------------------------------


def send_email(days):
    user, pwd, to = os.environ["SMTP_USER"], os.environ["SMTP_PASS"], os.environ["MAIL_TO"]
    msg = EmailMessage()
    msg["Subject"] = f"Sagrada Familia: szabad nap novemberben: {sorted(days)}"
    msg["From"], msg["To"] = user, to
    msg.set_content(f"Felszabadult hely! Foglalj most:\n{URL}")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(user, pwd)
        s.send_message(msg)
    print("E-mail elküldve.")


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_context(
            locale="en-US",
            viewport={"width": 1280, "height": 1600},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
        ).new_page()
        try:
            # A "networkidle" itt sosem teljesült, ezért elég a DOM betöltése
            page.goto(URL, wait_until="domcontentloaded", timeout=90000)
        except Exception as e:
            print("Betöltési hiba (folytatom, hogy legyen képernyőkép):", e)
        page.wait_for_timeout(8000)
        for label in ("Accept all", "Accept", "Aceptar", "OK"):
            try:
                page.get_by_role("button", name=re.compile(label, re.I)).first.click(timeout=2000)
                break
            except Exception:
                pass
        page.wait_for_timeout(3000)

        for _ in range(4):
            if TARGET_MONTH.lower() in page.inner_text("body").lower():
                break
            try:
                page.locator(NEXT_MONTH_SELECTOR).first.click(timeout=3000)
                page.wait_for_timeout(1000)
            except Exception:
                break

        # Képernyőkép és HTML mentése (a workflow feltölti, telefonról is megnézhető)
        page.screenshot(path="debug.png", full_page=True)
        open("debug.html", "w", encoding="utf-8").write(page.content())

        found = set()
        for el in page.locator(DAY_SELECTOR).all():
            try:
                text = (el.inner_text(timeout=1000) or "").strip()
            except Exception:
                continue
            m = re.match(r"^(\d{1,2})\b", text)
            if not m or int(m.group(1)) not in TARGET_DAYS:
                continue
            attrs = " ".join(filter(None, [
                el.get_attribute("class"),
                "disabled" if el.get_attribute("disabled") is not None else "",
                "disabled" if el.get_attribute("aria-disabled") == "true" else "",
            ])).lower()
            if not any(h in attrs for h in DISABLED_HINTS):
                found.add(int(m.group(1)))
        browser.close()

    print("Szabad napok:", sorted(found) or "nincs")
    if found:
        send_email(found)


if __name__ == "__main__":
    main()

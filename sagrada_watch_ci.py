#!/usr/bin/env python3
"""Sagrada Família jegyfigyelő GitHub Actions-höz.

A célhónap megjelenésekor megvizsgálja a megadott napokat, és csak akkor
küld e-mailt, ha a naptárban elérhetőnek látszó napot talál.

GitHub Secrets szükséges:
  SMTP_USER, SMTP_PASS, MAIL_TO
"""
import os
import re
import smtplib
from email.message import EmailMessage
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

URL = "https://tickets.sagradafamilia.org/en/1-individual/4375-sagrada-familia"
TARGET_MONTH = "November"
TARGET_DAYS = {3, 4, 5, 6}

NAVIGATION_TIMEOUT_MS = 60_000
CALENDAR_WAIT_MS = 5_000
DEBUG_SCREENSHOT = Path("debug.png")
DEBUG_HTML = Path("debug.html")

DAY_SELECTOR = "[class*='day'], [role='gridcell'], td button, td"
NEXT_MONTH_SELECTOR = 'button[aria-label*="next" i], [class*="next" i]'
DISABLED_HINTS = (
    "disabled", "sold", "unavailable", "soldout", "no-avail",
    "outside", "unbookable",
)


def save_debug(page):
    """Hibakeresési fájlok mentése; a sikertelen oldal is vizsgálható legyen."""
    try:
        page.screenshot(path=str(DEBUG_SCREENSHOT), full_page=True, timeout=15_000)
        print(f"Képernyőkép mentve: {DEBUG_SCREENSHOT}")
    except Exception as exc:
        print(f"A képernyőkép mentése nem sikerült: {exc}")

    try:
        DEBUG_HTML.write_text(page.content(), encoding="utf-8")
        print(f"HTML mentve: {DEBUG_HTML}")
    except Exception as exc:
        print(f"A HTML mentése nem sikerült: {exc}")


def send_email(days):
    user = os.environ.get("SMTP_USER")
    pwd = os.environ.get("SMTP_PASS")
    to = os.environ.get("MAIL_TO")
    missing = [name for name, value in (
        ("SMTP_USER", user), ("SMTP_PASS", pwd), ("MAIL_TO", to)
    ) if not value]
    if missing:
        raise RuntimeError(
            "Hiányzó GitHub Secrets / környezeti változók: " + ", ".join(missing)
        )

    msg = EmailMessage()
    msg["Subject"] = f"Sagrada Familia: lehetséges szabad nap novemberben: {sorted(days)}"
    msg["From"] = user
    msg["To"] = to
    msg.set_content(
        "A jegyoldal naptárában elérhetőnek látszó nap(ok): "
        f"{', '.join(map(str, sorted(days)))}\n\n"
        "Kérlek, ellenőrizd a foglalási oldalon, mert a naptár felépítése "
        "változhat, és az automatikus ellenőrzés tévedhet:\n"
        f"{URL}\n"
    )

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(user, pwd)
        smtp.send_message(msg)
    print("Értesítő e-mail elküldve.")


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            locale="en-US",
            viewport={"width": 1280, "height": 1600},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()
        page.set_default_timeout(10_000)

        try:
            response = None
            last_error = None

            # A networkidle helyett DOMContentLoaded: a foglalási oldalon
            # a háttérben folyamatos hálózati kérések futhatnak.
            for attempt in range(1, 3):
                try:
                    print(f"Oldalbetöltés ({attempt}/2): {URL}")
                    response = page.goto(
                        URL,
                        wait_until="domcontentloaded",
                        timeout=NAVIGATION_TIMEOUT_MS,
                    )
                    last_error = None
                    break
                except PlaywrightTimeoutError as exc:
                    last_error = exc
                    print(f"Navigációs timeout ({attempt}/2): {exc}")
                    if attempt < 2:
                        try:
                            page.goto(
                                "about:blank",
                                wait_until="load",
                                timeout=10_000,
                            )
                        except Exception:
                            pass

            if last_error is not None:
                save_debug(page)
                raise RuntimeError(
                    "A jegyoldal két próbálkozás után sem töltődött be. "
                    "A debug.png és debug.html fájlokat nézd meg az Actions artifactban."
                ) from last_error

            if response is not None:
                print(f"HTTP státuszkód: {response.status}")
                if response.status >= 400:
                    save_debug(page)
                    raise RuntimeError(f"A jegyoldal HTTP {response.status} hibát adott.")

            page.wait_for_timeout(CALENDAR_WAIT_MS)

            # Cookie-ablak elfogadása, ha megjelenik.
            for label in ("Accept all", "Accept", "Aceptar", "OK"):
                try:
                    page.get_by_role("button", name=re.compile(label, re.I)).first.click(
                        timeout=1_500
                    )
                    print(f"Cookie-ablak kezelve: {label}")
                    break
                except Exception:
                    continue

            page.wait_for_timeout(2_000)

            # Legfeljebb négy lépésben keressük a célhónapot.
            month_found = False
            for _ in range(5):
                body_text = page.locator("body").inner_text(timeout=10_000)
                if TARGET_MONTH.casefold() in body_text.casefold():
                    month_found = True
                    break
                try:
                    next_button = page.locator(NEXT_MONTH_SELECTOR).first
                    if next_button.count() == 0:
                        break
                    next_button.click(timeout=3_000)
                    page.wait_for_timeout(1_000)
                except Exception:
                    break

            if not month_found:
                save_debug(page)
                raise RuntimeError(
                    f"A(z) {TARGET_MONTH} hónapot nem találtam az oldalon. "
                    "Nem küldök értesítést, mert a naptár nem ellenőrizhető."
                )

            # A ténylegesen megjelenített oldalról mentjük a hibakeresési adatokat.
            save_debug(page)

            found = set()
            for element in page.locator(DAY_SELECTOR).all():
                try:
                    text = (element.inner_text(timeout=1_000) or "").strip()
                    match = re.match(r"^(\d{1,2})\b", text)
                    if not match:
                        continue

                    day = int(match.group(1))
                    if day not in TARGET_DAYS:
                        continue

                    classes = element.get_attribute("class") or ""
                    aria_disabled = element.get_attribute("aria-disabled") == "true"
                    disabled_attr = element.get_attribute("disabled") is not None
                    attrs = classes.lower()

                    if aria_disabled or disabled_attr:
                        continue
                    if any(hint in attrs for hint in DISABLED_HINTS):
                        continue

                    # Csak a látható elemeket vesszük figyelembe.
                    if not element.is_visible():
                        continue

                    found.add(day)
                except Exception:
                    continue

        finally:
            browser.close()

    print("Lehetséges szabad napok:", sorted(found) if found else "nincs")
    if found:
        send_email(found)
    else:
        print("Nem találtam elérhetőnek látszó cél-napot; e-mail nem ment ki.")


if __name__ == "__main__":
    main()

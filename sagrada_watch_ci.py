#!/usr/bin/env python3
"""Sagrada Família jegyfigyelő GitHub Actions-höz (Javított verzió)."""

import os
import re
import smtplib
from email.message import EmailMessage
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

URL = "https://tickets.sagradafamilia.org/en/1-individual/4375-sagrada-familia"
TARGET_MONTH = "November 2026"  # Hónap és év a pontos beazonosításhoz
TARGET_DAYS = {3, 4, 5, 6, 11}

NAVIGATION_TIMEOUT_MS = 60_000
CALENDAR_WAIT_MS = 5_000
DEBUG_SCREENSHOT = Path("debug.png")
DEBUG_HTML = Path("debug.html")


def save_debug(page):
    """Hibakeresési fájlok mentése."""
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
        "Kérlek, ellenőrizd a foglalási oldalon:\n"
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
            print(f"Oldalbetöltés: {URL}")
            response = page.goto(
                URL,
                wait_until="domcontentloaded",
                timeout=NAVIGATION_TIMEOUT_MS,
            )

            if response and response.status >= 400:
                save_debug(page)
                raise RuntimeError(f"A jegyoldal HTTP {response.status} hibát adott.")

            page.wait_for_timeout(CALENDAR_WAIT_MS)

            # 1. Cookie / Pop-up ablak bezárása (beleértve a "Close" és "Accept all" gombokat is)
            for label in ("Accept all", "Accept", "Close", "Aceptar", "OK"):
                try:
                    btn = page.get_by_role("button", name=re.compile(rf"^{label}$", re.I)).first
                    if btn.is_visible():
                        btn.click(timeout=2_000)
                        print(f"Pop-up/Cookie ablak bezárva: '{label}'")
                        page.wait_for_timeout(1_000)
                        break
                except Exception:
                    continue

            # 2. Léptetés a célhónaphoz (Pontosaria-label alapú gombkeresés)
            next_button_selector = '[aria-label*="Move forward to switch to the next month"]'
            month_found = False

            for attempt in range(6):
                # Ellenőrizzük, hogy a LÁTHATÓ naptárfejlécben szerepel-e a November 2026
                visible_captions = page.locator('.CalendarMonth[data-visible="true"] .CalendarMonth_caption').all_inner_texts()
                if any(TARGET_MONTH.lower() in cap.lower() for cap in visible_captions):
                    month_found = True
                    print(f"Sikeresen elérve: {TARGET_MONTH}")
                    break

                # Léptetés a következő hónapra
                next_btn = page.locator(next_button_selector).first
                if next_btn.is_visible():
                    print("Léptetés a következő hónapra...")
                    next_btn.click(timeout=3_000)
                    page.wait_for_timeout(1_500)
                else:
                    print("Nem található vagy nem kattintható a léptető gomb.")
                    break

            save_debug(page)

            if not month_found:
                raise RuntimeError(
                    f"A(z) {TARGET_MONTH} hónap nem jelent meg a látható naptárban."
                )

            # 3. Szabad napok keresése a látható November hónapon belül
            found = set()
            # Csak a látható novemberi hónap celláit vizsgáljuk
            november_month = page.locator('.CalendarMonth[data-visible="true"]').filter(has_text=TARGET_MONTH)
            day_cells = november_month.locator('.CalendarDay').all()

            for cell in day_cells:
                try:
                    aria_label = cell.get_attribute("aria-label") or ""
                    classes = cell.get_attribute("class") or ""
                    
                    # Ellenőrizzük a nap számát a gomb szövegéből
                    text = cell.inner_text().strip()
                    match = re.search(r"\b(\d{1,2})\b", text)
                    if not match:
                        continue
                    
                    day = int(match.group(1))
                    if day not in TARGET_DAYS:
                        continue

                    # Elérhetőség ellenőrzése:
                    # HA nem foglalható, a Clorian 'CalendarDay__blocked_calendar' vagy 'aria-disabled="true"' attribútumot ad neki.
                    aria_disabled = cell.get_attribute("aria-disabled") == "true"
                    is_blocked = "blocked" in classes or "disabled" in classes or "no-availability" in classes

                    if not aria_disabled and not is_blocked:
                        print(f"Elérhető napot találtam: November {day}")
                        found.add(day)

                except Exception as e:
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
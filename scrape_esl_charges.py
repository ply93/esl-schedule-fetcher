import asyncio
import re
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
from playwright.async_api import async_playwright

CHARGE_URL = "https://www.emiratesline.com/services-and-information/carrier-charge-finder/"
OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)

# (code, 顯示名／autocomplete keyword)
ORIGINS = [
    ("HKHKG", "HONG KONG"),
]

DESTINATIONS = [
    ("AEJEA", "JEBEL ALI"),
]


def empty_row(o_code, o_name, d_code, d_name, cargo, msg, scraped_at, raw=""):
    return {
        "OriginCode": o_code,
        "OriginName": o_name,
        "DestCode": d_code,
        "DestName": d_name,
        "CargoType": cargo,
        "ChargeName": msg,
        "Terminal": "",
        "Per20": "",
        "Per40": "",
        "Per40HC": "",
        "RawColumns": raw,
        "ScrapedAt": scraped_at,
    }


def parse_table(html, o_code, o_name, d_code, d_name, cargo, scraped_at):
    rows = []
    # 只搵有 CHARGES 欄嘅表
    tables = re.findall(r"<table[^>]*>(.*?)</table>", html, re.S | re.I)
    for table_html in tables:
        if not re.search(r"CHARGES", table_html, re.I):
            continue
        trs = re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, re.S | re.I)
        for tr in trs:
            cells = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, re.S | re.I)
            cells = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
            if len(cells) < 2:
                continue
            if cells[0].upper() in ("CHARGES", "CHARGE"):
                continue
            rows.append({
                "OriginCode": o_code,
                "OriginName": o_name,
                "DestCode": d_code,
                "DestName": d_name,
                "CargoType": cargo,
                "ChargeName": cells[0],
                "Terminal": cells[1] if len(cells) > 1 else "",
                "Per20": cells[2] if len(cells) > 2 else "",
                "Per40": cells[3] if len(cells) > 3 else "",
                "Per40HC": cells[4] if len(cells) > 4 else "",
                "RawColumns": " | ".join(cells),
                "ScrapedAt": scraped_at,
            })
        if rows:
            break
    return rows


async def set_port(page, which: str, code: str, keyword: str):
    """
    which: origin / destination
    1) 填可見 input
    2) 試 autocomplete 點選
    3) 強制寫 hidden port code（關鍵）
    """
    input_id = "originPort" if which == "origin" else "destinationPort"
    hidden_id = "originPortCode" if which == "origin" else "destinationPortCode"

    el = page.locator(f"#{input_id}")
    await el.click()
    await el.fill("")
    await el.type(keyword, delay=50)
    await page.wait_for_timeout(1500)

    # 試撳 autocomplete 項目
    try:
        item = page.locator(".ui-autocomplete .ui-menu-item, .ui-menu .ui-menu-item").first
        if await item.count() > 0 and await item.is_visible():
            await item.click()
            await page.wait_for_timeout(400)
    except Exception:
        try:
            await el.press("ArrowDown")
            await page.wait_for_timeout(200)
            await el.press("Enter")
            await page.wait_for_timeout(400)
        except Exception:
            pass

    # 強制寫 hidden code + 觸發 change（網站靠呢兩個 field 先 enable Search）
    await page.evaluate(
        """([hiddenId, inputId, code, keyword]) => {
            const hidden = document.getElementById(hiddenId);
            const input = document.getElementById(inputId);
            if (hidden) {
                hidden.value = code;
                hidden.dispatchEvent(new Event('input', { bubbles: true }));
                hidden.dispatchEvent(new Event('change', { bubbles: true }));
            }
            if (input && !input.value) {
                input.value = keyword;
                input.dispatchEvent(new Event('input', { bubbles: true }));
                input.dispatchEvent(new Event('change', { bubbles: true }));
            }
            // enable submit
            document.querySelectorAll('button[type=submit], button.primary-btn').forEach(btn => {
                btn.disabled = false;
                btn.removeAttribute('disabled');
            });
        }""",
        [hidden_id, input_id, code, keyword],
    )
    await page.wait_for_timeout(300)


async def select_cargo(page, cargo: str):
    if cargo.lower() == "reefer":
        loc = page.locator("#reefer")
    else:
        loc = page.locator("#dry")
    if await loc.count() > 0:
        await loc.check()
    else:
        await page.get_by_text(cargo.capitalize(), exact=True).click()
    await page.wait_for_timeout(200)


async def scrape_one(page, o_code, o_kw, d_code, d_kw, cargo: str, scraped_at: str):
    print(f"  {o_code} → {d_code} ({cargo})")
    try:
        await page.goto(CHARGE_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(2500)

        # CAPTCHA / validation 頁
        body_text = await page.locator("body").inner_text()
        if "validation required" in body_text.lower() or "captcha" in body_text.lower():
            return [empty_row(
                o_code, o_kw, d_code, d_kw, cargo.capitalize(),
                "(Blocked: CAPTCHA/validation page)", scraped_at, body_text[:200]
            )]

        await set_port(page, "origin", o_code, o_kw)
        await set_port(page, "destination", d_code, d_kw)
        await select_cargo(page, cargo)

        # 確認 hidden code
        codes = await page.evaluate(
            """() => ({
                o: document.getElementById('originPortCode')?.value || '',
                d: document.getElementById('destinationPortCode')?.value || ''
            })"""
        )
        print(f"    hidden codes: origin={codes.get('o')} dest={codes.get('d')}")

        btn = page.locator("button.primary-btn, button[type='submit']").first
        await btn.click(force=True)
        await page.wait_for_timeout(5000)

        # 等 table 出現
        try:
            await page.wait_for_selector("table:has-text('CHARGES'), table:has-text('TERMINAL')", timeout=8000)
        except Exception:
            pass

        html = await page.content()
        rows = parse_table(html, o_code, o_kw, d_code, d_kw, cargo.capitalize(), scraped_at)

        if not rows:
            # 再等一次
            await page.wait_for_timeout(4000)
            html = await page.content()
            rows = parse_table(html, o_code, o_kw, d_code, d_kw, cargo.capitalize(), scraped_at)

        if not rows:
            snippet = re.sub(r"\s+", " ", await page.locator("body").inner_text())[:300]
            return [empty_row(
                o_code, o_kw, d_code, d_kw, cargo.capitalize(),
                "(No charges found)", scraped_at, snippet
            )]
        return rows
    except Exception as e:
        return [empty_row(
            o_code, o_kw, d_code, d_kw, cargo.capitalize(),
            f"(Error: {e})", scraped_at
        )]


async def main():
    scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    all_rows = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            viewport={"width": 1400, "height": 900},
        )
        page = await context.new_page()

        total = len(ORIGINS) * len(DESTINATIONS) * 2
        done = 0
        for o_code, o_kw in ORIGINS:
            for d_code, d_kw in DESTINATIONS:
                for cargo in ("dry", "reefer"):
                    done += 1
                    print(f"[{done}/{total}]")
                    rows = await scrape_one(page, o_code, o_kw, d_code, d_kw, cargo, scraped_at)
                    all_rows.extend(rows)

        await browser.close()

    df = pd.DataFrame(all_rows)
    cols = [
        "OriginCode", "OriginName", "DestCode", "DestName", "CargoType",
        "ChargeName", "Terminal", "Per20", "Per40", "Per40HC", "RawColumns", "ScrapedAt",
    ]
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    df = df[cols]

    day = date.today().isoformat()
    dated = OUTPUT_DIR / f"esl_charges_{day}.xlsx"
    latest = OUTPUT_DIR / "esl_charges_latest.xlsx"
    df.to_excel(dated, index=False)
    df.to_excel(latest, index=False)

    # 簡單統計
    real = df[~df["ChargeName"].astype(str).str.startswith("(")]
    print(f"\n完成: {len(df)} 行 | 有效收費行: {len(real)}")
    print(f"已儲存: {dated}")
    print(f"已儲存: {latest}")


if __name__ == "__main__":
    asyncio.run(main())

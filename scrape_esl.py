import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from playwright.async_api import async_playwright

# 要查嘅航線 (origin_keyword, origin_code, dest_keyword, dest_code)
ROUTES = [
    ("NINGBO", "CNNGB", "JEBEL ALI", "AEJEA"),
    ("QINGDAO", "CNTAO", "JEBEL ALI", "AEJEA"),
    ("XINGANG", "CNXNG", "JEBEL ALI", "AEJEA"),
    ("WUHAN", "CNWUH", "JEBEL ALI", "AEJEA"),
    # 如果想試澳洲線可以加返，但預期多數無船期
    # ("ADELAIDE", "AUADL", "JEBEL ALI", "AEJEA"),
    # ("BRISBANE", "AUBNE", "JEBEL ALI", "AEJEA"),
]

OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)


async def fill_port(page, element_id: str, keyword: str):
    """輸入港口關鍵字並選第一個 autocomplete"""
    el = page.locator(f"#{element_id}")
    await el.click()
    await el.fill("")
    await el.type(keyword, delay=80)
    await page.wait_for_timeout(1200)
    await el.press("ArrowDown")
    await page.wait_for_timeout(400)
    await el.press("Enter")
    await page.wait_for_timeout(600)


async def scrape_route(page, origin_kw, origin_code, dest_kw, dest_code):
    route_name = f"{origin_code}→{dest_code}"
    print(f"正在查詢: {route_name}")

    await page.goto("https://www.emiratesline.com/schedule-search/", wait_until="domcontentloaded")
    await page.wait_for_timeout(2500)

    await fill_port(page, "originPort", origin_kw)
    await fill_port(page, "destinationPort", dest_kw)

    await page.locator("button[name='scheudle-search']").click()
    await page.wait_for_timeout(4500)

    scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    rows = []
    seen = set()

    tables = page.locator("table")
    count = await tables.count()

    for i in range(count):
        table = tables.nth(i)
        trs = table.locator("tr")
        tr_count = await trs.count()

        for r in range(tr_count):
            cells = trs.nth(r).locator("td")
            cell_count = await cells.count()
            if cell_count < 6:
                continue

            vals = []
            for c in range(cell_count):
                vals.append((await cells.nth(c).inner_text()).strip())

            # 預期: POL | ETD | Service | Vessel | Voyage | POD | ETA | Transit
            if len(vals) >= 8:
                pol, etd, service, vessel, voyage, pod, eta, transit = vals[:8]
            elif len(vals) >= 7:
                pol, etd, service, vessel, voyage, pod, eta = vals[:7]
                transit = ""
            else:
                continue

            # 過濾表頭
            if pol.lower() in ("port of loading", "departure", "pol") or not pol:
                continue

            key = (pol, etd, vessel, voyage, pod, eta)
            if key in seen:
                continue
            seen.add(key)

            rows.append({
                "Route": route_name,
                "POL": pol,
                "ETD": etd,
                "Service": service,
                "Vessel": vessel,
                "Voyage": voyage,
                "POD": pod,
                "ETA": eta,
                "TransitDays": transit.replace(" days", "").replace("days", "").strip(),
                "ScrapedAt": scraped_at,
            })

    if not rows:
        rows.append({
            "Route": route_name,
            "POL": "",
            "ETD": "",
            "Service": "",
            "Vessel": "(No schedule found)",
            "Voyage": "",
            "POD": "",
            "ETA": "",
            "TransitDays": "",
            "ScrapedAt": scraped_at,
        })

    print(f"  → {len(rows)} 行")
    return rows


async def main():
    all_rows = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="en-US",
            viewport={"width": 1400, "height": 900},
        )
        page = await context.new_page()

        for origin_kw, origin_code, dest_kw, dest_code in ROUTES:
            try:
                rows = await scrape_route(page, origin_kw, origin_code, dest_kw, dest_code)
                all_rows.extend(rows)
            except Exception as e:
                print(f"錯誤 {origin_code}→{dest_code}: {e}")
                all_rows.append({
                    "route": f"{origin_code}→{dest_code}",
                    "raw": f"(Error: {e})",
                    "scraped_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                })

        await browser.close()

    df = pd.DataFrame(all_rows)

    # 欄位順序
    cols = ["Route", "POL", "ETD", "Service", "Vessel", "Voyage", "POD", "ETA", "TransitDays", "ScrapedAt"]
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    df = df[cols]

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    dated_path = OUTPUT_DIR / f"esl_schedules_{today}.xlsx"
    latest_path = OUTPUT_DIR / "esl_schedules_latest.xlsx"

    df.to_excel(dated_path, index=False)
    df.to_excel(latest_path, index=False)

    print(f"\n完成，共 {len(df)} 行")
    print(f"已儲存: {dated_path}")
    print(f"已儲存: {latest_path}")


if __name__ == "__main__":
    asyncio.run(main())

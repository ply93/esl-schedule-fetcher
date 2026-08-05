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

    # 有機會出現 captcha，留少少時間
    await page.wait_for_timeout(1500)

    await fill_port(page, "originPort", origin_kw)
    await fill_port(page, "destinationPort", dest_kw)

    # 撳 Search（注意網站 button name 有 typo）
    btn = page.locator("button[name='scheudle-search']")
    await btn.click()
    await page.wait_for_timeout(4500)

    rows = []
    tables = page.locator("table")
    count = await tables.count()

    for i in range(count):
        table = tables.nth(i)
        text = await table.inner_text()
        if "Departure" not in text and "Port Of Loading" not in text and "ETD" not in text:
            continue

        trs = table.locator("tr")
        tr_count = await trs.count()
        for r in range(tr_count):
            line = (await trs.nth(r).inner_text()).strip()
            if not line or "Departure" in line or "Port Of Loading" in line:
                continue
            # 簡單整行保存，之後可以再拆欄
            rows.append({
                "route": route_name,
                "raw": line.replace("\n", " | "),
                "scraped_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            })

    if not rows:
        rows.append({
            "route": route_name,
            "raw": "(No schedule found)",
            "scraped_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
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
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    csv_path = OUTPUT_DIR / f"esl_schedules_{today}.csv"
    xlsx_path = OUTPUT_DIR / f"esl_schedules_{today}.xlsx"

    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    df.to_excel(xlsx_path, index=False)

    # 同時寫一份 latest，方便查看
    df.to_csv(OUTPUT_DIR / "esl_schedules_latest.csv", index=False, encoding="utf-8-sig")
    df.to_excel(OUTPUT_DIR / "esl_schedules_latest.xlsx", index=False)

    print(f"\n完成，共 {len(df)} 行")
    print(f"已儲存: {csv_path}")
    print(f"已儲存: {xlsx_path}")


if __name__ == "__main__":
    asyncio.run(main())

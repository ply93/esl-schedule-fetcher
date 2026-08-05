import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from playwright.async_api import async_playwright

# 去重後嘅唯一航線 (origin_code, origin_keyword, dest_code, dest_keyword)
ROUTES = [
    ("HKHKG", "HONG KONG", "SEGOT", "GOTHENBURG"),
    ("HKHKG", "HONG KONG", "NOOSL", "OSLO"),
    ("VNSGN", "HO CHI MINH", "NOTAE", "TANANGER"),
]

OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)
URL = "https://ecomm.one-line.com/one-ecom/schedule/point-to-point-schedule"


async def fill_autocomplete(page, placeholder_or_label: str, keyword: str):
    """盡量搵 Origin/Destination 輸入框並選第一個建議"""
    # 常見 selector（ONE 介面可能改，所以多幾個備選）
    candidates = [
        page.get_by_placeholder(placeholder_or_label),
        page.get_by_label(placeholder_or_label),
        page.locator(f"input[placeholder*='{placeholder_or_label}' i]"),
    ]
    el = None
    for c in candidates:
        try:
            if await c.count() > 0:
                el = c.first
                break
        except Exception:
            pass

    if el is None:
        # fallback：頁面上前兩個主要 text input
        inputs = page.locator("input[type='text'], input:not([type])")
        if "from" in placeholder_or_label.lower() or "origin" in placeholder_or_label.lower():
            el = inputs.nth(0)
        else:
            el = inputs.nth(1)

    await el.click()
    await el.fill("")
    await el.type(keyword, delay=60)
    await page.wait_for_timeout(1200)
    await el.press("ArrowDown")
    await page.wait_for_timeout(300)
    await el.press("Enter")
    await page.wait_for_timeout(500)


async def scrape_route(page, origin_code, origin_kw, dest_code, dest_kw):
    route = f"{origin_code}→{dest_code}"
    print(f"查詢: {route} ({origin_kw} → {dest_kw})")
    scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(3000)

    # 關閉可能出現嘅 cookie banner
    for text in ["Accept", "Accept All", "Agree", "I agree"]:
        try:
            btn = page.get_by_role("button", name=text)
            if await btn.count() > 0:
                await btn.first.click()
                await page.wait_for_timeout(500)
                break
        except Exception:
            pass

    try:
        await fill_autocomplete(page, "From", origin_kw)
        await fill_autocomplete(page, "To", dest_kw)
    except Exception as e:
        print(f"  輸入港口失敗: {e}")
        return [{
            "Route": route,
            "OriginCode": origin_code,
            "DestCode": dest_code,
            "Vessel": "",
            "Voyage": "",
            "ETD": "",
            "ETA": "",
            "TransitDays": "",
            "Service": "",
            "Raw": f"(Input error: {e})",
            "ScrapedAt": scraped_at,
        }]

    # 撳 Search
    try:
        search_btn = page.get_by_role("button", name="Search")
        if await search_btn.count() == 0:
            search_btn = page.locator("button:has-text('Search')")
        await search_btn.first.click()
    except Exception as e:
        print(f"  撳 Search 失敗: {e}")

    await page.wait_for_timeout(6000)

    rows = []
    # 嘗試由結果表擷取
    tables = page.locator("table")
    tcount = await tables.count()

    for i in range(tcount):
        table = tables.nth(i)
        trs = table.locator("tr")
        rcount = await trs.count()
        for r in range(rcount):
            cells = trs.nth(r).locator("td")
            ccount = await cells.count()
            if ccount < 3:
                continue
            vals = []
            for c in range(ccount):
                vals.append((await cells.nth(c).inner_text()).strip().replace("\n", " "))
            line = " | ".join(vals)
            if not line or "Vessel" in line and "ETD" in line:
                continue
            rows.append({
                "Route": route,
                "OriginCode": origin_code,
                "DestCode": dest_code,
                "Vessel": vals[0] if len(vals) > 0 else "",
                "Voyage": vals[1] if len(vals) > 1 else "",
                "ETD": vals[2] if len(vals) > 2 else "",
                "ETA": vals[3] if len(vals) > 3 else "",
                "TransitDays": vals[4] if len(vals) > 4 else "",
                "Service": vals[5] if len(vals) > 5 else "",
                "Raw": line,
                "ScrapedAt": scraped_at,
            })

    # 如果 table 結構唔穩，再抓結果區文字作備援
    if not rows:
        body_text = await page.locator("body").inner_text()
        if "No result" in body_text or "0 results" in body_text or "Total 0" in body_text:
            rows.append({
                "Route": route,
                "OriginCode": origin_code,
                "DestCode": dest_code,
                "Vessel": "(No schedule found)",
                "Voyage": "",
                "ETD": "",
                "ETA": "",
                "TransitDays": "",
                "Service": "",
                "Raw": "No schedule found",
                "ScrapedAt": scraped_at,
            })
        else:
            # 有內容但解析唔到，保留一段 raw 方便之後改 parser
            snippet = " ".join(body_text.split())[:300]
            rows.append({
                "Route": route,
                "OriginCode": origin_code,
                "DestCode": dest_code,
                "Vessel": "(Parse pending)",
                "Voyage": "",
                "ETD": "",
                "ETA": "",
                "TransitDays": "",
                "Service": "",
                "Raw": snippet,
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
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            viewport={"width": 1440, "height": 900},
        )
        page = await context.new_page()

        for origin_code, origin_kw, dest_code, dest_kw in ROUTES:
            try:
                rows = await scrape_route(page, origin_code, origin_kw, dest_code, dest_kw)
                all_rows.extend(rows)
            except Exception as e:
                print(f"錯誤 {origin_code}→{dest_code}: {e}")
                all_rows.append({
                    "Route": f"{origin_code}→{dest_code}",
                    "OriginCode": origin_code,
                    "DestCode": dest_code,
                    "Vessel": f"(Error: {e})",
                    "Voyage": "",
                    "ETD": "",
                    "ETA": "",
                    "TransitDays": "",
                    "Service": "",
                    "Raw": str(e),
                    "ScrapedAt": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                })

        await browser.close()

    df = pd.DataFrame(all_rows)
    cols = [
        "Route", "OriginCode", "DestCode", "Vessel", "Voyage",
        "ETD", "ETA", "TransitDays", "Service", "Raw", "ScrapedAt"
    ]
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    df = df[cols]

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    dated = OUTPUT_DIR / f"one_schedules_{today}.xlsx"
    latest = OUTPUT_DIR / "one_schedules_latest.xlsx"
    df.to_excel(dated, index=False)
    df.to_excel(latest, index=False)

    print(f"\n完成，共 {len(df)} 行")
    print(f"已儲存: {dated}")
    print(f"已儲存: {latest}")


if __name__ == "__main__":
    asyncio.run(main())

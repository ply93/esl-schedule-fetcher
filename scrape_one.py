import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from playwright.async_api import async_playwright

# 去重後唯一航線: (origin_code, origin_keyword, dest_code, dest_keyword)
# autocomplete 會優先選 (CY)
ROUTES = [
    ("HKHKG", "HONG KONG", "SEGOT", "GOTHENBURG"),
    ("HKHKG", "HONG KONG", "NOOSL", "OSLO"),
    ("VNSGN", "HO CHI MINH", "NOTAE", "TANANGER"),
    ("CNTAO", "QINGDAO", "ITSPE", "LA SPEZIA"),
]

OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)
URL = "https://ecomm.one-line.com/one-ecom/schedule/point-to-point-schedule"


async def dismiss_cookies(page):
    for text in ["Accept", "Accept All", "Agree", "I Agree", "Allow all"]:
        try:
            btn = page.get_by_role("button", name=text)
            if await btn.count() > 0:
                await btn.first.click()
                await page.wait_for_timeout(500)
                return
        except Exception:
            pass


async def fill_port(page, which: str, keyword: str):
    """
    which: "origin" / "destination"
    優先選 (CY)，避免 (DOOR)
    """
    # ONE 頁面常見係 From / To
    if which == "origin":
        locator = page.get_by_placeholder("From")
        if await locator.count() == 0:
            locator = page.locator("input").nth(0)
    else:
        locator = page.get_by_placeholder("To")
        if await locator.count() == 0:
            locator = page.locator("input").nth(1)

    el = locator.first
    await el.click()
    await el.fill("")
    await el.type(keyword, delay=50)
    await page.wait_for_timeout(1500)

    # 優先撳 (CY)
    try:
        cy = page.locator("text=/(CY)/i")
        if await cy.count() > 0:
            await cy.first.click()
            await page.wait_for_timeout(400)
            return
    except Exception:
        pass

    await el.press("ArrowDown")
    await page.wait_for_timeout(300)
    await el.press("Enter")
    await page.wait_for_timeout(400)


async def scrape_route(page, origin_code, origin_kw, dest_code, dest_kw):
    route = f"{origin_code}→{dest_code}"
    print(f"查詢: {route} ({origin_kw} → {dest_kw})")
    scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(3000)
    await dismiss_cookies(page)

    try:
        await fill_port(page, "origin", origin_kw)
        await fill_port(page, "destination", dest_kw)
    except Exception as e:
        print(f"  輸入港口失敗: {e}")
        return [{
            "Route": route,
            "OriginCode": origin_code,
            "DestCode": dest_code,
            "POL": "",
            "ETD": "",
            "Service": "",
            "Vessel": "",
            "Voyage": "",
            "POD": "",
            "ETA": "",
            "TransitDays": "",
            "Raw": f"(Input error: {e})",
            "ScrapedAt": scraped_at,
        }]

    # Search
    try:
        btn = page.get_by_role("button", name="Search")
        if await btn.count() == 0:
            btn = page.locator("button:has-text('Search')")
        await btn.first.click()
    except Exception as e:
        print(f"  撳 Search 失敗: {e}")

    await page.wait_for_timeout(7000)

    rows = []
    seen = set()

    # 優先解析 table
    tables = page.locator("table")
    tcount = await tables.count()
    for i in range(tcount):
        trs = tables.nth(i).locator("tr")
        rcount = await trs.count()
        for r in range(rcount):
            tds = trs.nth(r).locator("td")
            ccount = await tds.count()
            if ccount < 3:
                continue

            vals = []
            for c in range(ccount):
                vals.append((await tds.nth(c).inner_text()).strip().replace("\n", " "))

            line = " | ".join(vals)
            if not line:
                continue
            if "Vessel" in line and ("ETD" in line or "Departure" in line):
                continue
            if line in seen:
                continue
            seen.add(line)

            # 盡量對位；對唔到就入 Raw
            item = {
                "Route": route,
                "OriginCode": origin_code,
                "DestCode": dest_code,
                "POL": vals[0] if len(vals) > 0 else "",
                "ETD": vals[1] if len(vals) > 1 else "",
                "Service": vals[2] if len(vals) > 2 else "",
                "Vessel": vals[3] if len(vals) > 3 else "",
                "Voyage": vals[4] if len(vals) > 4 else "",
                "POD": vals[5] if len(vals) > 5 else "",
                "ETA": vals[6] if len(vals) > 6 else "",
                "TransitDays": vals[7] if len(vals) > 7 else "",
                "Raw": line,
                "ScrapedAt": scraped_at,
            }
            rows.append(item)

    if not rows:
        body = await page.locator("body").inner_text()
        body_norm = " ".join(body.split())
        if any(x in body_norm for x in ["Total 0", "0 results", "No result", "No Result"]):
            rows.append({
                "Route": route,
                "OriginCode": origin_code,
                "DestCode": dest_code,
                "POL": origin_kw,
                "ETD": "",
                "Service": "",
                "Vessel": "(No schedule found)",
                "Voyage": "",
                "POD": dest_kw,
                "ETA": "",
                "TransitDays": "",
                "Raw": "No schedule found",
                "ScrapedAt": scraped_at,
            })
        else:
            rows.append({
                "Route": route,
                "OriginCode": origin_code,
                "DestCode": dest_code,
                "POL": origin_kw,
                "ETD": "",
                "Service": "",
                "Vessel": "(Parse pending)",
                "Voyage": "",
                "POD": dest_kw,
                "ETA": "",
                "TransitDays": "",
                "Raw": body_norm[:300],
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
                    "POL": origin_kw,
                    "ETD": "",
                    "Service": "",
                    "Vessel": f"(Error: {e})",
                    "Voyage": "",
                    "POD": dest_kw,
                    "ETA": "",
                    "TransitDays": "",
                    "Raw": str(e),
                    "ScrapedAt": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                })

        await browser.close()

    df = pd.DataFrame(all_rows)
    cols = [
        "Route", "OriginCode", "DestCode",
        "POL", "ETD", "Service", "Vessel", "Voyage",
        "POD", "ETA", "TransitDays", "Raw", "ScrapedAt"
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

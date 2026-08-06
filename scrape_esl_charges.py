import asyncio
import re
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
from playwright.async_api import async_playwright

CHARGE_URL = "https://www.emiratesline.com/services-and-information/carrier-charge-finder/"
OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)

# CN / HK 出口
ORIGINS = [
    ("HKHKG", "HONG KONG"),
    ("CNNGB", "NINGBO"),
    ("CNSHA", "SHANGHAI"),
    ("CNTAO", "QINGDAO"),
    ("CNDLC", "DALIAN"),
    ("CNYTN", "YANTIAN"),
    ("CNSHE", "SHEKOU"),
    ("CNXMN", "XIAMEN"),
    ("CNNSA", "NANSHA"),
    ("CNXNG", "XINGANG"),
    ("CNWUH", "WUHAN"),
    ("CNCKG", "CHONGQING"),
]

# 常用目的港
DESTINATIONS = [
    ("AEJEA", "JEBEL ALI"),
    ("AEAUH", "ABU DHABI"),
    ("OMSOH", "SOHAR"),
    ("SGSIN", "SINGAPORE"),
    ("NLRTM", "ROTTERDAM"),
    ("BEANR", "ANTWERP"),
    ("GBFXT", "FELIXSTOWE"),
    ("ITGOA", "GENOA"),
    ("ITSPE", "LA SPEZIA"),
    ("ESBCN", "BARCELONA"),
    ("SAJED", "JEDDAH"),
    ("SADMM", "DAMMAM"),
    ("INNSA", "NHAVA SHEVA"),
    ("SEGOT", "GOTHENBURG"),
    ("NOOSL", "OSLO"),
    ("NOTAE", "TANANGER"),
]


def empty_row(o_code, o_name, d_code, d_name, cargo, msg, scraped_at):
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
        "RawColumns": "",
        "ScrapedAt": scraped_at,
    }


def parse_table(html, o_code, o_name, d_code, d_name, cargo, scraped_at):
    rows = []
    m = re.search(r"<table[^>]*>(.*?)</table>", html, re.S | re.I)
    if not m:
        return rows

    trs = re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(1), re.S | re.I)
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
    return rows


async def fill_port(page, input_id: str, keyword: str):
    el = page.locator(f"#{input_id}")
    await el.click()
    await el.fill("")
    await el.type(keyword, delay=40)
    await page.wait_for_timeout(1200)
    await el.press("ArrowDown")
    await page.wait_for_timeout(250)
    await el.press("Enter")
    await page.wait_for_timeout(400)


async def select_cargo(page, cargo: str):
    if cargo.lower() == "reefer":
        loc = page.locator("#reefer")
    else:
        loc = page.locator("#dry")
    if await loc.count() > 0:
        await loc.check()
    else:
        await page.get_by_label(cargo.capitalize()).check()
    await page.wait_for_timeout(200)


async def scrape_one(page, o_code, o_kw, d_code, d_kw, cargo: str, scraped_at: str):
    print(f"  {o_code} → {d_code} ({cargo})")
    try:
        await page.goto(CHARGE_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(2500)

        await fill_port(page, "originPort", o_kw)
        await fill_port(page, "destinationPort", d_kw)
        await select_cargo(page, cargo)

        await page.locator("button.primary-btn, button[type='submit']").first.click()
        await page.wait_for_timeout(4000)

        html = await page.content()
        rows = parse_table(html, o_code, o_kw, d_code, d_kw, cargo.capitalize(), scraped_at)
        if not rows:
            await page.wait_for_timeout(3000)
            html = await page.content()
            rows = parse_table(html, o_code, o_kw, d_code, d_kw, cargo.capitalize(), scraped_at)

        if not rows:
            return [empty_row(o_code, o_kw, d_code, d_kw, cargo.capitalize(), "(No charges found)", scraped_at)]
        return rows
    except Exception as e:
        return [empty_row(o_code, o_kw, d_code, d_kw, cargo.capitalize(), f"(Error: {e})", scraped_at)]


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

    print(f"\n完成: {len(df)} 行")
    print(f"已儲存: {dated}")
    print(f"已儲存: {latest}")


if __name__ == "__main__":
    asyncio.run(main())

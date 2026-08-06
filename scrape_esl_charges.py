import re
import time
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

PORT_LIST_URL = "https://www.emiratesline.com/wp-content/themes/esl/inc/api/origin-list-data.php"
CHARGE_URL = "https://www.emiratesline.com/services-and-information/carrier-charge-finder/"
OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Origin": "https://www.emiratesline.com",
    "Referer": CHARGE_URL,
}


def get_ports(session: requests.Session):
    r = session.get(PORT_LIST_URL, timeout=60)
    r.raise_for_status()
    data = r.json()
    ports = []
    seen = set()
    for item in data:
        code = (item.get("portCode") or item.get("value") or "").strip()
        name = (item.get("portName") or item.get("label") or code).strip()
        if code and code not in seen:
            seen.add(code)
            ports.append((code, name))
    return ports


def get_ncforminfo(session: requests.Session) -> str:
    r = session.get(CHARGE_URL, timeout=60)
    r.raise_for_status()
    m = re.search(r'name="__ncforminfo"[^>]*value="([^"]*)"', r.text)
    return m.group(1) if m else ""


def parse_charge_table(html: str, origin_code, origin_name, dest_code, dest_name, cargo, scraped_at):
    rows = []
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        return rows

    headers = [th.get_text(strip=True) for th in table.find_all("th")]
    # 常見: CHARGES | TERMINAL | DRY Per 20' | DRY Per 40' | HIGH Per 40'
    # Reefer 可能欄名唔同，一律用 header 動態對

    for tr in table.find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) < 2:
            continue

        charge_name = cells[0]
        terminal = cells[1] if len(cells) > 1 else ""

        # 其餘欄位按 header 對應
        amounts = {}
        for i, h in enumerate(headers[2:], start=2):
            amounts[h] = cells[i] if i < len(cells) else ""

        # 展平成多欄；同時保留 raw
        row = {
            "OriginCode": origin_code,
            "OriginName": origin_name,
            "DestCode": dest_code,
            "DestName": dest_name,
            "CargoType": cargo,
            "ChargeName": charge_name,
            "Terminal": terminal,
            "ScrapedAt": scraped_at,
        }
        # 標準化常見欄
        row["Per20"] = amounts.get("DRY Per 20’") or amounts.get("DRY Per 20'") or amounts.get("REEFER Per 20’") or amounts.get("Per 20’") or (cells[2] if len(cells) > 2 else "")
        row["Per40"] = amounts.get("DRY Per 40’") or amounts.get("DRY Per 40'") or amounts.get("REEFER Per 40’") or amounts.get("Per 40’") or (cells[3] if len(cells) > 3 else "")
        row["Per40HC"] = amounts.get("HIGH Per 40’") or amounts.get("HIGH Per 40'") or amounts.get("HC Per 40’") or (cells[4] if len(cells) > 4 else "")
        row["RawColumns"] = " | ".join(f"{h}={amounts.get(h,'')}" for h in headers[2:]) if headers[2:] else " | ".join(cells[2:])

        rows.append(row)
    return rows


def fetch_route_charges(session, token, origin_code, origin_name, dest_code, dest_name, cargo, scraped_at):
    data = {
        "originPort": origin_name,
        "originPortCode": origin_code,
        "destinationPort": dest_name,
        "destinationPortCode": dest_code,
        "cargoType": cargo.lower(),  # dry / reefer
        "ncformfield": "",
        "__ncforminfo": token,
    }
    try:
        r = session.post(CHARGE_URL, data=data, headers=HEADERS, timeout=60)
        if r.status_code == 429:
            time.sleep(8)
            r = session.post(CHARGE_URL, data=data, headers=HEADERS, timeout=60)
        r.raise_for_status()
        rows = parse_charge_table(r.text, origin_code, origin_name, dest_code, dest_name, cargo.capitalize(), scraped_at)
        if not rows:
            return [{
                "OriginCode": origin_code,
                "OriginName": origin_name,
                "DestCode": dest_code,
                "DestName": dest_name,
                "CargoType": cargo.capitalize(),
                "ChargeName": "(No charges found)",
                "Terminal": "",
                "Per20": "",
                "Per40": "",
                "Per40HC": "",
                "RawColumns": "",
                "ScrapedAt": scraped_at,
            }]
        return rows
    except Exception as e:
        return [{
            "OriginCode": origin_code,
            "OriginName": origin_name,
            "DestCode": dest_code,
            "DestName": dest_name,
            "CargoType": cargo.capitalize(),
            "ChargeName": f"(Error: {e})",
            "Terminal": "",
            "Per20": "",
            "Per40": "",
            "Per40HC": "",
            "RawColumns": "",
            "ScrapedAt": scraped_at,
        }]


def main():
    scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    session = requests.Session()
    session.headers.update(HEADERS)

    print("下載港口清單...")
    ports = get_ports(session)
    origins = [(c, n) for c, n in ports if c.startswith(("CN", "HK"))]
    destinations = [(c, n) for c, n in ports if not c.startswith(("CN", "HK"))]
    print(f"Origin CN/HK: {len(origins)} | Destination 其他: {len(destinations)}")
    print(f"預計查詢次數: {len(origins) * len(destinations) * 2} (含 Dry+Reefer)")

    token = get_ncforminfo(session)
    print("取得 form token")

    all_rows = []
    total = len(origins) * len(destinations) * 2
    done = 0

    for o_code, o_name in origins:
        for d_code, d_name in destinations:
            for cargo in ("dry", "reefer"):
                done += 1
                print(f"[{done}/{total}] {o_code} → {d_code} ({cargo})")
                rows = fetch_route_charges(
                    session, token, o_code, o_name, d_code, d_name, cargo, scraped_at
                )
                all_rows.extend(rows)
                time.sleep(0.35)

            # 每 30 次航線換一次 token，減低被擋機會
            if done % 60 == 0:
                try:
                    token = get_ncforminfo(session)
                except Exception:
                    pass

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
    main()

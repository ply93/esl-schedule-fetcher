import re
import time
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import requests

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
        code, name = "", ""

        if isinstance(item, dict):
            code = (item.get("portCode") or item.get("value") or "").strip()
            name = (item.get("portName") or item.get("label") or code).strip()
        elif isinstance(item, str):
            # 例如: "NINGBO, CHINA (CNNGB)"
            name = item.strip()
            m = re.search(r"\(([A-Z0-9]+)\)\s*$", name)
            code = m.group(1) if m else ""
        else:
            continue

        if code and code not in seen:
            seen.add(code)
            ports.append((code, name))

    return ports


def get_ncforminfo(session: requests.Session) -> str:
    r = session.get(CHARGE_URL, timeout=60)
    r.raise_for_status()
    m = re.search(r'name="__ncforminfo"[^>]*value="([^"]*)"', r.text)
    return m.group(1) if m else ""


def parse_charge_table(html, origin_code, origin_name, dest_code, dest_name, cargo, scraped_at):
    rows = []
    m = re.search(r"<table[^>]*>(.*?)</table>", html, re.S | re.I)
    if not m:
        return rows

    table_html = m.group(1)
    trs = re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, re.S | re.I)

    for tr in trs:
        cells = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, re.S | re.I)
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
        if len(cells) < 2:
            continue
        # 跳過表頭
        if cells[0].upper() in ("CHARGES", "CHARGE"):
            continue

        rows.append({
            "OriginCode": origin_code,
            "OriginName": origin_name,
            "DestCode": dest_code,
            "DestName": dest_name,
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
        rows = parse_charge_table(
            r.text, origin_code, origin_name, dest_code, dest_name, cargo.capitalize(), scraped_at
        )
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
    ports = None
    last_err = None
    for attempt in range(5):
        try:
            ports = get_ports(session)
            if ports:
                break
            last_err = "empty port list"
        except Exception as e:
            last_err = e
            print(f"  港口清單 attempt {attempt + 1} 失敗: {e}")
            time.sleep(3 * (attempt + 1))

    if not ports:
        raise SystemExit(f"無法取得港口清單: {last_err}")

    origins = [(c, n) for c, n in ports if c.startswith(("CN", "HK"))]
    destinations = [(c, n) for c, n in ports if not c.startswith(("CN", "HK"))]
    print(f"Origin CN/HK: {len(origins)} | Destination 其他: {len(destinations)}")
    print(f"預計查詢次數: {len(origins) * len(destinations) * 2} (含 Dry+Reefer)")

    if not origins:
        raise SystemExit("找不到 CN/HK 起運港")
    if not destinations:
        raise SystemExit("找不到目的港")

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

            # 每 30 條航線刷新 token
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

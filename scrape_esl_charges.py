import re
import time
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import requests

CHARGE_URL = "https://www.emiratesline.com/services-and-information/carrier-charge-finder/"
PORT_LIST_URLS = [
    "https://www.emiratesline.com/wp-content/themes/esl/inc/api/origin-list-data.php",
    "https://www.emiratesline.com/wp-content/themes/esl/inc/api/destination-list-data.php",
    "https://www.emiratesline.com/wp-content/themes/esl/inc/api/form-origin-list-data.php",
]

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

# API 失效時用呢份後備清單（可自行增刪）
# 起運：CN / HK
FALLBACK_ORIGINS = [
    ("HKHKG", "HONG KONG, HONG KONG (HKHKG)"),
    ("CNNGB", "NINGBO, CHINA (CNNGB)"),
    ("CNSHA", "SHANGHAI, CHINA (CNSHA)"),
    ("CNTAO", "QINGDAO, CHINA (CNTAO)"),
    ("CNDLC", "DALIAN, CHINA (CNDLC)"),
    ("CNYTN", "YANTIAN, CHINA (CNYTN)"),
    ("CNSHE", "SHEKOU, CHINA (CNSHE)"),
    ("CNXMN", "XIAMEN, CHINA (CNXMN)"),
    ("CNNSA", "NANSHA, CHINA (CNNSA)"),
    ("CNTXG", "TIANJIN XINGANG, CHINA (CNTXG)"),
    ("CNXNG", "XINGANG, CHINA (CNXNG)"),
    ("CNWUH", "WUHAN, CHINA (CNWUH)"),
    ("CNCKG", "CHONGQING, CHINA (CNCKG)"),
    ("CNNJG", "NANJING, CHINA (CNNJG)"),
    ("CNZJG", "ZHANGJIAGANG, CHINA (CNZJG)"),
    ("CNFOC", "FUZHOU, CHINA (CNFOC)"),
    ("CNCAN", "GUANGZHOU, CHINA (CNCAN)"),
    ("CNLYG", "LIANYUNGANG, CHINA (CNLYG)"),
]

# 目的：常見海外港（非 CN/HK）
FALLBACK_DESTINATIONS = [
    ("AEJEA", "JEBEL ALI, U.A.E. (AEJEA)"),
    ("AEAUH", "ABU DHABI, U.A.E. (AEAUH)"),
    ("AEDXB", "DUBAI, U.A.E. (AEDXB)"),
    ("AESHJ", "SHARJAH, U.A.E. (AESHJ)"),
    ("OMSOH", "SOHAR, OMAN (OMSOH)"),
    ("SGSIN", "SINGAPORE, SINGAPORE (SGSIN)"),
    ("MYPKG", "PORT KELANG, MALAYSIA (MYPKG)"),
    ("NLRTM", "ROTTERDAM, NETHERLANDS (NLRTM)"),
    ("BEANR", "ANTWERP, BELGIUM (BEANR)"),
    ("DEHAM", "HAMBURG, GERMANY (DEHAM)"),
    ("GBFXT", "FELIXSTOWE, U.K. (GBFXT)"),
    ("ITGOA", "GENOA, ITALY (ITGOA)"),
    ("ITSPE", "LA SPEZIA, ITALY (ITSPE)"),
    ("ESBCN", "BARCELONA, SPAIN (ESBCN)"),
    ("ESVLC", "VALENCIA, SPAIN (ESVLC)"),
    ("EGALY", "ALEXANDRIA, EGYPT (EGALY)"),
    ("EGPSD", "PORT SAID, EGYPT (EGPSD)"),
    ("SAJED", "JEDDAH, SAUDI ARABIA (SAJED)"),
    ("SADMM", "DAMMAM, SAUDI ARABIA (SADMM)"),
    ("INNSA", "NHAVA SHEVA, INDIA (INNSA)"),
    ("INMAA", "CHENNAI, INDIA (INMAA)"),
    ("LKCMB", "COLOMBO, SRI LANKA (LKCMB)"),
    ("PKKHI", "KARACHI, PAKISTAN (PKKHI)"),
    ("BDCGP", "CHITTAGONG, BANGLADESH (BDCGP)"),
    ("KRPUS", "BUSAN, SOUTH KOREA (KRPUS)"),
    ("JPYOK", "YOKOHAMA, JAPAN (JPYOK)"),
    ("JPTYO", "TOKYO, JAPAN (JPTYO)"),
    ("AUMEL", "MELBOURNE, AUSTRALIA (AUMEL)"),
    ("AUSYD", "SYDNEY, AUSTRALIA (AUSYD)"),
    ("ZADUR", "DURBAN, SOUTH AFRICA (ZADUR)"),
    ("BRSSZ", "SANTOS, BRAZIL (BRSSZ)"),
    ("USLAX", "LOS ANGELES, U.S.A. (USLAX)"),
    ("USNYC", "NEW YORK, U.S.A. (USNYC)"),
    ("SEGOT", "GOTHENBURG, SWEDEN (SEGOT)"),
    ("NOOSL", "OSLO, NORWAY (NOOSL)"),
    ("NOTAE", "TANANGER, NORWAY (NOTAE)"),
]


def parse_port_item(item):
    code, name = "", ""
    if isinstance(item, dict):
        code = (item.get("portCode") or item.get("value") or "").strip()
        name = (item.get("portName") or item.get("label") or code).strip()
    elif isinstance(item, str):
        name = item.strip()
        m = re.search(r"\(([A-Z0-9]+)\)\s*$", name)
        code = m.group(1) if m else ""
    return code, name


def get_ports_from_api(session: requests.Session):
    """嘗試官方 API；失敗回傳空 list。"""
    for url in PORT_LIST_URLS:
        try:
            r = session.get(url, timeout=60)
            if r.status_code != 200:
                print(f"  {url} HTTP {r.status_code}")
                continue
            data = r.json()
            if isinstance(data, dict) and data.get("error"):
                print(f"  {url} error: {data.get('error')}")
                continue
            if not isinstance(data, list) or not data:
                print(f"  {url} empty/invalid list")
                continue

            ports, seen = [], set()
            for item in data:
                code, name = parse_port_item(item)
                if code and code not in seen:
                    seen.add(code)
                    ports.append((code, name))
            if ports:
                print(f"  使用 API: {url} ({len(ports)} ports)")
                return ports
        except Exception as e:
            print(f"  {url} 失敗: {e}")
    return []


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
        "cargoType": cargo.lower(),
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
    ports = get_ports_from_api(session)

    if ports:
        origins = [(c, n) for c, n in ports if c.startswith(("CN", "HK"))]
        destinations = [(c, n) for c, n in ports if not c.startswith(("CN", "HK"))]
    else:
        print("API 不可用，改用內建 FALLBACK 港口清單")
        origins = list(FALLBACK_ORIGINS)
        destinations = list(FALLBACK_DESTINATIONS)

    print(f"Origin CN/HK: {len(origins)} | Destination: {len(destinations)}")
    print(f"預計查詢次數: {len(origins) * len(destinations) * 2} (Dry+Reefer)")

    if not origins or not destinations:
        raise SystemExit("起運或目的港清單為空")

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

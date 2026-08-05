import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

# 去重後航線 (porCode, delCode)
ROUTES = [
    ("HKHKG", "SEGOT"),
    ("HKHKG", "NOOSL"),
    ("VNSGN", "NOTAE"),
    ("CNTAO", "ITSPE"),
]

API_URL = "https://ecomm.one-line.com/api/v1/schedule/point-to-point"
OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://ecomm.one-line.com/one-ecom/schedule/point-to-point-schedule",
    "Origin": "https://ecomm.one-line.com",
}


def fetch_schedule(por_code: str, del_code: str, from_date: str, to_date: str) -> list[dict]:
    """查一條航線，返回整理後 rows。"""
    route = f"{por_code}→{del_code}"
    scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    params = {
        "porCode": por_code,
        "delCode": del_code,
        "fromDate": from_date,
        "toDate": to_date,
        "rcvTermCode": "Y",  # CY
        "deTermCode": "Y",   # CY
        "tsFlag": "",        # All (direct + transshipment)
    }

    try:
        r = requests.get(API_URL, params=params, headers=HEADERS, timeout=60)
        if r.status_code == 429:
            print(f"  rate limited，等 10 秒再試: {route}")
            time.sleep(10)
            r = requests.get(API_URL, params=params, headers=HEADERS, timeout=60)

        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  錯誤 {route}: {e}")
        return [{
            "Route": route,
            "OriginCode": por_code,
            "DestCode": del_code,
            "POL": "",
            "ETD": "",
            "Service": "",
            "Vessel": f"(Error: {e})",
            "Voyage": "",
            "POD": "",
            "ETA": "",
            "TransitDays": "",
            "Transshipment": "",
            "ScrapedAt": scraped_at,
        }]

    lines = data.get("scheduleLines") or []
    if not lines:
        return [{
            "Route": route,
            "OriginCode": por_code,
            "DestCode": del_code,
            "POL": "",
            "ETD": "",
            "Service": "",
            "Vessel": "(No schedule found)",
            "Voyage": "",
            "POD": "",
            "ETA": "",
            "TransitDays": "",
            "Transshipment": "",
            "ScrapedAt": scraped_at,
        }]

    rows = []
    for line in lines:
        # 主航段資料
        pol = line.get("polName") or line.get("porName") or ""
        etd = line.get("polDepartureDate") or line.get("porDepartureDate") or ""
        pod = line.get("podName") or line.get("delName") or ""
        eta = line.get("podArrivalDate") or line.get("delArrivalDate") or ""
        transit = line.get("displayTransitDays") or line.get("oceanTransitTime") or ""
        ts_type = line.get("transshipmentType") or ""
        ts_count = line.get("totalTransshipment") or ""
        trunk = line.get("trunkVvd") or ""

        # journeys 有更細 vessel 資料；無就用 trunkVvd
        journeys = line.get("journeys") or []
        if journeys:
            # 取第一段（起運）同最後一段（到目的）資訊
            first = journeys[0]
            last = journeys[-1]
            vessel = first.get("vsslName") or first.get("vesselName") or ""
            voyage = first.get("vesselName") or first.get("vesselCode") or trunk
            service = first.get("serviceLane") or ""
            if not etd:
                etd = first.get("departureDate") or ""
            if not eta:
                eta = last.get("berthingDate") or last.get("arrivalDate") or ""
            if not pol:
                pol = first.get("polName") or first.get("polLocationName") or ""
            if not pod:
                pod = last.get("podName") or last.get("podLocationName") or ""
            if not transit:
                transit = line.get("totalTransitTime") or first.get("transitTime") or ""
        else:
            vessel = trunk
            voyage = trunk
            service = ""

        rows.append({
            "Route": route,
            "OriginCode": por_code,
            "DestCode": del_code,
            "POL": pol,
            "ETD": etd,
            "Service": service,
            "Vessel": vessel,
            "Voyage": voyage,
            "POD": pod,
            "ETA": eta,
            "TransitDays": str(transit).replace(" day(s)", "").replace("days", "").strip(),
            "Transshipment": f"{ts_type} ({ts_count})" if ts_count != "" else str(ts_type),
            "ScrapedAt": scraped_at,
        })

    return rows


def main():
    today = date.today()
    from_date = today.isoformat()
    to_date = (today + timedelta(days=28)).isoformat()

    all_rows = []
    for por, dest in ROUTES:
        print(f"查詢: {por} → {dest}")
        rows = fetch_schedule(por, dest, from_date, to_date)
        print(f"  → {len(rows)} 行")
        all_rows.extend(rows)
        time.sleep(2)  # 避免 429

    df = pd.DataFrame(all_rows)
    cols = [
        "Route", "OriginCode", "DestCode",
        "POL", "ETD", "Service", "Vessel", "Voyage",
        "POD", "ETA", "TransitDays", "Transshipment", "ScrapedAt",
    ]
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    df = df[cols]

    day = today.isoformat()
    dated = OUTPUT_DIR / f"one_schedules_{day}.xlsx"
    latest = OUTPUT_DIR / "one_schedules_latest.xlsx"
    df.to_excel(dated, index=False)
    df.to_excel(latest, index=False)

    print(f"\n完成，共 {len(df)} 行")
    print(f"已儲存: {dated}")
    print(f"已儲存: {latest}")


if __name__ == "__main__":
    main()

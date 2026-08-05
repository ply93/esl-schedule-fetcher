import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

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


def empty_row(por, dest, msg, scraped_at):
    return {
        "Route": f"{por}→{dest}",
        "OriginCode": por,
        "DestCode": dest,
        "POL": "",
        "ETD": "",
        "Service": "",
        "Vessel": msg,
        "Voyage": "",
        "POD": "",
        "ETA": "",
        "TransitDays": "",
        "Transshipment": "",
        "ScrapedAt": scraped_at,
    }


def fetch_schedule(por_code, del_code, from_date, to_date):
    scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    params = {
        "porCode": por_code,
        "delCode": del_code,
        "fromDate": from_date,
        "toDate": to_date,
        "rcvTermCode": "Y",
        "deTermCode": "Y",
        "tsFlag": "",
    }

    data = None
    last_err = None
    for attempt in range(3):
        try:
            r = requests.get(API_URL, params=params, headers=HEADERS, timeout=60)
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            data = r.json()
            break
        except Exception as e:
            last_err = e
            time.sleep(3)

    if data is None:
        return [empty_row(por_code, del_code, f"(Error: {last_err})", scraped_at)]

    lines = data.get("scheduleLines") or []
    if not lines:
        return [empty_row(por_code, del_code, "(No schedule found)", scraped_at)]

    rows = []
    for line in lines:
        pol = line.get("polName") or line.get("porName") or ""
        etd = line.get("polDepartureDate") or line.get("porDepartureDate") or ""
        pod = line.get("podName") or line.get("delName") or ""
        eta = line.get("podArrivalDate") or line.get("delArrivalDate") or ""
        transit = line.get("displayTransitDays") or line.get("oceanTransitTime") or ""
        ts_type = line.get("transshipmentType") or ""
        ts_count = line.get("totalTransshipment")
        trunk = line.get("trunkVvd") or ""

        journeys = line.get("journeys") or []
        if journeys:
            first, last = journeys[0], journeys[-1]
            vessel = first.get("vsslName") or first.get("vesselName") or ""
            voyage = first.get("vesselName") or first.get("vesselCode") or trunk
            service = first.get("serviceLane") or ""
            etd = etd or first.get("departureDate") or ""
            eta = eta or last.get("berthingDate") or last.get("arrivalDate") or ""
            pol = pol or first.get("polName") or first.get("polLocationName") or ""
            pod = pod or last.get("podName") or last.get("podLocationName") or ""
        else:
            vessel, voyage, service = trunk, trunk, ""

        ts_text = f"{ts_type} ({ts_count})" if ts_count not in (None, "") else str(ts_type)

        rows.append({
            "Route": f"{por_code}→{del_code}",
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
            "Transshipment": ts_text,
            "ScrapedAt": scraped_at,
        })
    return rows


def main():
    today = date.today()
    from_date = today.isoformat()
    to_date = (today + timedelta(days=28)).isoformat()

    all_rows = []
    for por, dest in ROUTES:
        print(f"[ONE] {por} → {dest}")
        rows = fetch_schedule(por, dest, from_date, to_date)
        print(f"  → {len(rows)} 行")
        all_rows.extend(rows)
        time.sleep(3)

    df = pd.DataFrame(all_rows)
    cols = [
        "Route", "OriginCode", "DestCode", "POL", "ETD", "Service",
        "Vessel", "Voyage", "POD", "ETA", "TransitDays", "Transshipment", "ScrapedAt",
    ]
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    df = df[cols]

    day = today.isoformat()
    df.to_excel(OUTPUT_DIR / f"one_schedules_{day}.xlsx", index=False)
    df.to_excel(OUTPUT_DIR / "one_schedules_latest.xlsx", index=False)
    print(f"[ONE] 完成 {len(df)} 行")


if __name__ == "__main__":
    main()

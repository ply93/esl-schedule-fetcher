import time
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import requests

PORT_LIST_URL = "https://www.emiratesline.com/wp-content/themes/esl/inc/api/origin-list-data.php"
DEM_URL = "https://www.emiratesline.com/wp-content/themes/esl/inc/api/form-demmurage-charges.php"
ADV_URL = "https://www.emiratesline.com/wp-content/themes/esl/inc/api/form-advancement-charges.php"

OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.emiratesline.com/services-and-information/carrier-charge-finder/",
}


def cargo_type_from_equipment(eq: str) -> str:
    eq = (eq or "").upper()
    if eq.startswith(("RE", "SR", "RF")):
        return "Reefer"
    return "Dry"


def fetch_ports():
    r = requests.get(PORT_LIST_URL, headers=HEADERS, timeout=60)
    r.raise_for_status()
    data = r.json()
    ports = []
    seen = set()
    for item in data:
        code = item.get("portCode") or item.get("value") or ""
        name = item.get("portName") or item.get("label") or code
        if code and code not in seen:
            seen.add(code)
            ports.append((code, name))
    return ports


def fetch_charges(url: str, port_code: str):
    for attempt in range(3):
        try:
            r = requests.get(
                url,
                params={"portSelect": port_code},
                headers=HEADERS,
                timeout=60,
            )
            if r.status_code == 429:
                time.sleep(3 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == 2:
                return {"_error": str(e)}
            time.sleep(2)
    return {"_error": "unknown"}


def expand_rows(port_code, port_name, direction, payload, scraped_at):
    rows = []
    if not payload:
        return rows
    if payload.get("_error"):
        rows.append({
            "PortCode": port_code,
            "PortName": port_name,
            "Direction": direction,
            "ListType": "",
            "CargoType": "",
            "TariffType": "",
            "EquipmentType": "",
            "FromSlab": "",
            "ToSlab": "",
            "Amount": "",
            "Currency": "",
            "FromValidity": "",
            "ToValidity": "",
            "IntransitCode": "",
            "NOR": "",
            "Note": f"Error: {payload['_error']}",
            "ScrapedAt": scraped_at,
        })
        return rows

    for list_name in ("demList", "detList"):
        for item in payload.get(list_name) or []:
            eq = item.get("equipmentType") or ""
            rows.append({
                "PortCode": port_code,
                "PortName": port_name,
                "Direction": direction,  # Import / Export
                "ListType": list_name,   # demList / detList
                "CargoType": cargo_type_from_equipment(eq),
                "TariffType": item.get("tariffType") or "",
                "EquipmentType": eq,
                "FromSlab": item.get("fromSlab", ""),
                "ToSlab": item.get("toSlab", ""),
                "Amount": item.get("amount", ""),
                "Currency": item.get("currencyCode") or "",
                "FromValidity": item.get("fromValidityDate") or "",
                "ToValidity": item.get("toValidityDate") or "",
                "IntransitCode": item.get("intransitCode") or "",
                "NOR": item.get("nor", ""),
                "Note": "",
                "ScrapedAt": scraped_at,
            })
    return rows


def main():
    scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print("下載港口清單...")
    ports = fetch_ports()
    print(f"共 {len(ports)} 個港口")

    all_rows = []
    for i, (code, name) in enumerate(ports, 1):
        print(f"[{i}/{len(ports)}] {code} - {name}")

        # Import = demurrage API
        dem = fetch_charges(DEM_URL, code)
        all_rows.extend(expand_rows(code, name, "Import", dem, scraped_at))
        time.sleep(0.4)

        # Export = advancement API
        adv = fetch_charges(ADV_URL, code)
        all_rows.extend(expand_rows(code, name, "Export", adv, scraped_at))
        time.sleep(0.4)

    df = pd.DataFrame(all_rows)
    cols = [
        "PortCode", "PortName", "Direction", "ListType", "CargoType",
        "TariffType", "EquipmentType", "FromSlab", "ToSlab", "Amount",
        "Currency", "FromValidity", "ToValidity", "IntransitCode", "NOR",
        "Note", "ScrapedAt",
    ]
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    df = df[cols]

    # Dry + Reefer 都保留；如只要其中一種可再 filter
    day = date.today().isoformat()
    dated = OUTPUT_DIR / f"esl_charges_{day}.xlsx"
    latest = OUTPUT_DIR / "esl_charges_latest.xlsx"
    df.to_excel(dated, index=False)
    df.to_excel(latest, index=False)

    print(f"\n完成：{len(df)} 行")
    print(f"已儲存: {dated}")
    print(f"已儲存: {latest}")
    print(f"Dry: {(df['CargoType']=='Dry').sum()} / Reefer: {(df['CargoType']=='Reefer').sum()}")


if __name__ == "__main__":
    main()

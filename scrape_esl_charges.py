#!/usr/bin/env python3
"""
Emirates Shipping Line - Carrier Charge Finder Scraper
使用 Chromium + Selenium 自動填表 + 拎結果
"""

import time
import json
import argparse
from pathlib import Path
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager


def create_driver(headless: bool = False) -> webdriver.Chrome:
    options = Options()
    options.add_argument("--window-size=1400,900")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    if headless:
        options.add_argument("--headless=new")

    # 用 Chromium / Chrome
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)

    # 隱藏 webdriver 特徵
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {
            "source": """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            """
        },
    )
    return driver


def select_port(driver, input_id: str, hidden_id: str, port_text: str, timeout: int = 15):
    """輸入港口並選擇 autocomplete 建議，確保 hidden code 有值"""
    wait = WebDriverWait(driver, timeout)

    input_el = wait.until(EC.element_to_be_clickable((By.ID, input_id)))
    input_el.clear()
    input_el.send_keys(port_text)

    # 等 jQuery UI autocomplete 出現
    try:
        # 常見 selector
        suggestion = wait.until(
            EC.visibility_of_element_located(
                (By.CSS_SELECTOR, "ul.ui-autocomplete li.ui-menu-item")
            )
        )
        # 點第一個匹配或者包含文字嘅
        items = driver.find_elements(By.CSS_SELECTOR, "ul.ui-autocomplete li.ui-menu-item")
        clicked = False
        for item in items:
            if port_text.upper() in item.text.upper() or item.text.strip():
                item.click()
                clicked = True
                break
        if not clicked and items:
            items[0].click()
    except TimeoutException:
        # 有時直接 Enter 都可以
        input_el.send_keys(Keys.ENTER)

    time.sleep(0.8)

    # 確認 hidden code 有值
    hidden = driver.find_element(By.ID, hidden_id)
    code = hidden.get_attribute("value")
    if not code:
        raise ValueError(f"無法正確選擇港口：{port_text}（hidden code 為空）")
    print(f"  ✓ 已選 {port_text} → code = {code}")
    return code


def scrape_charges(
    origin: str,
    destination: str,
    cargo_type: str = "dry",
    headless: bool = False,
    save_html: bool = True,
):
    url = "https://www.emiratesline.com/services-and-information/carrier-charge-finder/"
    driver = create_driver(headless=headless)

    try:
        print(f"打開頁面：{url}")
        driver.get(url)
        time.sleep(2)

        # 選擇 Origin
        print(f"選擇 Origin: {origin}")
        select_port(driver, "originPort", "originPortCode", origin)

        # 選擇 Destination
        print(f"選擇 Destination: {destination}")
        select_port(driver, "destinationPort", "destinationPortCode", destination)

        # 選擇 Cargo Type
        cargo_type = cargo_type.lower()
        if cargo_type not in ("dry", "reefer"):
            raise ValueError("cargo_type 只能是 dry 或 reefer")

        radio = driver.find_element(By.ID, cargo_type)
        driver.execute_script("arguments[0].click();", radio)
        print(f"  ✓ Cargo Type = {cargo_type}")

        # 撳 Search
        search_btn = driver.find_element(By.CSS_SELECTOR, "button.primary-btn[type='submit']")
        search_btn.click()
        print("已提交，等待結果...")

        # 等結果出現（最多等 12 秒）
        time.sleep(3)
        try:
            WebDriverWait(driver, 10).until(
                lambda d: len(d.find_elements(By.CSS_SELECTOR, "table, .accordion, .result, [class*='charge']")) > 0
                or "no charge" in d.page_source.lower()
            )
        except TimeoutException:
            print("⚠ 等唔到明顯結果元素，繼續儲存頁面內容")

        # 儲存 HTML（方便之後分析 / 調試）
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if save_html:
            html_path = Path(f"result_{timestamp}.html")
            html_path.write_text(driver.page_source, encoding="utf-8")
            print(f"已儲存完整頁面 → {html_path}")

        # 嘗試提取表格資料
        tables = driver.find_elements(By.TAG_NAME, "table")
        results = []
        for i, table in enumerate(tables):
            rows = []
            for tr in table.find_elements(By.TAG_NAME, "tr"):
                cells = [td.text.strip() for td in tr.find_elements(By.CSS_SELECTOR, "th, td")]
                if any(cells):
                    rows.append(cells)
            if rows:
                results.append({"table_index": i, "rows": rows})

        # 另外掃一啲常見收費關鍵字
        page_text = driver.find_element(By.TAG_NAME, "body").text
        charge_lines = [
            line.strip()
            for line in page_text.splitlines()
            if any(kw in line.lower() for kw in ["fee", "charge", "thc", "b/l", "documentation", "amount", "usd", "aed", "cny"])
            and len(line.strip()) > 5
        ]

        output = {
            "origin": origin,
            "destination": destination,
            "cargo_type": cargo_type,
            "scraped_at": timestamp,
            "tables": results,
            "charge_related_lines": charge_lines[:50],  # 最多留 50 行
        }

        # 輸出 JSON
        json_path = Path(f"charges_{timestamp}.json")
        json_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已輸出 JSON → {json_path}")

        if charge_lines:
            print("\n=== 找到嘅收費相關文字（前 15 行）===")
            for line in charge_lines[:15]:
                print(" •", line)

        return output

    finally:
        driver.quit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ESL Carrier Charge Finder Scraper")
    parser.add_argument("--origin", required=True, help="例如: DUBAI, U.A.E. (AEDXB) 或 JEBEL ALI")
    parser.add_argument("--destination", required=True, help="例如: HONG KONG SAR, CHINA (HKHKG)")
    parser.add_argument("--cargo", default="dry", choices=["dry", "reefer"])
    parser.add_argument("--headless", action="store_true", help="無頭模式")
    args = parser.parse_args()

    scrape_charges(
        origin=args.origin,
        destination=args.destination,
        cargo_type=args.cargo,
        headless=args.headless,
    )

import os
import json
from datetime import datetime
from playwright.sync_api import sync_playwright

def run():
    # 初始化 Playwright 無頭瀏覽器
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # 模擬真實瀏覽器 User-Agent，避免被防火牆阻擋
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        
        print("正在打開 ESL 運費查詢網頁...")
        page.goto("https://emiratesline.com")
        page.wait_for_load_state("networkidle")
        
        # ─── 鎖定特定航線參數 ───
        origin_port = "HKHKG"   # Hong Kong
        destination_port = "SEGOT" # Gothenburg, Sweden
        
        print(f"【航線鎖定】正在自動輸入 起點: {origin_port} -> 終點: {destination_port}")
        
        # 模擬人類點擊、清空並輸入港口代碼
        # (備註：若 ESL 網頁有自動完成選單，用 fill 後可能需要觸發按鍵或點擊下拉選單項目)
        page.locator("input[name='origin']").fill(origin_port)
        page.locator("input[name='destination']").fill(destination_port)
        
        # 預設勾選 Cargo Type: Dry (一般乾貨櫃)
        # 如果需要 Reefer (冷凍櫃) 可以改為點擊 text=Reefer
        if page.locator("text=Dry").is_visible():
            page.click("text=Dry")
        
        # 點擊查詢按鈕
        print("正在提交查詢表單...")
        page.click("button:has-text('Search'), input[type='submit'], text=Search")
        
        # 彈性等待 5~8 秒讓後台 API 讀取並渲染表格
        page.wait_for_timeout(6000) 
        
        print("正在解析網頁附加費資料表格...")
        charge_data = []
        
        # 抓取傳回的費用明細
        # 根據阿聯酋官網實際表格架構，循環抓取 td 欄位
        rows = page.locator("table tr").all()
        if len(rows) <= 1:
            print("⚠️ 提示：未抓取到費用數據，可能是該航線無即時報價，或網頁選單需點擊觸發。")
        
        for row in rows[1:]:  # 跳過表頭第一列
            cells = row.locator("td").all_text_contents()
            if cells and len(cells) >= 3:
                charge_data.append({
                    "charge_code_name": cells[0].strip(), # 費用項目名稱
                    "currency": cells[1].strip(),         # 幣別 (如 USD / EUR / HKD)
                    "amount": cells[2].strip(),           # 金額
                    "billing_basis": cells[3].strip() if len(cells) > 3 else "Per Container" # 計費單位
                })
        
        # ─── 封裝成結構化 JSON ───
        result = {
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "route": {
                "from": origin_port,
                "to": destination_port
            },
            "status": "Success" if charge_data else "No Data/Check Layout",
            "carrier_charges": charge_data
        }
        
        # 儲存結果檔案
        output_filename = "hkhkg_to_segot_charges.json"
        with open(output_filename, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=4, ensure_ascii=False)
            
        print(f"🎉 自動化完成！數據已成功更新至 {output_filename}")
        browser.close()

if __name__ == "__main__":
    run()

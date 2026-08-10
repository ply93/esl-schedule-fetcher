import os
import json
from datetime import datetime
from playwright.sync_api import sync_playwright

def run():
    with sync_playwright() as p:
        # 啟動瀏覽器並加大視窗，確保響應式網頁不會變成手機版選單
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        
        print("正在打開 ESL 運費查詢網頁...")
        page.goto("https://emiratesline.com", timeout=60000)
        
        # 確保網頁核心元素加載完成
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(3000)  # 額外緩衝 3 秒等待 JS 渲染表單
        
        origin_port = "HKHKG"
        destination_port = "SEGOT"
        
        print(f"【航線鎖定】正在自動輸入 起點: {origin_port} -> 終點: {destination_port}")
        
        try:
            # ─── 優化：尋找「起點 (Origin)」輸入框 ───
            # 嘗試多種可能的前端屬性（Placeholder、文字鄰近、或是特殊的 autocomplete 屬性）
            origin_input = page.locator("input[placeholder*='Origin' i], input[placeholder*='起點' i], .select2-search__field").first
            
            # 如果透過 placeholder 找不到，改用標籤附近的 input
            if not origin_input.is_visible():
                origin_input = page.locator("//h3[contains(text(), 'Origin')]/following-sibling::input | //label[contains(text(), 'Origin')]/following-sibling::input").first

            # 點擊、清空並輸入起點
            origin_input.click()
            origin_input.fill("") # 清空
            origin_input.type(origin_port, delay=100) # 模擬真實打字速度，觸發下拉選單
            page.wait_for_timeout(1500) # 等待選單彈出
            page.keyboard.press("Enter") # 按下 Enter 鎖定選項
            print("  [✓] 起點輸入成功")

            # ─── 優化：尋找「終點 (Destination)」輸入框 ───
            destination_input = page.locator("input[placeholder*='Destination' i], input[placeholder*='終點' i]").first
            if not destination_input.is_visible():
                destination_input = page.locator("//h3[contains(text(), 'Destination')]/following-sibling::input | //label[contains(text(), 'Destination')]/following-sibling::input").first
                
            destination_input.click()
            destination_input.fill("")
            destination_input.type(destination_port, delay=100)
            page.wait_for_timeout(1500)
            page.keyboard.press("Enter")
            print("  [✓] 終點輸入成功")
            
            # ─── 選擇 Cargo Type ───
            # 尋找乾貨櫃選項
            dry_checkbox = page.locator("text=dry, text=Dry, input[value='dry']").first
            if dry_checkbox.is_visible():
                dry_checkbox.click()
            
            # ─── 點擊 Search ───
            print("正在提交查詢表單...")
            search_btn = page.locator("button:has-text('Search'), input[type='submit'], .btn-search, text=Search").first
            search_btn.click()
            
            # 等待資料表格渲染 (等 7 秒)
            page.wait_for_timeout(7000)
            
            # ─── 解析結果表格 ───
            print("正在解析網頁附加費資料表格...")
            charge_data = []
            
            # 抓取頁面上所有的 table row
            rows = page.locator("table tr").all()
            if len(rows) <= 1:
                print("⚠️ 提示：未抓取到費用數據。請確認官網當前是否有開通 HKHKG 到 SEGOT 的報價。")
            
            for row in rows[1:]:
                cells = row.locator("td").all_text_contents()
                if cells and len(cells) >= 3:
                    charge_data.append({
                        "charge_code_name": cells[0].strip(),
                        "currency": cells[1].strip(),
                        "amount": cells[2].strip(),
                        "billing_basis": cells[3].strip() if len(cells) > 3 else "Per Container"
                    })
            
            result = {
                "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "route": {"from": origin_port, "to": destination_port},
                "status": "Success" if charge_data else "No Data or Route Not Active",
                "carrier_charges": charge_data
            }
            
            output_filename = "hkhkg_to_segot_charges.json"
            with open(output_filename, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=4, ensure_ascii=False)
                
            print(f"🎉 自動化完成！數據已儲存至 {output_filename}")
            
        except Exception as e:
            print(f"❌ 執行過程中發生錯誤: {str(e)}")
            # 偵錯用：在 GitHub Actions 伺服器上截圖，方便查看當時網頁長怎樣
            page.screenshot(path="error_screenshot.png")
            print("已儲存錯誤截圖至 error_screenshot.png，您可以檢查專案目錄。")
            raise e
            
        finally:
            browser.close()

if __name__ == "__main__":
    run()

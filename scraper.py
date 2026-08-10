import os
import json
from datetime import datetime
from playwright.sync_api import sync_playwright

def run():
    with sync_playwright() as p:
        # 啟動 Chromium 瀏覽器
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 900}, # 放大視窗避免隱藏元素
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        
        print("正在打開 ESL 運費查詢網頁...")
        page.goto("https://emiratesline.com", timeout=60000)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(4000) # 緩衝等待彈窗完全載入
        
        # ─── 步驟 1：消除 Cookie 政策彈窗阻擋 ───
        print("檢查是否有 Cookie 彈窗阻擋...")
        # 常見的 Cookie 同意按鈕文字，如 "Accept", "Agree", "OK", "Allow all"
        cookie_buttons = page.locator("button:has-text('Accept'), button:has-text('Agree'), #onetrust-accept-btn-handler, .cookie-accept")
        if cookie_buttons.first.is_visible():
            try:
                cookie_buttons.first.click(timeout=3000)
                print("  [✓] 已自動點擊同意 Cookie 彈窗")
                page.wait_for_timeout(1000)
            except Exception:
                print("  [!] 有偵測到彈窗，但點擊時跳過")

        origin_port = "HKHKG"
        destination_port = "SEGOT"
        print(f"【航線鎖定】正在自動輸入 起點: {origin_port} -> 終點: {destination_port}")
        
        try:
            # ─── 步驟 2：精準填寫 起點 (Origin) ───
            # 根據 Log，ID 確定為 #originPort
            origin_input = page.locator("#originPort")
            
            # 使用 scroll_into_view_if_needed 確保它在畫面上
            origin_input.scroll_into_view_if_needed()
            
            # 強制點擊並填寫 (force=True 可以繞過被元素微幅遮擋的問題)
            origin_input.click(force=True)
            origin_input.fill("", force=True)
            origin_input.type(origin_port, delay=150)
            page.wait_for_timeout(2000) # 等待官方 Autocomplete 下拉選單跑出來
            page.keyboard.press("ArrowDown") # 往下選取第一個建議港口
            page.keyboard.press("Enter")     # 按下確認
            print("  [✓] 起點輸入並確認成功")

            # ─── 步驟 3：精準填寫 終點 (Destination) ───
            # 對應起點，終點 ID 為 #destinationPort
            destination_input = page.locator("#destinationPort")
            destination_input.scroll_into_view_if_needed()
            destination_input.click(force=True)
            destination_input.fill("", force=True)
            destination_input.type(destination_port, delay=150)
            page.wait_for_timeout(2000)
            page.keyboard.press("ArrowDown")
            page.keyboard.press("Enter")
            print("  [✓] 終點輸入並確認成功")
            
            # ─── 步驟 4：勾選貨物類型 ───
            dry_label = page.locator("label:has-text('dry'), label:has-text('Dry')")
            if dry_label.first.is_visible():
                dry_label.first.click(force=True)
            
            # ─── 步驟 5：點擊搜尋 ───
            print("正在提交查詢表單...")
            # 尋找 Search 按鈕
            search_btn = page.locator("button:has-text('Search'), input[type='submit'], .btn-search")
            search_btn.first.click(force=True)
            
            # 等待查詢結果載入（海運費 API 渲染通常較慢，給予 8 秒）
            print("等待運費數據加載中...")
            page.wait_for_timeout(8000)
            
            # ─── 步驟 6：解析並儲存運費表格 ───
            print("正在解析網頁附加費資料表格...")
            charge_data = []
            
            rows = page.locator("table tr").all()
            for row in rows[1:]:  # 跳過第一行表頭
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
                "status": "Success" if charge_data else "No Data (Check if route has active rate)",
                "carrier_charges": charge_data
            }
            
            output_filename = "hkhkg_to_segot_charges.json"
            with open(output_filename, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=4, ensure_ascii=False)
                
            print(f"🎉 自動化任務成功！數據已儲存至 {output_filename}")
            
        except Exception as e:
            print(f"❌ 執行過程中發生錯誤: {str(e)}")
            # 發生錯誤時依然會保留截圖，方便查看
            page.screenshot(path="error_screenshot.png")
            print("已儲存錯誤瞬間的螢幕截圖至 error_screenshot.png")
            raise e
            
        finally:
            browser.close()

if __name__ == "__main__":
    run()

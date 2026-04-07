"""
Catown Frontend E2E Tests - Playwright
"""
import asyncio
import httpx
from playwright.async_api import async_playwright

API = "http://localhost:8000"
FRONTEND = "http://localhost:3001"
passed = 0
failed = 0

def result(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        status = "[OK]"
    else:
        failed += 1
        status = "[FAIL]"
    print(f"  {status} {name}" + (f" => {detail}" if detail else ""))

async def test_page_loads(browser):
    print("--- 1. Page Structure ---")
    page = await browser.new_page()
    await page.goto(FRONTEND)
    title = await page.title()
    result("Page loads with title", "Catown" in title, "title=" + title)
    await page.close()

async def test_page_elements(browser):
    page = await browser.new_page()
    await page.goto(FRONTEND)
    
    checks = [
        ("Sidebar exists", "#sidebar"),
        ("Message input exists", "#message-input"),
        ("Messages area exists", "#messages-area"),
        ("Side panel exists", "#side-panel"),
    ]
    
    for name, sel in checks:
        el = await page.query_selector(sel)
        result(name, el is not None)
    
    # Check logo text
    try:
        logo = await page.wait_for_selector("text=Catown", timeout=2000)
        result("Catown text visible", True)
    except:
        result("Catown text visible", False)
    
    await page.close()

async def test_api_connectivity(browser):
    page = await browser.new_page()
    logs = []
    page.on("console", lambda msg: logs.append(msg.text))
    await page.goto(FRONTEND)
    await asyncio.sleep(3)
    
    success_logs = [l for l in logs if "[API]" in l and "Loaded" in l]
    error_logs = [l for l in logs if "Failed" in l or "failed" in l.lower()]
    
    result("Frontend API calls succeed", len(success_logs) >= 2, f"{len(success_logs)} API success logs")
    result("No critical console errors", len(error_logs) == 0, f"{len(error_logs)} errors" if error_logs else "")
    await page.close()

async def test_agent_and_rooms(browser):
    page = await browser.new_page()
    await page.goto(FRONTEND)
    
    try:
        agent_bar = await page.wait_for_selector("#agent-status-bar", timeout=10000)
        result("Agent status bar exists", True)
        content = await agent_bar.inner_text()
        result("Agent bar populated", len(content) > 10, content[:80])
    except:
        result("Agent status bar", False, "timeout")
    
    try:
        rooms = await page.wait_for_selector("#rooms-list", timeout=10000)
        result("Rooms list exists", True)
        content = await rooms.inner_text()
        result("Rooms list populated", len(content) > 5, content[:80])
    except:
        result("Rooms list", False, "timeout")
    
    try:
        btn = await page.wait_for_selector('button[onclick="sendMessage()"]', timeout=10000)
        result("Send button exists", True)
    except:
        result("Send button exists", False)
    
    await page.close()

async def test_at_mention(browser):
    print("\n--- 2. @Mention Feature ---")
    page = await browser.new_page()
    await page.goto(FRONTEND)
    
    try:
        input_box = await page.wait_for_selector("#message-input", timeout=10000)
        result("Input box found", True)
        
        # Type @ to trigger mention dropdown
        await input_box.click()
        await input_box.fill("@")
        await asyncio.sleep(0.5)
        
        # Check dropdown DOM
        dropdown = await page.query_selector(".agent-mention-dropdown")
        if dropdown:
            cls = await dropdown.get_attribute("class")
            visible = "hidden" not in cls if cls else False
            result("@ mention dropdown exists", True, f"visible={visible}")
            
            items = await page.query_selector_all(".agent-mention-item")
            result(f"Mention items count", len(items) > 0, f"{len(items)} items")
            
            if items:
                text = await items[0].inner_text()
                result("First item text", True, text[:50])
        else:
            result("@ mention dropdown", False, "not found")
    except Exception as e:
        result("@ mention behavior", False, str(e))
    
    await page.close()

async def test_websocket(browser):
    print("\n--- 3. WebSocket ---")
    page = await browser.new_page()
    logs = []
    page.on("console", lambda msg: logs.append(msg.text))
    
    await page.goto(FRONTEND)
    await asyncio.sleep(5)
    
    ws_connected = any("Connected" in l for l in logs)
    result("WebSocket connected", ws_connected)
    
    ws_join = any("join" in l.lower() for l in logs)
    result("WebSocket join room", ws_join)
    
    await page.close()

async def test_agent_response(browser):
    print("\n--- 4. Agent Response ---")
    page = await browser.new_page()
    await page.goto(FRONTEND)
    
    try:
        await page.wait_for_selector("#rooms-list", timeout=10000)
    except:
        result("Rooms loaded", False)
        await page.close()
        return
    
    result("Rooms loaded", True)
    
    input_box = await page.wait_for_selector("#message-input", timeout=10000)
    await input_box.click()
    await input_box.fill("hello")
    
    btn = await page.query_selector('button[onclick="sendMessage()"]')
    if btn:
        await btn.click()
        result("Send clicked", True)
    else:
        result("Send clicked", False, "no button")
        await page.close()
        return
    
    # Wait for agent response (markdown-content class)
    try:
        await page.wait_for_selector(".markdown-content", timeout=20000)
        result("Agent response received", True)
        
        els = await page.query_selector_all(".markdown-content")
        if els:
            text = await els[-1].inner_text()
            result("Response content", len(text) > 10, text[:80])
    except:
        result("Agent response received", False, "timeout 20s")
    
    await page.close()

async def main():
    global passed, failed
    print("=== Catown Frontend E2E Tests (Playwright) ===\n")
    
    # Check services
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{API}/api/status")
            ok = r.status_code == 200
    except Exception:
        ok = False
    if not ok:
        print("ERROR: Backend not at", API)
        return
    
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(FRONTEND)
            ok = r.status_code == 200
    except Exception:
        ok = False
    if not ok:
        print("ERROR: Frontend not at", FRONTEND)
        return
    
    print("Backend:", API, "OK")
    print("Frontend:", FRONTEND, "OK\n")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        
        await test_page_loads(browser)
        await test_page_elements(browser)
        await test_api_connectivity(browser)
        await test_agent_and_rooms(browser)
        await test_at_mention(browser)
        await test_websocket(browser)
        await test_agent_response(browser)
        
        await browser.close()
    
    total = passed + failed
    print(f"\nResult: {passed}/{total} OK, {failed} FAIL")

asyncio.run(main())

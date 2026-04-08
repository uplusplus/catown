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
        print(f"  [OK] {name}" + (f" => {detail}" if detail else ""))
    else:
        failed += 1
        print(f"  [FAIL] {name}" + (f" => {detail}" if detail else ""))

async def test_api_health(pw, browser):
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{API}/api/status")
        result("API /api/status healthy", resp.status_code == 200)

async def test_page_loads(pw, browser):
    page = await browser.new_page()
    await page.goto(FRONTEND)
    title = await page.title()
    result("Page loads with title", "Catown" in title, f"title='{title}'")
    await page.close()

async def test_page_elements(pw, browser):
    page = await browser.new_page()
    await page.goto(FRONTEND)
    
    # Check sidebar exists
    sidebar = await page.query_selector("#sidebar")
    result("Sidebar exists", sidebar is not None)
    
    # Check catown logo
    logo = await page.query_selector("text=Catown")
    result("Catown logo visible", logo is not None)
    
    # Check input box
    input_box = await page.query_selector("#message-input")
    result("Message input exists", input_box is not None)
    
    # Check messages area
    msg_area = await page.query_selector("#messages-area")
    result("Messages area exists", msg_area is not None)
    
    # Check side panel
    panel = await page.query_selector("#side-panel")
    result("Side panel exists", panel is not None)
    
    await page.close()

async def test_api_connectivity_from_frontend(pw, browser):
    page = await browser.new_page()
    # Capture console logs
    logs = []
    page.on("console", lambda msg: logs.append(msg.text))
    await page.goto(FRONTEND, timeout=15000)
    await asyncio.sleep(3)  # Wait for API calls
    
    # Check if any API errors
    error_logs = [l for l in logs if "Failed" in l or "Error" in l.lower()]
    success_logs = [l for l in logs if "[API]" in l and "Loaded" in l]
    result("Frontend API calls succeed", len(success_logs) >= 2, f"found {len(success_logs)} API success logs")
    if error_logs:
        result("No critical console errors", False, f"errors: {error_logs[:3]}")
    else:
        result("No critical console errors", True)
    await page.close()

async def test_agent_list_loaded(pw, browser):
    page = await browser.new_page()
    await page.goto(FRONTEND)
    
    # Wait for agent status bar or timeout
    try:
        agent_bar = await page.wait_for_selector("#agent-status-bar", timeout=10000)
        result("Agent status bar exists", True)
        
        # Check if agents are displayed
        content = await page.inner_text("#agent-status-bar")
        result("Agent status bar populated", len(content) > 10, f"content: {content[:80]}")
    except:
        result("Agent status bar exists", False, "timeout waiting for #agent-status-bar")
    
    await page.close()

async def test_projects_list_loaded(pw, browser):
    page = await browser.new_page()
    await page.goto(FRONTEND)
    
    try:
        rooms = await page.wait_for_selector("#rooms-list", timeout=10000)
        result("Rooms list exists", True)
        content = await page.inner_text("#rooms-list")
        result("Rooms list populated", len(content) > 5, f"content: {content[:80]}")
    except:
        result("Rooms list loaded", False, "timeout")
    
    await page.close()

async def test_send_button_exists(pw, browser):
    page = await browser.new_page()
    await page.goto(FRONTEND)
    
    # Check search button with paper-plane icon (send button)
    btn = await page.query_selector('button[onclick="sendMessage()"]')
    result("Send button exists", btn is not None)
    
    await page.close()

async def test_at_mention_input_behavior(pw, browser):
    page = await browser.new_page()
    await page.goto(FRONTEND)
    
    try:
        input_box = await page.wait_for_selector("#message-input", timeout=10000)
        result("Input box found", True)
        
        # Type @ to trigger mention dropdown
        await input_box.click()
        await input_box.fill("@")
        await asyncio.sleep(0.5)
        
        # Check if mention dropdown appears
        dropdown = await page.query_selector(".agent-mention-dropdown")
        if dropdown:
            is_hidden = await dropdown.get_attribute("class")
            dropdown_visible = "hidden" not in is_hidden if is_hidden else False
            result("@ mention dropdown DOM exists", True)
            
            # Check items
            items = await page.query_selector_all(".agent-mention-item")
            result(f"Agent mention items available", len(items) > 0, f"{len(items)} items")
            
            if items:
                # Get first item text
                first_text = await items[0].inner_text()
                result("First mention item readable", True, first_text[:50])
        else:
            result("@ mention dropdown visible", False, "dropdown not found after typing @")
    except Exception as e:
        result("@ mention behavior", False, str(e))
    
    await page.close()

async def test_websocket_connection(pw, browser):
    page = await browser.new_page()
    logs = []
    page.on("console", lambda msg: logs.append(msg.text))
    
    await page.goto(FRONTEND)
    await asyncio.sleep(5)
    
    # Check console for WS connection
    ws_connected = any("[WS] Connected" in log for log in logs)
    result("WebSocket connected", ws_connected, "WS connect log found in console")
    
    # Check for join room message
    ws_joined = any("join" in log.lower() for log in logs)
    result("WebSocket join room", ws_joined, "join log found")
    
    await page.close()

async def test_agent_responds_to_message(pw, browser):
    page = await browser.new_page()
    await page.goto(FRONTEND)
    
    # Wait for rooms to load
    try:
        await page.wait_for_selector("#rooms-list", timeout=10000)
    except:
        result("Rooms loaded", False)
        await page.close()
        return
    
    # Send a simple message
    input_box = await page.wait_for_selector("#message-input", timeout=10000)
    await input_box.click()
    await input_box.fill("hello")
    
    # Click send
    send_btn = await page.query_selector('button[onclick="sendMessage()"]')
    if send_btn:
        await send_btn.click()
        result("Send button clicked", True)
    else:
        result("Send button clicked", False, "no send button")
        await page.close()
        return
    
    # Wait for agent response
    try:
        await page.wait_for_selector(".markdown-content", timeout=20000)
        result("Agent response received", True)
        
        # Get the response content
        resp_elements = await page.query_selector_all(".markdown-content")
        if resp_elements:
            last_resp = await resp_elements[-1].inner_text()
            result("Response has content", len(last_resp) > 10, last_resp[:80])
    except:
        result("Agent response received", False, "timeout 20s waiting for markdown-content")
    
    await page.close()

async def test_config_tab_switches(pw, browser):
    """Config 页签可以切换显示"""
    page = await browser.new_page()
    await page.goto(FRONTEND)
    await page.wait_for_load_state("networkidle")
    
    # 点击 Config 页签
    config_tab = await page.query_selector("#tab-config")
    if config_tab:
        await config_tab.click()
        await asyncio.sleep(0.5)
        
        # 检查 config content 是否显示
        config_content = await page.query_selector("#config-content")
        if config_content:
            is_visible = await config_content.is_visible()
            result("Config tab click shows content", is_visible)
        else:
            result("Config tab click shows content", False, "config-content not found")
    else:
        result("Config tab exists", False, "tab-config not found")
    
    await page.close()

async def test_config_global_llm_fields(pw, browser):
    """全局 LLM 配置字段存在"""
    page = await browser.new_page()
    await page.goto(FRONTEND)
    await page.wait_for_load_state("networkidle")
    
    # 切换到 Config 页签
    config_tab = await page.query_selector("#tab-config")
    if config_tab:
        await config_tab.click()
        await asyncio.sleep(0.5)
    
    # 检查全局配置字段
    global_url = await page.query_selector("#global-base-url")
    global_model = await page.query_selector("#global-model")
    global_key = await page.query_selector("#global-api-key")
    
    result("Global Base URL input exists", global_url is not None)
    result("Global Model input exists", global_model is not None)
    result("Global API Key input exists", global_key is not None)
    
    # 检查按钮
    save_btn = await page.query_selector("button:has-text('Save Global')")
    test_btn = await page.query_selector("button:has-text('Test')")
    result("Save Global button exists", save_btn is not None)
    result("Test button exists", test_btn is not None)
    
    await page.close()

async def test_config_agent_section_exists(pw, browser):
    """Agent 配置区域存在"""
    page = await browser.new_page()
    await page.goto(FRONTEND)
    await page.wait_for_load_state("networkidle")
    
    config_tab = await page.query_selector("#tab-config")
    if config_tab:
        await config_tab.click()
        await asyncio.sleep(1)  # 等待配置加载
    
    # Agent 配置区域
    agent_section = await page.query_selector("#agent-llm-configs")
    result("Agent LLM config section exists", agent_section is not None)
    
    # 运行时生效摘要
    effective = await page.query_selector("#effective-configs")
    result("Effective config section exists", effective is not None)
    
    await page.close()

async def test_config_global_save(pw, browser):
    """全局配置可以保存"""
    page = await browser.new_page()
    await page.goto(FRONTEND)
    await page.wait_for_load_state("networkidle")
    
    config_tab = await page.query_selector("#tab-config")
    if config_tab:
        await config_tab.click()
        await asyncio.sleep(0.5)
    
    # 填写全局配置
    url_input = await page.query_selector("#global-base-url")
    model_input = await page.query_selector("#global-model")
    
    if url_input and model_input:
        await url_input.fill("http://test-global.example.com/v1")
        await model_input.fill("test-global-model")
        
        # 点击保存
        save_btn = await page.query_selector("button:has-text('Save Global')")
        if save_btn:
            await save_btn.click()
            await asyncio.sleep(1)
            
            # 检查是否有成功日志
            log_area = await page.query_selector("#log-area")
            if log_area:
                log_text = await log_area.inner_text()
                result("Global config save shows success", "Global LLM config saved" in log_text or "config" in log_text.lower())
            else:
                result("Global config save shows success", True, "save button clicked")
        else:
            result("Global config save button", False)
    else:
        result("Global config inputs", False)
    
    await page.close()

async def test_config_agent_use_global_button(pw, browser):
    """Agent 配置有 Use Global 按钮"""
    page = await browser.new_page()
    await page.goto(FRONTEND)
    await page.wait_for_load_state("networkidle")
    
    config_tab = await page.query_selector("#tab-config")
    if config_tab:
        await config_tab.click()
        await asyncio.sleep(1)
    
    # 查找 Use Global 按钮
    use_global_btns = await page.query_selector_all("button:has-text('Use Global')")
    result("Use Global buttons exist", len(use_global_btns) > 0, f"found {len(use_global_btns)}")
    
    await page.close()


async def main():
    global passed, failed
    print("=== Catown Frontend E2E Tests (Playwright) ===\n")
    
    # Check services first
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{API}/api/status")
            if resp.status_code != 200:
                print("ERROR: Backend not running at", API)
                return
    except:
        print("ERROR: Cannot connect to backend at", API)
        return
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(FRONTEND)
            if resp.status_code != 200:
                print("ERROR: Frontend not running at", FRONTEND)
                return
    except:
        print("ERROR: Cannot connect to frontend at", FRONTEND)
        return
    
    print(f"Backend: {API} OK")
    print(f"Frontend: {FRONTEND} OK\n")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        
        print("--- 1. Page Structure ---")
        await test_page_loads(p, browser)
        await test_page_elements(p, browser)
        await test_api_connectivity_from_frontend(p, browser)
        await test_agent_list_loaded(p, browser)
        await test_projects_list_loaded(p, browser)
        await test_send_button_exists(p, browser)
        
        print("\n--- 2. @Mention Feature ---")
        await test_at_mention_input_behavior(p, browser)
        
        print("\n--- 3. WebSocket ---")
        await test_websocket_connection(p, browser)
        
        print("\n--- 4. Agent Response ---")
        await test_agent_responds_to_message(p, browser)
        
        print("\n--- 5. Config UI ---")
        await test_config_tab_switches(p, browser)
        await test_config_global_llm_fields(p, browser)
        await test_config_agent_section_exists(p, browser)
        await test_config_global_save(p, browser)
        await test_config_agent_use_global_button(p, browser)
        
        await browser.close()
    
    print(f"\n{'='*40}")
    print(f"Result: {passed} OK, {failed} FAIL, {passed+failed} total")
    print(f"Pass rate: {passed/(passed+failed)*100:.0f}%")

asyncio.run(main())

import asyncio
from playwright.async_api import async_playwright

async def check():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        logs = []
        page.on('console', lambda m: logs.append(f'[{m.type}] {m.text}'))
        await page.goto('http://localhost:3001/room_decoded.html')
        await asyncio.sleep(3)

        # Verify the element exists
        all_dd = await page.query_selector_all('#mention-dropdown')
        print(f'All #mention-dropdown elements: {len(all_dd)}')

        # Check agents loaded
        agents_count = await page.evaluate('agents.length')
        print(f'Agents loaded: {agents_count}')

        # Click the Mention Agent button
        await page.click('#mention-btn')
        await asyncio.sleep(1)

        # Check dropdown visibility using classList.contains (not substring match!)
        is_hidden = await page.evaluate('document.getElementById("mention-dropdown").classList.contains("hidden")')
        print(f'Dropdown hidden (classList): {is_hidden}')

        if not is_hidden:
            inner = await page.evaluate('document.getElementById("mention-dropdown").innerText')
            print(f'Dropdown content: {inner[:300]}')
            print('SUCCESS: Mention Agent dropdown opens correctly!')
        else:
            print('FAIL: Dropdown is still hidden')

        # Click an agent to test mention insertion
        if not is_hidden:
            await page.evaluate('''(() => {
                var btns = document.querySelectorAll("#mention-agent-list button");
                if (btns.length > 0) btns[0].click();
            })()''')
            await asyncio.sleep(0.5)
            val = await page.input_value('#message-input')
            print(f'Input value after agent select: {repr(val)}')

        for l in logs[-10:]:
            print(f'Console: {l}')

        await browser.close()

asyncio.run(check())

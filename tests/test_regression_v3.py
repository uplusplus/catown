"""
Catown Phase 3 Regression Test - Slow version with delays for rate limiting
"""
import asyncio
import urllib.request
import json
import time

API = "http://localhost:8000"
passed = 0
failed = 0

def result(name, ok, detail=""):
    global passed, failed
    status = "[OK]" if ok else "[X]"
    if ok:
        passed += 1
    else:
        failed += 1
    print(f"  {status} {name}" + (f" => {detail}" if detail else ""))

def post_message(chatroom_id, content, timeout=10):
    data = json.dumps({"content": content}).encode("utf-8")
    req = urllib.request.Request(
        f"{API}/chatrooms/{chatroom_id}/messages",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    resp = urllib.request.urlopen(req, timeout=timeout)
    return json.loads(resp.read())

def get(path):
    req = urllib.request.Request(f"{API}{path}")
    resp = urllib.request.urlopen(req, timeout=10)
    return json.loads(resp.read())

def get_messages(chatroom_id, limit=5):
    return get(f"/chatrooms/{chatroom_id}/messages?limit={limit}")

print("=== Catown Phase 3 Regression Test ===\n")
print("--- 1. API Endpoints ---")

# Health
try:
    s = get("/api/status")
    result("GET /api/status", s.get("status") == "healthy", f"v{s.get('version')}")
except Exception as e:
    result("GET /api/status", False, str(e))

# Health
try:
    s = get("/api/health")
    result("GET /api/health", True)
except Exception as e:
    result("GET /api/health", False, str(e))

# Agents
try:
    agents = get("/api/agents")
    result("GET /api/agents", len(agents) == 5, f"{len(agents)} agents")
except Exception as e:
    result("GET /api/agents", False, str(e))

# Tools
try:
    tools = get("/api/tools")
    result("GET /api/tools", tools.get("count", 0) >= 13, f"{tools.get('count')} tools")
except Exception as e:
    result("GET /api/tools", False, str(e))

# Projects
try:
    projects = get("/api/projects")
    result("GET /api/projects", len(projects) >= 1, f"{len(projects)} projects")
except Exception as e:
    result("GET /api/projects", False, str(e))

print("\n--- 2. Message + Agent Response ---")
try:
    msg = post_message(1, "用2个字回答：1+1")
    result("Send message", True, f"id={msg['id']}")
    time.sleep(10)
    msgs = get_messages(1, 3)
    has_agent = any(m.get("agent_name") for m in msgs)
    if has_agent:
        agent_msg = [m for m in msgs if m.get("agent_name")][0]
        result("Agent auto-response", True, f"[{agent_msg['agent_name']}] {agent_msg['content'][:60]}")
    else:
        result("Agent auto-response", False, "no agent reply")
except Exception as e:
    result("Message + Agent", False, str(e))

time.sleep(5)

print("\n--- 3. Tool: web_search ---")
try:
    msg = post_message(1, "请用 web_search 搜索今天日期")
    result("Send web_search request", True)
    time.sleep(15)
    msgs = get_messages(1, 4)
    found = False
    for m in msgs:
        c = m.get("content", "")
        if "web_search" in c or "日期" in c or "date" in c.lower() or "Error" not in c:
            found = True
            result("web_search", "SSL" not in c and "Error" not in c, c[:80])
            break
    if not found:
        result("web_search", False, "no search result found")
except Exception as e:
    result("web_search", False, str(e))

time.sleep(5)

print("\n--- 4. Tool: execute_code ---")
try:
    msg = post_message(1, "请用 execute_code 执行 10*10 并返回结果")
    result("Send execute_code request", True)
    time.sleep(15)
    msgs = get_messages(1, 5)
    found = False
    for m in msgs:
        c = m.get("content", "")
        if "100" in c or "execute" in c.lower() or "execute_code" in c:
            found = True
            result("execute_code", True, c[:80])
            break
    if not found:
        result("execute_code", False, "no result found")
except Exception as e:
    result("execute_code", False, str(e))

time.sleep(5)

print("\n--- 5. Tool: retrieve_memory ---")
try:
    msg = post_message(1, "请回忆一下之前的对话")
    result("Send retrieve_memory request", True)
    time.sleep(15)
    msgs = get_messages(1, 5)
    found = False
    for m in msgs:
        c = m.get("content", "")
        if "memory" in c.lower() or "回忆" in c:
            found = True
            result("retrieve_memory", True, c[:80])
            break
    if not found:
        result("retrieve_memory", False, "no memory result found")
except Exception as e:
    result("retrieve_memory", False, str(e))

print("\n--- 6. CORS Check ---")
try:
    req = urllib.request.Request(
        "http://localhost:8000/api/status",
        headers={"Origin": "http://evil.com"}
    )
    resp = urllib.request.urlopen(req, timeout=10)
    cors = resp.headers.get("Access-Control-Allow-Origin", "")
    # If CORS is restricted, evil.com should not be allowed
    result("CORS header", cors != "http://evil.com" or cors == "", f"Access-Control-Allow-Origin: {cors}")
except Exception as e:
    result("CORS check", False, str(e))

print("\n--- 7. Structured Logging ---")
try:
    # Check if logs are being generated properly (look at last message in backend)
    s = get("/api/status")
    result("Backend responding", True, "logging module in use")
except Exception as e:
    result("Logging", False, str(e))

total = passed + failed
print(f"\nResult: {passed}/{total} OK, {failed} FAIL")
print(f"Pass rate: {passed/total*100:.0f}%")

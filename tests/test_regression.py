import urllib.request
import urllib.parse
import json
import time
import sys

API = "http://localhost:8000/api"
results = []

def test(name, ok, detail=""):
    status = "[PASS]" if ok else "[FAIL]"
    results.append({"name": name, "ok": ok, "detail": detail})
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

def get_messages(chatroom_id, limit=5):
    resp = urllib.request.urlopen(f"{API}/chatrooms/{chatroom_id}/messages?limit={limit}", timeout=10)
    return json.loads(resp.read())

def get(url, timeout=10):
    resp = urllib.request.urlopen(url, timeout=timeout)
    return json.loads(resp.read())

print("=== Catown Phase 2 回归测试 ===\n")

# 1. API 状态
print("--- 1. API 端点 ---")
try:
    s = get(f"{API}/status")
    test("GET /api/status", s.get("status") == "healthy", f"v{s.get('version')}, {s.get('stats',{}).get('agents')} agents")
except Exception as e:
    test("GET /api/status", False, str(e))

try:
    s = get(f"{API}/health")
    test("GET /api/health", True, "可用")
except urllib.error.HTTPError as e:
    if e.code == 404:
        test("GET /api/health", False, "404 — 路由可能未注册在根路径")
    else:
        test("GET /api/health", False, f"HTTP {e.code}")
except Exception as e:
    test("GET /api/health", False, str(e))

# 2. Agent 列表
try:
    agents = get(f"{API}/agents")
    names = [a["name"] for a in agents]
    test("GET /api/agents", "assistant" in names and "coder" in names and "reviewer" in names and "researcher" in names, f"{names}")
except Exception as e:
    test("GET /api/agents", False, str(e))

# 3. Tools 列表
try:
    tools = get(f"{API}/tools")
    test("GET /api/tools", tools.get("count", 0) == 13, f"{tools.get('count')} tools registered")
except Exception as e:
    test("GET /api/tools", False, str(e))

# 4. Projects 列表
try:
    projects = get(f"{API}/projects")
    test("GET /api/projects", len(projects) >= 1, f"{len(projects)} projects")
except Exception as e:
    test("GET /api/projects", False, str(e))

# 5. 消息链路
print("\n--- 2. 消息链路 ---")
try:
    msg = post_message(1, "你好，请用3个字回答")
    test("POST 发送用户消息", True, f"msg id={msg['id']}")
    time.sleep(8)
    msgs = get_messages(1, 3)
    last_agent = msgs[0] if msgs and msgs[0].get("agent_name") else None
    if last_agent and last_agent.get("agent_name"):
        test("Agent 自动响应", True, f"[{last_agent['agent_name']}] {last_agent['content'][:50]}")
    else:
        test("Agent 自动响应", False, "无 Agent 回复")
except Exception as e:
    test("消息链路", False, str(e))

# 6. web_search 工具
print("\n--- 3. web_search 工具 ---")
try:
    msg = post_message(1, "请用 web_search 搜索 Python 编程语言是什么")
    test("发送 web_search 请求", True, f"msg id={msg['id']}")
    time.sleep(15)
    msgs = get_messages(1, 4)
    found_search = False
    search_ok = False
    for m in msgs:
        if "web_search" in m.get("content", "") or "Python" in m.get("content", ""):
            found_search = True
            if "Error" not in m.get("content", "") and "SSL" not in m.get("content", ""):
                search_ok = True
                test("web_search 执行", True, f"返回 {m['content'][:80]}")
                break
    if found_search and not search_ok:
        test("web_search 执行", False, "执行但出现 SSL 错误")
    elif not found_search:
        test("web_search 执行", False, "Agent 未调用 web_search")
except Exception as e:
    test("web_search 工具", False, str(e))

# 7. execute_code 工具
print("\n--- 4. execute_code 工具 ---")
try:
    msg = post_message(1, "请用 execute_code 执行 print(41) 并返回结果")
    test("发送 execute_code 请求", True, f"msg id={msg['id']}")
    time.sleep(12)
    msgs = get_messages(1, 5)
    found_exec = False
    exec_ok = False
    for m in msgs:
        content = m.get("content", "")
        if "execute_code" in content or "41" in content:
            found_exec = True
            if "41" in content:
                exec_ok = True
                test("execute_code 执行", True, f"返回 {content[:80]}")
                break
    if found_exec and not exec_ok:
        test("execute_code 执行", False, "Agent 调用了但结果不对")
    elif not found_exec:
        test("execute_code 执行", False, "Agent 未调用 execute_code")
except Exception as e:
    test("execute_code 工具", False, str(e))

# 8. retrieve_memory 工具
print("\n--- 5. retrieve_memory 工具 ---")
try:
    msg = post_message(1, "请回忆一下之前的对话")
    test("发送 retrieve_memory 请求", True, f"msg id={msg['id']}")
    time.sleep(12)
    msgs = get_messages(1, 5)
    found_mem = False
    mem_ok = False
    for m in msgs:
        content = m.get("content", "")
        if "retrieve_memory" in content or "memory" in content.lower() or "Memory" in content:
            found_mem = True
            if "Error" not in content or ("agent_name" not in content and "attribute" not in content.lower()):
                mem_ok = True
                test("retrieve_memory 执行", True, f"返回 {content[:100]}")
                break
    if found_mem and not mem_ok:
        test("retrieve_memory 执行", False, "执行出现属性错误")
    elif not found_mem:
        test("retrieve_memory 执行", False, "Agent 未调用 retrieve_memory")
except Exception as e:
    test("retrieve_memory 工具", False, str(e))

# 9. 前端
print("\n--- 6. 前端 ---")
try:
    req = urllib.request.Request("http://localhost:3001/")
    resp = urllib.request.urlopen(req, timeout=10)
    html = resp.read().decode("utf-8")
    test("GET / (前端)", resp.status == 200 and "Catown" in html, f"HTTP {resp.status}")
except Exception as e:
    test("GET / (前端)", False, str(e))

# Summary
print("\n=== 测试汇总 ===")
passed = sum(1 for r in results if r["ok"])
total = len(results)
for r in results:
    status = "[OK]" if r["ok"] else "[X]"
    print(f"  {status} {r['name']}" + (f" => {r['detail']}" if r["detail"] else ""))
print(f"\n通过率: {passed}/{total} ({passed/total*100:.0f}%)")

"""
Catown API 集成测试

需要后端运行在 localhost:8000
运行: python3 tests/test_api_integration.py
"""
import urllib.request
import json
import sys

API = "http://localhost:8000/api"
passed = 0
failed = 0
results = []


def test(name, ok, detail=""):
    global passed, failed
    status = "✅" if ok else "❌"
    if ok:
        passed += 1
    else:
        failed += 1
    msg = f"  {status} {name}" + (f" → {detail}" if detail else "")
    results.append(msg)
    print(msg)


def api_get(path, timeout=10):
    r = urllib.request.urlopen(f"{API}{path}", timeout=timeout)
    return r.status, json.loads(r.read())


def api_post(path, data, timeout=30):
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        f"{API}{path}", data=body,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    r = urllib.request.urlopen(req, timeout=timeout)
    return r.status, json.loads(r.read())


def run():
    print("=" * 50)
    print("Catown API Integration Tests")
    print("=" * 50)

    # 1. Health check
    print("\n--- Health ---")
    try:
        st, body = api_get("/status")
        test("GET /api/status", st == 200, f"version={body.get('version')}")
    except Exception as e:
        test("GET /api/status", False, str(e))

    # 2. Agents
    print("\n--- Agents ---")
    try:
        st, agents = api_get("/agents")
        test("GET /api/agents", st == 200, f"{len(agents)} agents")
        names = [a["name"] for a in agents]
        test("Has assistant", "assistant" in names, names)
        test("Has coder", "coder" in names)
        test("Has researcher", "researcher" in names)
        test("Has reviewer", "reviewer" in names)
    except Exception as e:
        test("GET /api/agents", False, str(e))

    # 3. Tools
    print("\n--- Tools ---")
    try:
        st, body = api_get("/tools")
        test("GET /api/tools", st == 200, f"{body['count']} tools")
        tool_names = [t["name"] for t in body["tools"]]
        for t in ["web_search", "execute_code", "save_memory", "delegate_task"]:
            test(f"Tool {t}", t in tool_names)
    except Exception as e:
        test("GET /api/tools", False, str(e))

    # 4. Config
    print("\n--- Config ---")
    try:
        st, body = api_get("/config")
        test("GET /api/config", st == 200, f"model={body.get('llm', {}).get('model')}")
    except Exception as e:
        test("GET /api/config", False, str(e))

    # 5. Projects CRUD
    print("\n--- Projects ---")
    try:
        st, projects_before = api_get("/projects")
        test("GET /api/projects", st == 200, f"{len(projects_before)} projects")

        st, project = api_post("/projects", {
            "name": "Integration Test Room",
            "description": "Auto-created by integration test",
            "agent_names": ["assistant", "coder"]
        })
        test("POST /api/projects", st == 200, f"id={project['id']}, chatroom={project.get('chatroom_id')}")
        project_id = project["id"]
        chatroom_id = project.get("chatroom_id")

        st, detail = api_get(f"/projects/{project_id}")
        test("GET /api/projects/:id", st == 200)

        # Send a message
        if chatroom_id:
            st, msg = api_post(f"/chatrooms/{chatroom_id}/messages", {"content": "Hello, what is 2+3?"})
            test("POST /api/chatrooms/:id/messages", st == 200, f"msg_id={msg['id']}")

            import time
            time.sleep(5)

            st, msgs = api_get(f"/chatrooms/{chatroom_id}/messages?limit=5")
            test("GET messages after response", st == 200, f"{len(msgs)} messages")
            has_agent_response = any(m.get("agent_name") for m in msgs)
            test("Agent responded", has_agent_response)

        # Cleanup
        st, _ = api_get(f"/projects/{project_id}")
        if st == 200:
            req = urllib.request.Request(f"{API}/projects/{project_id}", method="DELETE")
            urllib.request.urlopen(req, timeout=10)
            test("DELETE /api/projects/:id", True, "cleaned up")

    except Exception as e:
        test("Projects flow", False, str(e))

    # 6. Collaboration
    print("\n--- Collaboration ---")
    try:
        st, body = api_get("/collaboration/status")
        test("GET /api/collaboration/status", st == 200, f"collaborators={body.get('active_collaborators')}")
    except Exception as e:
        test("GET /api/collaboration/status", False, str(e))

    # Summary
    print("\n" + "=" * 50)
    print(f"Results: {passed} passed, {failed} failed, {passed + failed} total")
    print("=" * 50)
    return failed == 0


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)

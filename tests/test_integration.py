import urllib.request
import urllib.parse
import json
import time

API = "http://localhost:8000/api"

def post_message(chatroom_id, content):
    data = json.dumps({"content": content}).encode("utf-8")
    req = urllib.request.Request(
        f"{API}/chatrooms/{chatroom_id}/messages",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    resp = urllib.request.urlopen(req, timeout=10)
    return json.loads(resp.read())

def get_messages(chatroom_id, limit=5):
    resp = urllib.request.urlopen(f"{API}/chatrooms/{chatroom_id}/messages?limit={limit}", timeout=10)
    return json.loads(resp.read())

print("=== Test: Send message via agent ===")
msg = post_message(1, "@coder 请执行代码 2*3+5*7 并返回结果")
print(f"Sent: id={msg['id']}")
time.sleep(8)
messages = get_messages(1, 3)
for m in messages:
    print(f"  [{m['agent_name'] or 'user'}] {m['content'][:150]}")

print("\n=== Test: Web search tool ===")
msg2 = post_message(1, "@assistant 请搜索 Python 是什么")
print(f"Sent: id={msg2['id']}")
time.sleep(10)
messages2 = get_messages(1, 4)
print(f"Got {len(messages2)} messages")
for m in messages2:
    preview = m['content'][:100].replace('\n', ' ')
    print(f"  [{m['agent_name'] or 'user'}] {preview}")

print("\n=== Test: retrieve_memory tool ===")
msg3 = post_message(1, "@assistant 请回忆一下之前的对话内容")
print(f"Sent: id={msg3['id']}")
time.sleep(8)
messages3 = get_messages(1, 3)
print(f"Got {len(messages3)} messages")
for m in messages3:
    preview = m['content'][:100].replace('\n', ' ')
    print(f"  [{m['agent_name'] or 'user'}] {preview}")

print("\nAll tests done!")

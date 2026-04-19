"""
示例脚本 - 演示如何创建项目和使用 Agent
"""
import asyncio
import requests
import time


BASE_URL = "http://localhost:8000/api"


def print_section(title: str):
    """打印分隔线"""
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def list_agents():
    """获取可用 Agent 列表"""
    print_section("Available Agents")
    
    response = requests.get(f"{BASE_URL}/agents")
    agents = response.json()
    
    for agent in agents:
        print(f"✓ {agent['name']} - {agent['role']}")
    
    return agents


def create_demo_project():
    """创建演示项目"""
    print_section("Creating Demo Project")
    
    project_data = {
        "name": "Demo Project - Code Review",
        "description": "A demonstration project where coder and reviewer agents collaborate",
        "agent_names": ["coder", "reviewer"]
    }
    
    response = requests.post(f"{BASE_URL}/projects", json=project_data)
    project = response.json()
    
    print(f"✓ Created project: {project['name']}")
    print(f"  ID: {project['id']}")
    print(f"  Chatroom: {project['chatroom_id']}")
    print(f"  Agents: {', [a['name'] for a in project['agents']]}")
    
    return project


def send_message_to_chatroom(chatroom_id: int, message: str):
    """发送消息到聊天室"""
    print(f"\n📤 Sending message: {message}")
    
    response = requests.post(
        f"{BASE_URL}/chatrooms/{chatroom_id}/messages",
        json={"content": message}
    )
    
    result = response.json()
    print(f"✓ Message sent (ID: {result['id']})")
    
    return result


def get_chatroom_messages(chatroom_id: int, limit: int = 10):
    """获取聊天室消息"""
    print(f"\n📥 Fetching messages from chatroom {chatroom_id}")
    
    response = requests.get(f"{BASE_URL}/chatrooms/{chatroom_id}/messages?limit={limit}")
    messages = response.json()
    
    print(f"Found {len(messages)} messages:\n")
    
    for msg in messages:
        sender = msg['agent_name'] or "User"
        print(f"  [{sender}]: {msg['content'][:100]}...")
    
    return messages


def get_system_status():
    """获取系统状态"""
    print_section("System Status")
    
    response = requests.get(f"{BASE_URL}/status")
    status = response.json()
    
    print(f"Status: {status['status']}")
    print(f"Version: {status['version']}")
    print(f"\nStatistics:")
    print(f"  Agents: {status['stats']['agents']}")
    print(f"  Projects: {status['stats']['projects']}")
    print(f"  Chatrooms: {status['stats']['chatrooms']}")
    print(f"  Messages: {status['stats']['messages']}")
    
    return status


def demo_workflow():
    """演示完整工作流"""
    print_section("Demo Workflow")
    
    # 1. 检查系统状态
    get_system_status()
    
    # 2. 列出可用 Agent
    agents = list_agents()
    
    # 3. 创建项目
    project = create_demo_project()
    
    # 4. 发送消息
    if project['chatroom_id']:
        time.sleep(1)  # 等待聊天室初始化
        
        messages = [
            "Hello! I need help writing a Python function to sort a list.",
            "Can you provide an example with explanation?",
            "Please review the code for best practices."
        ]
        
        for msg in messages:
            send_message_to_chatroom(project['chatroom_id'], msg)
            time.sleep(0.5)
        
        # 5. 查看消息历史
        time.sleep(1)
        get_chatroom_messages(project['chatroom_id'])


def interactive_demo():
    """交互式演示"""
    print_section("Interactive Demo")
    print("Type 'quit' to exit\n")
    
    # 获取最新项目
    response = requests.get(f"{BASE_URL}/projects")
    projects = response.json()
    
    if not projects:
        print("No projects found. Creating one...")
        projects = [create_demo_project()]
    
    project = projects[0]
    chatroom_id = project['chatroom_id']
    
    print(f"Using project: {project['name']}")
    print(f"Chatroom ID: {chatroom_id}\n")
    
    while True:
        user_input = input("You: ").strip()
        
        if user_input.lower() in ['quit', 'exit', 'q']:
            print("Goodbye!")
            break
        
        if user_input:
            send_message_to_chatroom(chatroom_id, user_input)
            time.sleep(1)
            
            # 显示最新消息
            messages = get_chatroom_messages(chatroom_id, limit=3)


if __name__ == "__main__":
    print("\n🐱 Catown Demo Script")
    print("Make sure the backend server is running on http://localhost:8000\n")
    
    try:
        # 运行演示工作流
        demo_workflow()
        
        print_section("Demo Complete!")
        print("You can now:")
        print("1. Visit http://localhost:8000 to use the Web interface")
        print("2. Visit http://localhost:8000/docs to view API documentation")
        print("3. Run this script with interactive mode: python examples/demo.py --interactive")
        
    except requests.exceptions.ConnectionError:
        print("❌ Error: Cannot connect to backend server")
        print("   Please start the backend server first:")
        print("   cd backend && uvicorn main:app --reload")
    except Exception as e:
        print(f"❌ Error: {e}")

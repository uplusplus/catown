"""Demo script for creating a project and sending chatroom messages."""

import time

import requests


BASE_URL = "http://localhost:8000/api"
REQUEST_HEADERS = {"X-Catown-Client": "example"}


def print_section(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}\n")


def list_agents():
    print_section("Available Agents")

    response = requests.get(f"{BASE_URL}/agents", headers=REQUEST_HEADERS)
    response.raise_for_status()
    agents = response.json()

    for agent in agents:
        print(f"- {agent['name']} - {agent['role']}")

    return agents


def create_demo_project():
    print_section("Creating Demo Project")

    project_data = {
        "name": "Demo Project - Code Review",
        "description": "A demonstration project where coder and reviewer agents collaborate",
        "agent_names": ["coder", "reviewer"],
    }

    response = requests.post(f"{BASE_URL}/projects", json=project_data, headers=REQUEST_HEADERS)
    response.raise_for_status()
    project = response.json()

    agent_names = ", ".join(a["name"] for a in project["agents"])
    print(f"- Created project: {project['name']}")
    print(f"  ID: {project['id']}")
    print(f"  Chatroom: {project['chatroom_id']}")
    print(f"  Agents: {agent_names}")

    return project


def send_message_to_chatroom(chatroom_id: int, message: str):
    print(f"\nSending message: {message}")

    response = requests.post(
        f"{BASE_URL}/chatrooms/{chatroom_id}/messages",
        json={"content": message},
        headers=REQUEST_HEADERS,
    )
    response.raise_for_status()

    result = response.json()
    print(f"- Message sent (ID: {result['id']})")

    return result


def get_chatroom_messages(chatroom_id: int, limit: int = 10):
    print(f"\nFetching messages from chatroom {chatroom_id}")

    response = requests.get(
        f"{BASE_URL}/chatrooms/{chatroom_id}/messages?limit={limit}",
        headers=REQUEST_HEADERS,
    )
    response.raise_for_status()
    messages = response.json()

    print(f"Found {len(messages)} messages:\n")

    for message in messages:
        sender = message["agent_name"] or "User"
        print(f"  [{sender}]: {message['content'][:100]}...")

    return messages


def get_system_status():
    print_section("System Status")

    response = requests.get(f"{BASE_URL}/status", headers=REQUEST_HEADERS)
    response.raise_for_status()
    status = response.json()

    print(f"Status: {status['status']}")
    print(f"Version: {status['version']}")
    print("\nStatistics:")
    print(f"  Agents: {status['stats']['agents']}")
    print(f"  Projects: {status['stats']['projects']}")
    print(f"  Chatrooms: {status['stats']['chatrooms']}")
    print(f"  Messages: {status['stats']['messages']}")

    return status


def demo_workflow() -> None:
    print_section("Demo Workflow")

    get_system_status()
    list_agents()
    project = create_demo_project()

    if project["chatroom_id"]:
        time.sleep(1)
        messages = [
            "Hello! I need help writing a Python function to sort a list.",
            "Can you provide an example with explanation?",
            "Please review the code for best practices.",
        ]

        for message in messages:
            send_message_to_chatroom(project["chatroom_id"], message)
            time.sleep(0.5)

        time.sleep(1)
        get_chatroom_messages(project["chatroom_id"])


def interactive_demo() -> None:
    print_section("Interactive Demo")
    print("Type 'quit' to exit\n")

    response = requests.get(f"{BASE_URL}/projects", headers=REQUEST_HEADERS)
    response.raise_for_status()
    projects = response.json()

    if not projects:
        print("No projects found. Creating one...")
        projects = [create_demo_project()]

    project = projects[0]
    chatroom_id = project["chatroom_id"]

    print(f"Using project: {project['name']}")
    print(f"Chatroom ID: {chatroom_id}\n")

    while True:
        user_input = input("You: ").strip()

        if user_input.lower() in ["quit", "exit", "q"]:
            print("Goodbye!")
            break

        if user_input:
            send_message_to_chatroom(chatroom_id, user_input)
            time.sleep(1)
            get_chatroom_messages(chatroom_id, limit=3)


if __name__ == "__main__":
    print("\nCatown Demo Script")
    print("Make sure the backend server is running on http://localhost:8000\n")

    try:
        demo_workflow()

        print_section("Demo Complete!")
        print("You can now:")
        print("1. Visit http://localhost:8000 to use the web interface")
        print("2. Visit http://localhost:8000/docs to view API documentation")
        print("3. Import interactive_demo() from this script to run interactively")
    except requests.exceptions.ConnectionError:
        print("Error: Cannot connect to backend server")
        print("Please start the backend server first:")
        print("  cd backend && uvicorn main:app --reload")
    except Exception as exc:
        print(f"Error: {exc}")

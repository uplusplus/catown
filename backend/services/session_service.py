# -*- coding: utf-8 -*-
"""Session and project creation flows for standalone chats and hidden project chats."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from agents.identity import DEFAULT_AGENT_TYPE, default_agent_name, legacy_default_agent_names, normalize_agent_type
from chatrooms.manager import ChatroomInstance, chatroom_manager
from config import settings
from models.database import Agent, AgentAssignment, Chatroom, Message, Project


class SessionService:
    """Encapsulate chat/project creation rules from the business flow doc."""

    SEED_MESSAGE_LIMIT = 20

    def __init__(self, db: Session):
        self.db = db

    def list_visible_chats(self) -> list[Chatroom]:
        return (
            self.db.query(Chatroom)
            .filter(
                Chatroom.session_type == "standalone",
                Chatroom.is_visible_in_chat_list == True,
            )
            .order_by(Chatroom.created_at.desc(), Chatroom.id.desc())
            .all()
        )

    def get_or_create_default_standalone_chat(self, title: str | None = None) -> Chatroom:
        chatroom = (
            self.db.query(Chatroom)
            .filter(
                Chatroom.session_type == "standalone",
                Chatroom.is_visible_in_chat_list == True,
            )
            .order_by(Chatroom.created_at.asc(), Chatroom.id.asc())
            .first()
        )
        return chatroom or self.create_standalone_chat(title=title)

    def _next_project_display_order(self) -> int:
        latest = self.db.query(Project).order_by(Project.display_order.desc(), Project.id.desc()).first()
        if not latest:
            return 0
        return int(latest.display_order or 0) + 1

    def create_standalone_chat(self, title: str | None = None) -> Chatroom:
        chatroom = Chatroom(
            project_id=None,
            title=(title or "").strip() or "New Chat",
            session_type="standalone",
            is_visible_in_chat_list=True,
        )
        self.db.add(chatroom)
        self.db.commit()
        self.db.refresh(chatroom)
        self._register_chatroom_instance(chatroom, project_name=chatroom.title or "Standalone Chat")
        return chatroom

    def get_or_create_self_bootstrap_project(self) -> tuple[Project, Chatroom, list[Agent]]:
        workspace_path = str(self._self_workspace_root())
        project = (
            self.db.query(Project)
            .filter(Project.workspace_path == workspace_path)
            .order_by(Project.id.asc())
            .first()
        )
        if project:
            project_chat = self.get_project_chat(project.id)
            self._register_chatroom_instance(project_chat, project_name=project.name or "Catown")
            agents = self._project_agents(project.id)
            return project, project_chat, agents

        agent_names = self._default_self_project_agent_names()
        project_name = self._self_workspace_root().name or "catown"
        return self.create_project_directly(
            name=project_name,
            description="Self-bootstrap workspace bound to the current Catown repository.",
            agent_names=agent_names,
            workspace_path=workspace_path,
        )

    def create_hidden_project_chat(
        self,
        project_id: int,
        title: str,
        source_chatroom_id: int | None = None,
    ) -> Chatroom:
        chatroom = Chatroom(
            project_id=project_id,
            title=title.strip() or "Project Chat",
            session_type="project-bound",
            is_visible_in_chat_list=False,
            source_chatroom_id=source_chatroom_id,
        )
        self.db.add(chatroom)
        self.db.flush()
        self._register_chatroom_instance(chatroom, project_name=chatroom.title or "Project Chat")
        return chatroom

    def create_project_subchat(self, project_id: int, title: str | None = None) -> Chatroom:
        project = self.db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        project_chat = self.get_project_chat(project_id)
        existing_count = (
            self.db.query(Chatroom)
            .filter(
                Chatroom.source_chatroom_id == project_chat.id,
                Chatroom.is_visible_in_chat_list == True,
            )
            .count()
        )
        chatroom = Chatroom(
            project_id=None,
            title=(title or "").strip() or f"{project.name} Chat {existing_count + 1}",
            session_type="standalone",
            is_visible_in_chat_list=True,
            source_chatroom_id=project_chat.id,
        )
        self.db.add(chatroom)
        self.db.commit()
        self.db.refresh(chatroom)
        self._register_chatroom_instance(chatroom, project_name=project.name or chatroom.title or "Project Chat")
        return chatroom

    def get_chatroom_or_404(self, chatroom_id: int) -> Chatroom:
        chatroom = self.db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()
        if not chatroom:
            raise HTTPException(status_code=404, detail="Chatroom not found")
        return chatroom

    def get_project_chat(self, project_id: int) -> Chatroom:
        project_chat = (
            self.db.query(Chatroom)
            .filter(Chatroom.project_id == project_id, Chatroom.session_type == "project-bound")
            .order_by(Chatroom.id.asc())
            .first()
        )
        if not project_chat:
            raise HTTPException(status_code=404, detail="Project chat not found")
        return project_chat

    def create_project_directly(
        self,
        name: str,
        description: str,
        agent_names: list[str],
        workspace_path: str | None = None,
    ) -> tuple[Project, Chatroom, list[Agent]]:
        project = Project(
            name=name,
            description=description,
            display_order=self._next_project_display_order(),
        )
        self.db.add(project)
        self.db.flush()
        project.workspace_path = self._resolve_workspace_path(project, workspace_path)

        project_chat = self.create_hidden_project_chat(
            project_id=project.id,
            title=name,
            source_chatroom_id=None,
        )
        project.default_chatroom_id = project_chat.id

        assigned_agents = self._assign_agents(project.id, project_chat.id, agent_names)

        self.db.add(project)
        self.db.commit()
        self.db.refresh(project)
        self.db.refresh(project_chat)
        return project, project_chat, assigned_agents

    def create_project_from_github(
        self,
        repo_url: str,
        description: str,
        agent_names: list[str],
        name: str | None = None,
        ref: str | None = None,
    ) -> tuple[Project, Chatroom, list[Agent]]:
        repo_meta = self._normalize_github_repo(repo_url)
        normalized_ref = (ref or "").strip() or None

        existing_project = self._find_existing_github_project(repo_meta["full_name"], normalized_ref)
        if existing_project:
            project_chat = self.get_project_chat(existing_project.id)
            self._register_chatroom_instance(project_chat, project_name=existing_project.name or repo_meta["repo_name"])
            return existing_project, project_chat, self._project_agents(existing_project.id)

        project_name = (name or "").strip() or repo_meta["repo_name"]
        workspace_path = self._allocate_managed_workspace_path(repo_meta["workspace_label"])
        self._clone_github_repository(repo_meta["clone_url"], workspace_path, ref=normalized_ref)

        try:
            project, project_chat, assigned_agents = self.create_project_directly(
                name=project_name,
                description=description.strip() or f"Imported from GitHub repository {repo_meta['full_name']}.",
                agent_names=agent_names,
                workspace_path=str(workspace_path),
            )
            project.source_type = "github"
            project.repo_url = repo_meta["display_url"]
            project.repo_full_name = repo_meta["full_name"]
            project.clone_ref = normalized_ref
            self.db.add(project)
            self.db.commit()
            self.db.refresh(project)
            return project, project_chat, assigned_agents
        except Exception:
            shutil.rmtree(workspace_path, ignore_errors=True)
            raise

    def sync_github_project(self, project_id: int) -> tuple[Project, dict[str, str | bool | None]]:
        project = self.db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        if project.source_type != "github":
            raise HTTPException(status_code=400, detail="Only GitHub-backed projects can be synced")
        if not project.workspace_path:
            raise HTTPException(status_code=400, detail="Project workspace is not configured")

        workspace_path = Path(project.workspace_path).expanduser().resolve()
        if not workspace_path.exists():
            raise HTTPException(status_code=400, detail="Project workspace does not exist")

        before_status = self._read_git_workspace_status(workspace_path)
        self._sync_git_workspace(workspace_path, ref=(project.clone_ref or None))
        after_status = self._read_git_workspace_status(workspace_path)

        updated = before_status["head_commit"] != after_status["head_commit"]
        branch_label = after_status["branch"] or before_status["branch"] or (project.clone_ref or "detached")
        if after_status["detached"]:
            summary = (
                f"Refreshed {project.repo_full_name or project.name} at {after_status['head_short']}"
                if updated
                else f"{project.repo_full_name or project.name} already at {after_status['head_short']}"
            )
        else:
            summary = (
                f"Pulled latest changes for {project.repo_full_name or project.name} on {branch_label}"
                if updated
                else f"{project.repo_full_name or project.name} is already up to date on {branch_label}"
            )

        return project, {
            "updated": updated,
            "branch": after_status["branch"],
            "head_commit": after_status["head_commit"],
            "head_short": after_status["head_short"],
            "previous_head_commit": before_status["head_commit"],
            "detached": after_status["detached"],
            "summary": summary,
        }

    def create_project_from_chat(
        self,
        source_chatroom_id: int,
        name: str,
        description: str,
        agent_names: list[str],
        workspace_path: str | None = None,
    ) -> tuple[Project, Chatroom, list[Agent]]:
        source_chatroom = self.get_chatroom_or_404(source_chatroom_id)
        if source_chatroom.session_type != "standalone":
            raise HTTPException(status_code=400, detail="Only standalone chats can be converted into projects")

        project = Project(
            name=name,
            description=description,
            display_order=self._next_project_display_order(),
        )
        self.db.add(project)
        self.db.flush()
        project.workspace_path = self._resolve_workspace_path(project, workspace_path)

        project_chat = self.create_hidden_project_chat(
            project_id=project.id,
            title=name,
            source_chatroom_id=source_chatroom.id,
        )
        project.default_chatroom_id = project_chat.id
        self.copy_chat_context(source_chatroom.id, project_chat.id)

        assigned_agents = self._assign_agents(project.id, project_chat.id, agent_names)

        self.db.add(project)
        self.db.commit()
        self.db.refresh(project)
        self.db.refresh(project_chat)
        return project, project_chat, assigned_agents

    def copy_chat_context(self, source_chatroom_id: int, target_chatroom_id: int) -> None:
        source_messages = (
            self.db.query(Message)
            .filter(Message.chatroom_id == source_chatroom_id)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(self.SEED_MESSAGE_LIMIT)
            .all()
        )
        source_messages = list(reversed(source_messages))

        self.db.add(
            Message(
                chatroom_id=target_chatroom_id,
                agent_id=None,
                content=(
                    f"This project was created from standalone chat #{source_chatroom_id}. "
                    "The following messages were copied as seed context."
                ),
                message_type="system",
                metadata_json="{}",
            )
        )
        for source_message in source_messages:
            self.db.add(
                Message(
                    chatroom_id=target_chatroom_id,
                    agent_id=source_message.agent_id,
                    content=source_message.content,
                    message_type=source_message.message_type,
                    metadata_json=source_message.metadata_json,
                    created_at=source_message.created_at,
                )
            )
        self.db.flush()

    def _assign_agents(self, project_id: int, chatroom_id: int, agent_names: list[str]) -> list[Agent]:
        assigned_agents: list[Agent] = []
        project_chat = chatroom_manager.get_chatroom(chatroom_id)

        for agent_name in agent_names:
            agent_type = normalize_agent_type(agent_name)
            candidate_names = {agent_type, default_agent_name(agent_type)}
            candidate_names.update({value.title() for value in legacy_default_agent_names(agent_type)})
            candidate_names.update(legacy_default_agent_names(agent_type))
            agent = (
                self.db.query(Agent)
                .filter(
                    or_(
                        Agent.agent_type == agent_type,
                        Agent.name.in_(sorted(candidate_names)),
                    )
                )
                .order_by(Agent.id.asc())
                .first()
            )
            if not agent:
                continue

            self.db.add(AgentAssignment(project_id=project_id, agent_id=agent.id))
            assigned_agents.append(agent)

            if project_chat:
                project_chat.add_agent(agent.id)

        self.db.flush()
        return assigned_agents

    def _project_agents(self, project_id: int) -> list[Agent]:
        assignments = self.db.query(AgentAssignment).filter(AgentAssignment.project_id == project_id).all()
        agent_ids = [assignment.agent_id for assignment in assignments]
        if not agent_ids:
            return []
        return self.db.query(Agent).filter(Agent.id.in_(agent_ids)).order_by(Agent.id.asc()).all()

    def _register_chatroom_instance(self, chatroom: Chatroom, project_name: str) -> None:
        if chatroom.id in chatroom_manager.chatrooms:
            return
        chatroom_manager.chatrooms[chatroom.id] = ChatroomInstance(
            id=chatroom.id,
            project_id=chatroom.project_id,
            project_name=project_name,
        )

    def _resolve_workspace_path(self, project: Project, workspace_path: str | None) -> str:
        if workspace_path:
            resolved = Path(workspace_path).expanduser()
            resolved.mkdir(parents=True, exist_ok=True)
            return str(resolved)

        workspace_root = Path(settings.PROJECTS_ROOT)
        try:
            workspace_root.mkdir(parents=True, exist_ok=True)
        except OSError:
            workspace_root = Path(tempfile.gettempdir()) / "catown-projects"
            workspace_root.mkdir(parents=True, exist_ok=True)

        safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "-", (project.name or "project").strip()).strip("-") or "project"
        workspace_dir = workspace_root / f"{project.id}-{safe_name}"
        workspace_dir.mkdir(parents=True, exist_ok=True)
        return str(workspace_dir)

    def _managed_workspace_root(self) -> Path:
        workspace_root = Path(settings.PROJECTS_ROOT)
        try:
            workspace_root.mkdir(parents=True, exist_ok=True)
        except OSError:
            workspace_root = Path(tempfile.gettempdir()) / "catown-projects"
            workspace_root.mkdir(parents=True, exist_ok=True)
        return workspace_root

    def _allocate_managed_workspace_path(self, label: str) -> Path:
        safe_label = re.sub(r"[^a-zA-Z0-9._-]+", "-", label.strip()).strip("-") or "project"
        workspace_root = self._managed_workspace_root()
        candidate = workspace_root / safe_label
        suffix = 2
        while candidate.exists():
            candidate = workspace_root / f"{safe_label}-{suffix}"
            suffix += 1
        return candidate

    def _normalize_github_repo(self, repo_url: str) -> dict[str, str]:
        raw = (repo_url or "").strip()
        if not raw:
            raise HTTPException(status_code=400, detail="GitHub repository is required")

        patterns = (
            re.compile(r"^https?://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$", re.IGNORECASE),
            re.compile(r"^git@github\.com:(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?$", re.IGNORECASE),
            re.compile(r"^(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?$", re.IGNORECASE),
        )
        match = next((pattern.match(raw) for pattern in patterns if pattern.match(raw)), None)
        if not match:
            raise HTTPException(
                status_code=400,
                detail="Only GitHub repositories are supported. Use owner/repo or a github.com URL.",
            )

        owner = match.group("owner")
        repo = match.group("repo")
        full_name = f"{owner}/{repo}"
        return {
            "owner": owner,
            "repo_name": repo,
            "full_name": full_name,
            "display_url": f"https://github.com/{full_name}",
            "clone_url": raw if raw.startswith("git@github.com:") else f"https://github.com/{full_name}.git",
            "workspace_label": full_name.replace("/", "-"),
        }

    def _find_existing_github_project(self, repo_full_name: str, ref: str | None) -> Project | None:
        candidates = (
            self.db.query(Project)
            .filter(Project.source_type == "github", Project.repo_full_name == repo_full_name)
            .order_by(Project.id.asc())
            .all()
        )
        wanted_ref = (ref or "").strip()
        for candidate in candidates:
            if (candidate.clone_ref or "").strip() != wanted_ref:
                continue
            if candidate.workspace_path and Path(candidate.workspace_path).expanduser().exists():
                return candidate
        return None

    def _git_binary(self) -> str:
        git_bin = shutil.which("git")
        if not git_bin:
            raise HTTPException(status_code=500, detail="git is required for GitHub project operations")
        return git_bin

    def _git_command_prefix(self) -> list[str]:
        command = [self._git_binary()]
        token = os.getenv("GITHUB_TOKEN", "").strip()
        if token:
            command.extend(["-c", f"http.extraHeader=Authorization: Bearer {token}"])
        return command

    def _run_git_command(self, workspace: Path, *args: str, timeout: int = 60) -> str:
        env = os.environ.copy()
        env.setdefault("GIT_TERMINAL_PROMPT", "0")
        result = subprocess.run(
            [*self._git_command_prefix(), "-C", str(workspace), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        if result.returncode != 0:
            error_text = (result.stderr or result.stdout or "Unknown git error").strip()
            raise HTTPException(status_code=400, detail=f"Git command failed ({' '.join(args)}): {error_text[:300]}")
        return (result.stdout or "").strip()

    def _git_ref_exists(self, workspace: Path, ref: str, env: dict[str, str]) -> bool:
        result = subprocess.run(
            [*self._git_command_prefix(), "-C", str(workspace), "rev-parse", "--verify", "--quiet", ref],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        return result.returncode == 0

    def _resolve_checkout_target(self, workspace: Path, ref: str, env: dict[str, str]) -> tuple[str, str]:
        normalized_ref = (ref or "").strip()
        if not normalized_ref:
            raise HTTPException(status_code=400, detail="Git ref cannot be empty")

        branch_candidates = [
            f"refs/remotes/origin/{normalized_ref}",
        ]
        if normalized_ref.startswith("origin/"):
            branch_candidates.insert(0, f"refs/remotes/{normalized_ref}")
        if normalized_ref.startswith("refs/remotes/"):
            branch_candidates.insert(0, normalized_ref)
        if normalized_ref.startswith("refs/heads/"):
            branch_candidates.insert(0, normalized_ref.replace("refs/heads/", "refs/remotes/origin/", 1))

        for candidate in branch_candidates:
            if self._git_ref_exists(workspace, candidate, env):
                local_branch_name = normalized_ref
                if local_branch_name.startswith("refs/heads/"):
                    local_branch_name = local_branch_name[len("refs/heads/") :]
                elif local_branch_name.startswith("refs/remotes/origin/"):
                    local_branch_name = local_branch_name[len("refs/remotes/origin/") :]
                elif local_branch_name.startswith("origin/"):
                    local_branch_name = local_branch_name[len("origin/") :]
                return "branch", f"{local_branch_name}|{candidate}"

        tag_candidates = [normalized_ref]
        if not normalized_ref.startswith("refs/tags/"):
            tag_candidates.insert(0, f"refs/tags/{normalized_ref}")
        for candidate in tag_candidates:
            if self._git_ref_exists(workspace, candidate, env):
                return "tag", candidate

        if self._git_ref_exists(workspace, f"{normalized_ref}^{{commit}}", env):
            return "commit", normalized_ref

        raise HTTPException(status_code=400, detail=f"Git ref '{normalized_ref}' was not found in the repository")

    def _checkout_git_ref(self, workspace: Path, ref: str, env: dict[str, str]) -> bool:
        target_kind, target_value = self._resolve_checkout_target(workspace, ref, env)
        git_prefix = self._git_command_prefix()

        if target_kind == "branch":
            local_branch_name, remote_ref = target_value.split("|", 1)
            checkout_command = [*git_prefix, "-C", str(workspace), "checkout", "-B", local_branch_name, remote_ref]
        else:
            checkout_command = [*git_prefix, "-C", str(workspace), "checkout", "--detach", target_value]

        checkout_result = subprocess.run(
            checkout_command,
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )
        if checkout_result.returncode != 0:
            error_text = (checkout_result.stderr or checkout_result.stdout or "Unknown git checkout error").strip()
            raise HTTPException(status_code=400, detail=f"Failed to checkout Git ref '{ref}': {error_text[:300]}")

        if target_kind != "branch":
            return False

        local_branch_name, remote_ref = target_value.split("|", 1)
        upstream_result = subprocess.run(
            [*git_prefix, "-C", str(workspace), "branch", "--set-upstream-to", remote_ref, local_branch_name],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        if upstream_result.returncode != 0:
            error_text = (upstream_result.stderr or upstream_result.stdout or "Unknown git upstream error").strip()
            raise HTTPException(status_code=400, detail=f"Failed to configure tracking branch for '{ref}': {error_text[:300]}")

        return True

    def _read_git_workspace_status(self, workspace: Path) -> dict[str, str | bool]:
        if not (workspace / ".git").exists():
            raise HTTPException(status_code=400, detail="Project workspace is not a git repository")

        head_commit = self._run_git_command(workspace, "rev-parse", "HEAD")
        branch = self._run_git_command(workspace, "rev-parse", "--abbrev-ref", "HEAD")
        detached = branch == "HEAD"
        if detached:
            branch = ""

        return {
            "branch": branch,
            "head_commit": head_commit,
            "head_short": head_commit[:8],
            "detached": detached,
        }

    def _sync_git_workspace(self, workspace: Path, ref: str | None = None) -> None:
        env = os.environ.copy()
        env.setdefault("GIT_TERMINAL_PROMPT", "0")

        fetch_result = subprocess.run(
            [*self._git_command_prefix(), "-C", str(workspace), "fetch", "--all", "--tags", "--prune"],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
        if fetch_result.returncode != 0:
            error_text = (fetch_result.stderr or fetch_result.stdout or "Unknown git fetch error").strip()
            raise HTTPException(status_code=400, detail=f"Git command failed (fetch): {error_text[:300]}")

        if ref:
            checked_out_branch = self._checkout_git_ref(workspace, ref, env)
            if checked_out_branch:
                self._run_git_command(workspace, "pull", "--ff-only", timeout=120)
            return

        branch = self._run_git_command(workspace, "rev-parse", "--abbrev-ref", "HEAD")
        if branch != "HEAD":
            self._run_git_command(workspace, "pull", "--ff-only", timeout=120)
            return

    def _clone_github_repository(self, clone_url: str, destination: Path, ref: str | None = None) -> None:
        git_prefix = self._git_command_prefix()

        destination = destination.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env.setdefault("GIT_TERMINAL_PROMPT", "0")

        clone_result = subprocess.run(
            [*git_prefix, "clone", clone_url, str(destination)],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
        if clone_result.returncode != 0:
            shutil.rmtree(destination, ignore_errors=True)
            error_text = (clone_result.stderr or clone_result.stdout or "Unknown git clone error").strip()
            raise HTTPException(status_code=400, detail=f"Failed to clone GitHub repository: {error_text[:300]}")

        if ref:
            try:
                self._checkout_git_ref(destination, ref, env)
            except HTTPException:
                shutil.rmtree(destination, ignore_errors=True)
                raise

    def _self_workspace_root(self) -> Path:
        configured = os.getenv("CATOWN_SELF_WORKSPACE")
        if configured:
            return Path(configured).expanduser().resolve()
        return Path(__file__).resolve().parent.parent.parent

    def _default_self_project_agent_names(self) -> list[str]:
        active_agents = (
            self.db.query(Agent)
            .filter(Agent.is_active == True)
            .order_by(Agent.id.asc())
            .all()
        )
        names = [agent.agent_type for agent in active_agents if agent.agent_type]
        return names or [DEFAULT_AGENT_TYPE]

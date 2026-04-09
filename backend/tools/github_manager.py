# -*- coding: -*- coding: utf-8 -*-
"""
GitHub Management Tool

Lets agents manage GitHub projects via the REST API + local git.

Supported actions:
  # Repo
  - repo_info:        Get repository info
  - fork_repo:        Fork a repository
  # Branches
  - list_branches:    List branches
  - create_branch:    Create a branch
  - delete_branch:    Delete a branch
  # Tags & Releases
  - list_tags:        List tags
  - create_tag:       Create a tag (annotated or lightweight)
  - list_releases:    List releases
  - create_release:   Create a release
  - update_release:   Update a release
  - delete_release:   Delete a release
  # Issues
  - list_issues:      List issues (with filters)
  - create_issue:     Create an issue
  - close_issue:      Close an issue
  # Pull Requests
  - list_prs:         List pull requests
  - create_pr:        Create a pull request
  # Files & Content
  - list_contents:    List files/dirs in a repo path
  - get_file:         Get file content (decoded)
  - create_file:      Create a new file
  - update_file:      Update an existing file
  - delete_file:      Delete a file
  - clone_repo:       Clone repo to workspace (local git)
  # Commits
  - list_commits:     List commits (with path/author filters)
  - get_commit:       Get commit details (files changed, diff)
  # Search
  - search_code:      Search code in a repo

Authentication: set GITHUB_TOKEN env var (classic PAT or fine-grained with repo scope).
Repository:     set GITHUB_REPO env var as "owner/repo", or pass repo param per call.
"""
from .base import BaseTool
from typing import Optional, Dict, Any, List
import os
import json
import base64
import logging

logger = logging.getLogger("catown.github")

API_BASE = "https://api.github.com"


def _get_headers() -> dict:
    token = os.getenv("GITHUB_TOKEN", "")
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _get_default_repo() -> Optional[str]:
    repo = os.getenv("GITHUB_REPO", "").strip()
    if repo:
        return repo
    try:
        import subprocess
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
            cwd=os.getenv("CATOWN_WORKSPACE", os.getcwd())
        )
        url = result.stdout.strip()
        if "github.com" in url:
            if url.startswith("git@"):
                path = url.split(":")[1]
            else:
                path = "/".join(url.split("github.com/")[1:])
            return path.rstrip(".git")
    except Exception:
        pass
    return None


async def _api_request(method: str, path: str, data: dict = None, params: dict = None) -> dict:
    import httpx
    url = f"{API_BASE}{path}"
    headers = _get_headers()

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.request(
            method, url,
            headers=headers,
            json=data if data else None,
            params=params
        )

        if resp.status_code == 204:
            return {"success": True}

        body = resp.json() if resp.content else {}

        if resp.status_code >= 400:
            msg = body.get("message", resp.text[:300])
            raise RuntimeError(f"GitHub API {resp.status_code}: {msg}")

        return body


class GitHubManagerTool(BaseTool):
    """Manage a GitHub repository: files, branches, commits, releases, tags, issues, PRs, and more."""

    name = "github_manager"
    description = (
        "Manage a GitHub repository. Actions: repo_info, fork_repo, "
        "list_branches, create_branch, delete_branch, "
        "list_tags, create_tag, "
        "list_releases, create_release, update_release, delete_release, "
        "list_issues, create_issue, close_issue, "
        "list_prs, create_pr, "
        "list_contents, get_file, create_file, update_file, delete_file, clone_repo, "
        "list_commits, get_commit, "
        "search_code. "
        "Requires GITHUB_TOKEN env var. Default repo is auto-detected from git remote "
        "or set GITHUB_REPO env var (owner/repo format)."
    )

    async def execute(self, action: str, repo: str = "", **kwargs) -> str:
        repo = repo.strip() or (_get_default_repo() or "")

        # clone_repo / search_code don't need repo pre-resolved
        if action == "clone_repo":
            return await self._action_clone_repo(repo or kwargs.get("repo", ""), **kwargs)
        if action == "search_code":
            if not repo:
                return "[github_manager] Error: 'repo' is required for search_code."
            return await self._action_search_code(repo, **kwargs)

        if not repo:
            return (
                "[github_manager] Error: No repository specified. "
                "Set GITHUB_REPO=owner/repo or pass repo parameter."
            )

        write_actions = {
            "create_tag", "create_release", "update_release", "delete_release",
            "create_issue", "close_issue", "create_pr",
            "create_branch", "delete_branch", "fork_repo",
            "create_file", "update_file", "delete_file",
        }
        if action in write_actions and not os.getenv("GITHUB_TOKEN"):
            return (
                "[github_manager] Error: GITHUB_TOKEN env var is required for "
                f"action '{action}'. Set it to a GitHub Personal Access Token."
            )

        try:
            handler = getattr(self, f"_action_{action}", None)
            if not handler:
                return f"[github_manager] Unknown action: '{action}'. Use: {self._list_actions()}"
            return await handler(repo, **kwargs)
        except RuntimeError as e:
            return f"[github_manager] {e}"
        except Exception as e:
            logger.error(f"[github_manager] {action} failed: {e}")
            return f"[github_manager] Error: {e}"

    # ──────────────────── Repo ────────────────────

    async def _action_repo_info(self, repo: str, **kw) -> str:
        data = await _api_request("GET", f"/repos/{repo}")
        return (
            f"[GitHub] {data['full_name']}\n"
            f"  Description: {data.get('description') or '(none)'}\n"
            f"  Default branch: {data.get('default_branch')}\n"
            f"  Stars: {data.get('stargazers_count')}  "
            f"Forks: {data.get('forks_count')}  "
            f"Open issues: {data.get('open_issues_count')}\n"
            f"  Language: {data.get('language') or '(none)'}\n"
            f"  URL: {data.get('html_url')}\n"
            f"  Created: {data.get('created_at')}  Updated: {data.get('updated_at')}"
        )

    async def _action_fork_repo(self, repo: str, organization: str = "", **kw) -> str:
        """Fork a repository."""
        payload = {}
        if organization:
            payload["organization"] = organization
        data = await _api_request("POST", f"/repos/{repo}/forks", data=payload)
        return (
            f"[GitHub] Fork created: {data['full_name']}\n"
            f"  URL: {data['html_url']}\n"
            f"  Branch: {data.get('default_branch', 'main')}"
        )

    # ──────────────────── Branches ────────────────────

    async def _action_list_branches(self, repo: str, **kw) -> str:
        data = await _api_request("GET", f"/repos/{repo}/branches", params={"per_page": 100})
        if not data:
            return f"[GitHub] No branches found in {repo}."
        lines = [f"  - {b['name']}" + (" (protected)" if b.get('protected') else "") for b in data]
        return f"[GitHub] Branches in {repo} ({len(data)}):\n" + "\n".join(lines)

    async def _action_create_branch(self, repo: str, branch: str = "", source: str = "", **kw) -> str:
        """Create a branch from source (default: default branch HEAD)."""
        if not branch:
            return "[github_manager] Error: 'branch' is required for create_branch."

        if not source:
            repo_info = await _api_request("GET", f"/repos/{repo}")
            source = repo_info["default_branch"]

        # Resolve source to SHA
        try:
            branch_data = await _api_request("GET", f"/repos/{repo}/branches/{source}")
            sha = branch_data["commit"]["sha"]
        except Exception:
            sha = source  # assume it's a SHA

        await _api_request("POST", f"/repos/{repo}/git/refs", data={
            "ref": f"refs/heads/{branch}",
            "sha": sha
        })
        return f"[GitHub] Branch '{branch}' created from '{source}' ({sha[:8]})"

    async def _action_delete_branch(self, repo: str, branch: str = "", **kw) -> str:
        """Delete a branch."""
        if not branch:
            return "[github_manager] Error: 'branch' is required for delete_branch."
        await _api_request("DELETE", f"/repos/{repo}/git/refs/heads/{branch}")
        return f"[GitHub] Branch '{branch}' deleted."

    # ──────────────────── Tags & Releases ────────────────────

    async def _action_list_tags(self, repo: str, **kw) -> str:
        data = await _api_request("GET", f"/repos/{repo}/tags", params={"per_page": 100})
        if not data:
            return f"[GitHub] No tags found in {repo}."
        lines = [f"  - {t['name']} (commit: {t['commit']['sha'][:8]})" for t in data]
        return f"[GitHub] Tags in {repo} ({len(data)}):\n" + "\n".join(lines)

    async def _action_create_tag(
        self, repo: str,
        tag: str = "", ref: str = "",
        message: str = "", tagger_name: str = "", tagger_email: str = "",
        **kw
    ) -> str:
        if not tag:
            return "[github_manager] Error: 'tag' is required for create_tag."

        if not ref:
            repo_info = await _api_request("GET", f"/repos/{repo}")
            default_branch = repo_info["default_branch"]
            branch_data = await _api_request("GET", f"/repos/{repo}/branches/{default_branch}")
            ref = branch_data["commit"]["sha"]

        if len(ref) < 40:
            try:
                branch_data = await _api_request("GET", f"/repos/{repo}/branches/{ref}")
                ref = branch_data["commit"]["sha"]
            except Exception:
                pass

        if message:
            tagger_name = tagger_name or os.getenv("GIT_AUTHOR_NAME", "Catown")
            tagger_email = tagger_email or os.getenv("GIT_AUTHOR_EMAIL", "catown@bot")
            from datetime import datetime, timezone
            tag_obj = await _api_request("POST", f"/repos/{repo}/git/tags", data={
                "tag": tag,
                "message": message,
                "object": ref,
                "type": "commit",
                "tagger": {
                    "name": tagger_name,
                    "email": tagger_email,
                    "date": datetime.now(timezone.utc).isoformat()
                }
            })
            sha = tag_obj["sha"]
            await _api_request("POST", f"/repos/{repo}/git/refs", data={
                "ref": f"refs/tags/{tag}",
                "sha": sha
            })
            return f"[GitHub] Annotated tag '{tag}' created on {ref[:8]} (tag object: {sha[:8]})"
        else:
            await _api_request("POST", f"/repos/{repo}/git/refs", data={
                "ref": f"refs/tags/{tag}",
                "sha": ref
            })
            return f"[GitHub] Lightweight tag '{tag}' created on {ref[:8]}"

    async def _action_list_releases(self, repo: str, per_page: int = 10, **kw) -> str:
        data = await _api_request(
            "GET", f"/repos/{repo}/releases",
            params={"per_page": min(per_page, 50)}
        )
        if not data:
            return f"[GitHub] No releases in {repo}."
        lines = []
        for r in data:
            status = "draft" if r.get("draft") else ("prerelease" if r.get("prerelease") else "release")
            lines.append(
                f"  - {r['tag_name']} ({status}): {r.get('name') or '(no title)'} "
                f"[id={r['id']}] {r.get('published_at') or r.get('created_at', '')[:10]}"
            )
        return f"[GitHub] Releases in {repo} ({len(data)}):\n" + "\n".join(lines)

    async def _action_create_release(
        self, repo: str,
        tag: str = "", name: str = "", body: str = "",
        target: str = "", draft: bool = False, prerelease: bool = False,
        **kw
    ) -> str:
        if not tag:
            return "[github_manager] Error: 'tag' is required for create_release."

        payload: Dict[str, Any] = {
            "tag_name": tag,
            "name": name or tag,
            "body": body or "",
            "draft": draft,
            "prerelease": prerelease,
        }
        if target:
            payload["target_commitish"] = target

        data = await _api_request("POST", f"/repos/{repo}/releases", data=payload)
        return (
            f"[GitHub] Release created: {data['name']} ({data['tag_name']})\n"
            f"  URL: {data['html_url']}\n"
            f"  ID: {data['id']}  Draft: {data['draft']}  Prerelease: {data['prerelease']}"
        )

    async def _action_update_release(
        self, repo: str,
        release_id: int = 0, name: str = "", body: str = "",
        draft: Optional[bool] = None, prerelease: Optional[bool] = None,
        tag: str = "", **kw
    ) -> str:
        if not release_id and not tag:
            return "[github_manager] Error: 'release_id' or 'tag' is required for update_release."

        if not release_id:
            releases = await _api_request("GET", f"/repos/{repo}/releases", params={"per_page": 100})
            match = next((r for r in releases if r["tag_name"] == tag), None)
            if not match:
                return f"[github_manager] No release found with tag '{tag}'."
            release_id = match["id"]

        payload: Dict[str, Any] = {}
        if name:
            payload["name"] = name
        if body:
            payload["body"] = body
        if draft is not None:
            payload["draft"] = draft
        if prerelease is not None:
            payload["prerelease"] = prerelease
        if tag:
            payload["tag_name"] = tag

        data = await _api_request("PATCH", f"/repos/{repo}/releases/{release_id}", data=payload)
        return f"[GitHub] Release updated: {data['name']} ({data['tag_name']}) [id={data['id']}]"

    async def _action_delete_release(
        self, repo: str,
        release_id: int = 0, tag: str = "", **kw
    ) -> str:
        if not release_id and not tag:
            return "[github_manager] Error: 'release_id' or 'tag' is required for delete_release."

        if not release_id:
            releases = await _api_request("GET", f"/repos/{repo}/releases", params={"per_page": 100})
            match = next((r for r in releases if r["tag_name"] == tag), None)
            if not match:
                return f"[github_manager] No release found with tag '{tag}'."
            release_id = match["id"]

        await _api_request("DELETE", f"/repos/{repo}/releases/{release_id}")
        return f"[GitHub] Release deleted (id={release_id})."

    # ──────────────────── Issues ────────────────────

    async def _action_list_issues(
        self, repo: str,
        state: str = "open", labels: str = "", per_page: int = 15, **kw
    ) -> str:
        params = {"state": state, "per_page": min(per_page, 100), "sort": "created", "direction": "desc"}
        if labels:
            params["labels"] = labels
        data = await _api_request("GET", f"/repos/{repo}/issues", params=params)
        issues = [i for i in data if "pull_request" not in i]
        if not issues:
            return f"[GitHub] No {state} issues in {repo}."
        lines = []
        for i in issues[:per_page]:
            label_str = ", ".join(l["name"] for l in i.get("labels", []))
            label_str = f" [{label_str}]" if label_str else ""
            assignee = f" → {i['assignee']['login']}" if i.get("assignee") else ""
            lines.append(
                f"  #{i['number']} {i['title']}{label_str}{assignee} "
                f"({i['state']}, {i['created_at'][:10]})"
            )
        return f"[GitHub] {state.capitalize()} issues in {repo} ({len(issues)}):\n" + "\n".join(lines)

    async def _action_create_issue(
        self, repo: str,
        title: str = "", body: str = "",
        labels: str = "", assignees: str = "", **kw
    ) -> str:
        if not title:
            return "[github_manager] Error: 'title' is required for create_issue."

        payload: Dict[str, Any] = {"title": title, "body": body or ""}
        if labels:
            payload["labels"] = [l.strip() for l in labels.split(",") if l.strip()]
        if assignees:
            payload["assignees"] = [a.strip() for a in assignees.split(",") if a.strip()]

        data = await _api_request("POST", f"/repos/{repo}/issues", data=payload)
        return (
            f"[GitHub] Issue created: #{data['number']} {data['title']}\n"
            f"  URL: {data['html_url']}\n"
            f"  State: {data['state']}"
        )

    async def _action_close_issue(self, repo: str, issue_number: int = 0, **kw) -> str:
        if not issue_number:
            return "[github_manager] Error: 'issue_number' is required for close_issue."

        data = await _api_request(
            "PATCH", f"/repos/{repo}/issues/{issue_number}",
            data={"state": "closed", "state_reason": "completed"}
        )
        return f"[GitHub] Issue #{data['number']} closed: {data['title']}"

    # ──────────────────── Pull Requests ────────────────────

    async def _action_list_prs(
        self, repo: str,
        state: str = "open", per_page: int = 15, **kw
    ) -> str:
        params = {"state": state, "per_page": min(per_page, 100), "sort": "created", "direction": "desc"}
        data = await _api_request("GET", f"/repos/{repo}/pulls", params=params)
        if not data:
            return f"[GitHub] No {state} pull requests in {repo}."
        lines = []
        for pr in data[:per_page]:
            lines.append(
                f"  #{pr['number']} {pr['title']} "
                f"({pr['head']['ref']} → {pr['base']['ref']}, {pr['state']})"
            )
        return f"[GitHub] {state.capitalize()} PRs in {repo} ({len(data)}):\n" + "\n".join(lines)

    async def _action_create_pr(
        self, repo: str,
        title: str = "", head: str = "", base: str = "",
        body: str = "", draft: bool = False, **kw
    ) -> str:
        if not title or not head:
            return "[github_manager] Error: 'title' and 'head' are required for create_pr."

        if not base:
            repo_info = await _api_request("GET", f"/repos/{repo}")
            base = repo_info["default_branch"]

        payload: Dict[str, Any] = {
            "title": title,
            "head": head,
            "base": base,
            "body": body or "",
            "draft": draft,
        }

        data = await _api_request("POST", f"/repos/{repo}/pulls", data=payload)
        return (
            f"[GitHub] PR created: #{data['number']} {data['title']}\n"
            f"  {data['head']['ref']} → {data['base']['ref']}\n"
            f"  URL: {data['html_url']}\n"
            f"  State: {data['state']}  Draft: {data.get('draft', False)}"
        )

    # ──────────────────── Files & Content ────────────────────

    async def _action_list_contents(
        self, repo: str, path: str = "", ref: str = "", **kw
    ) -> str:
        """List files and dirs at a repo path."""
        params = {}
        if ref:
            params["ref"] = ref
        data = await _api_request("GET", f"/repos/{repo}/contents/{path}", params=params)
        if isinstance(data, dict) and data.get("type") == "file":
            return f"[GitHub] {path} is a file ({data.get('size', 0)} bytes)."
        if not data:
            return f"[GitHub] Empty directory: /{path}"
        lines = []
        for item in sorted(data, key=lambda x: (x["type"] != "dir", x["name"])):
            icon = "📁" if item["type"] == "dir" else "📄"
            size = f" ({item['size']}B)" if item["type"] == "file" else ""
            lines.append(f"  {icon} {item['name']}{size}")
        return f"[GitHub] Contents of /{path or ''} in {repo}:\n" + "\n".join(lines)

    async def _action_get_file(
        self, repo: str, path: str = "", ref: str = "", **kw
    ) -> str:
        """Get file content (decoded from base64)."""
        if not path:
            return "[github_manager] Error: 'path' is required for get_file."

        params = {}
        if ref:
            params["ref"] = ref
        data = await _api_request("GET", f"/repos/{repo}/contents/{path}", params=params)

        if data.get("type") != "file":
            return f"[github_manager] '{path}' is not a file (type: {data.get('type')})."

        content = base64.b64decode(data["content"]).decode("utf-8")
        size = data.get("size", 0)
        sha = data.get("sha", "")[:8]

        if size > 50000:
            return (
                f"[GitHub] File: {path} ({size} bytes, sha: {sha})\n"
                f"  [File too large ({size} bytes), showing first 10000 chars]\n\n"
                f"{content[:10000]}"
            )
        return f"[GitHub] File: {path} ({size} bytes, sha: {sha})\n\n{content}"

    async def _action_create_file(
        self, repo: str,
        path: str = "", content: str = "",
        message: str = "", branch: str = "", **kw
    ) -> str:
        """Create a new file in the repo."""
        if not path:
            return "[github_manager] Error: 'path' is required for create_file."
        if content is None:
            return "[github_manager] Error: 'content' is required for create_file."
        if not message:
            message = f"Create {path}"

        payload: Dict[str, Any] = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        }
        if branch:
            payload["branch"] = branch

        data = await _api_request("PUT", f"/repos/{repo}/contents/{path}", data=payload)
        commit = data.get("commit", {})
        return (
            f"[GitHub] File created: {path}\n"
            f"  Commit: {commit.get('sha', '')[:8]}\n"
            f"  URL: {data.get('content', {}).get('html_url', '')}"
        )

    async def _action_update_file(
        self, repo: str,
        path: str = "", content: str = "",
        message: str = "", branch: str = "", sha: str = "", **kw
    ) -> str:
        """Update an existing file. Requires the file's current SHA for conflict detection."""
        if not path:
            return "[github_manager] Error: 'path' is required for update_file."
        if content is None:
            return "[github_manager] Error: 'content' is required for update_file."
        if not message:
            message = f"Update {path}"

        # Auto-fetch SHA if not provided
        if not sha:
            file_data = await _api_request("GET", f"/repos/{repo}/contents/{path}")
            sha = file_data.get("sha", "")
            if not sha:
                return f"[github_manager] Error: Could not get SHA for {path}."

        payload: Dict[str, Any] = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "sha": sha,
        }
        if branch:
            payload["branch"] = branch

        data = await _api_request("PUT", f"/repos/{repo}/contents/{path}", data=payload)
        commit = data.get("commit", {})
        return (
            f"[GitHub] File updated: {path}\n"
            f"  Commit: {commit.get('sha', '')[:8]}\n"
            f"  New SHA: {data.get('content', {}).get('sha', '')[:8]}"
        )

    async def _action_delete_file(
        self, repo: str,
        path: str = "", message: str = "",
        branch: str = "", sha: str = "", **kw
    ) -> str:
        """Delete a file from the repo."""
        if not path:
            return "[github_manager] Error: 'path' is required for delete_file."
        if not message:
            message = f"Delete {path}"

        if not sha:
            file_data = await _api_request("GET", f"/repos/{repo}/contents/{path}")
            sha = file_data.get("sha", "")
            if not sha:
                return f"[github_manager] Error: Could not get SHA for {path}."

        payload: Dict[str, Any] = {"message": message, "sha": sha}
        if branch:
            payload["branch"] = branch

        await _api_request("DELETE", f"/repos/{repo}/contents/{path}", data=payload)
        return f"[GitHub] File deleted: {path}"

    async def _action_clone_repo(
        self, repo: str, dest: str = "", branch: str = "", **kw
    ) -> str:
        """Clone a repo to the local workspace directory."""
        import subprocess

        if not repo:
            return "[github_manager] Error: 'repo' is required for clone_repo."

        workspace = os.getenv("CATOWN_WORKSPACE", os.getcwd())
        if dest:
            target = os.path.join(workspace, dest)
        else:
            target = os.path.join(workspace, repo.split("/")[-1])

        token = os.getenv("GITHUB_TOKEN", "")
        if token:
            clone_url = f"https://{token}@github.com/{repo}.git"
        else:
            clone_url = f"https://github.com/{repo}.git"

        cmd = ["git", "clone"]
        if branch:
            cmd.extend(["-b", branch])
        cmd.extend(["--depth", "1", clone_url, target])

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120,
                cwd=workspace
            )
            if result.returncode != 0:
                return f"[github_manager] Clone failed: {result.stderr[:500]}"
            return f"[GitHub] Cloned {repo} → {target}"
        except subprocess.TimeoutExpired:
            return "[github_manager] Clone timed out (120s)."

    # ──────────────────── Commits ────────────────────

    async def _action_list_commits(
        self, repo: str,
        branch: str = "", path: str = "",
        author: str = "", per_page: int = 15, **kw
    ) -> str:
        """List commits with optional filters."""
        params: Dict[str, Any] = {"per_page": min(per_page, 100)}
        if branch:
            params["sha"] = branch
        if path:
            params["path"] = path
        if author:
            params["author"] = author

        data = await _api_request("GET", f"/repos/{repo}/commits", params=params)
        if not data:
            return f"[GitHub] No commits found in {repo}."
        lines = []
        for c in data[:per_page]:
            msg = c["commit"]["message"].split("\n")[0][:80]
            author_name = c["commit"]["author"]["name"]
            date = c["commit"]["author"]["date"][:10]
            lines.append(f"  {c['sha'][:8]} {date} {author_name}: {msg}")
        return f"[GitHub] Commits in {repo} ({len(data)}):\n" + "\n".join(lines)

    async def _action_get_commit(self, repo: str, sha: str = "", **kw) -> str:
        """Get commit details: message, author, files changed."""
        if not sha:
            return "[github_manager] Error: 'sha' is required for get_commit."

        data = await _api_request("GET", f"/repos/{repo}/commits/{sha}")
        commit = data["commit"]
        files = data.get("files", [])

        lines = [
            f"[GitHub] Commit {data['sha'][:8]} in {repo}",
            f"  Author: {commit['author']['name']} <{commit['author']['email']}>",
            f"  Date: {commit['author']['date']}",
            f"  Message: {commit['message'][:200]}",
            f"  Files changed: {len(files)}",
        ]
        for f in files[:30]:
            lines.append(f"    {f['status'][:1].upper()} {f['filename']} (+{f['additions']}/-{f['deletions']})")
        if len(files) > 30:
            lines.append(f"    ... and {len(files) - 30} more files")

        stats = data.get("stats", {})
        lines.append(f"  Total: +{stats.get('additions', 0)}/-{stats.get('deletions', 0)}")

        return "\n".join(lines)

    # ──────────────────── Search ────────────────────

    async def _action_search_code(
        self, repo: str, query: str = "", per_page: int = 10, **kw
    ) -> str:
        """Search code in a repo."""
        if not query:
            return "[github_manager] Error: 'query' is required for search_code."

        params = {
            "q": f"{query} repo:{repo}",
            "per_page": min(per_page, 30),
        }
        data = await _api_request("GET", "/search/code", params=params)

        items = data.get("items", [])
        total = data.get("total_count", 0)
        if not items:
            return f"[GitHub] No results for '{query}' in {repo}."

        lines = []
        for item in items[:per_page]:
            text_matches = item.get("text_matches", [])
            snippet = ""
            if text_matches:
                fragment = text_matches[0].get("fragment", "")
                snippet = f"\n      {fragment[:120]}"
            lines.append(f"  📄 {item['path']}{snippet}")

        return f"[GitHub] Code search: '{query}' in {repo} ({total} results):\n" + "\n".join(lines)

    # ──────────────────── Helpers ────────────────────

    def _list_actions(self) -> str:
        return (
            "repo_info, fork_repo, "
            "list_branches, create_branch, delete_branch, "
            "list_tags, create_tag, "
            "list_releases, create_release, update_release, delete_release, "
            "list_issues, create_issue, close_issue, "
            "list_prs, create_pr, "
            "list_contents, get_file, create_file, update_file, delete_file, clone_repo, "
            "list_commits, get_commit, "
            "search_code"
        )

    def _get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "Action to perform",
                    "enum": [
                        "repo_info", "fork_repo",
                        "list_branches", "create_branch", "delete_branch",
                        "list_tags", "create_tag",
                        "list_releases", "create_release", "update_release", "delete_release",
                        "list_issues", "create_issue", "close_issue",
                        "list_prs", "create_pr",
                        "list_contents", "get_file", "create_file", "update_file", "delete_file", "clone_repo",
                        "list_commits", "get_commit",
                        "search_code",
                    ]
                },
                "repo": {
                    "type": "string",
                    "description": "Repository in owner/repo format. Auto-detected from git remote if omitted.",
                    "default": ""
                },
                # File
                "path": {
                    "type": "string",
                    "description": "File/dir path in repo (for get_file, create_file, update_file, delete_file, list_contents)",
                    "default": ""
                },
                "content": {
                    "type": "string",
                    "description": "File content (for create_file, update_file)",
                    "default": ""
                },
                "sha": {
                    "type": "string",
                    "description": "File SHA (for update_file/delete_file) or commit SHA (for get_commit)",
                    "default": ""
                },
                # Branch
                "branch": {
                    "type": "string",
                    "description": "Branch name (for create_branch, delete_branch, clone_repo, or file operations)",
                    "default": ""
                },
                "source": {
                    "type": "string",
                    "description": "Source branch for create_branch",
                    "default": ""
                },
                # Tag
                "tag": {
                    "type": "string",
                    "description": "Tag name (for create_tag, create_release, etc.)",
                    "default": ""
                },
                "ref": {
                    "type": "string",
                    "description": "Commit SHA or branch to tag (default: HEAD of default branch)",
                    "default": ""
                },
                "message": {
                    "type": "string",
                    "description": "Commit message (for create_file, update_file, delete_file) or tag message",
                    "default": ""
                },
                # Release
                "release_id": {
                    "type": "integer",
                    "description": "Release ID (for update_release / delete_release)",
                    "default": 0
                },
                "name": {
                    "type": "string",
                    "description": "Release title or issue title",
                    "default": ""
                },
                "body": {
                    "type": "string",
                    "description": "Release notes, issue body, or PR description (Markdown)",
                    "default": ""
                },
                "target": {
                    "type": "string",
                    "description": "Commit SHA or branch for release tag creation",
                    "default": ""
                },
                "draft": {
                    "type": "boolean",
                    "description": "Create as draft (release or PR)",
                    "default": False
                },
                "prerelease": {
                    "type": "boolean",
                    "description": "Mark release as prerelease",
                    "default": False
                },
                # Issue
                "title": {
                    "type": "string",
                    "description": "Issue title or PR title",
                    "default": ""
                },
                "issue_number": {
                    "type": "integer",
                    "description": "Issue number (for close_issue)",
                    "default": 0
                },
                "labels": {
                    "type": "string",
                    "description": "Comma-separated labels (e.g. 'bug,priority:high')",
                    "default": ""
                },
                "assignees": {
                    "type": "string",
                    "description": "Comma-separated GitHub usernames",
                    "default": ""
                },
                # PR
                "head": {
                    "type": "string",
                    "description": "Source branch for PR",
                    "default": ""
                },
                "base": {
                    "type": "string",
                    "description": "Target branch for PR (default: repo's default branch)",
                    "default": ""
                },
                # Commit
                "author": {
                    "type": "string",
                    "description": "Filter commits by author (for list_commits)",
                    "default": ""
                },
                # Clone
                "dest": {
                    "type": "string",
                    "description": "Destination directory name in workspace (for clone_repo)",
                    "default": ""
                },
                # Fork
                "organization": {
                    "type": "string",
                    "description": "Organization to fork into (for fork_repo)",
                    "default": ""
                },
                # Search
                "query": {
                    "type": "string",
                    "description": "Search query (for search_code)",
                    "default": ""
                },
                # Common
                "state": {
                    "type": "string",
                    "description": "Filter by state (open/closed/all)",
                    "enum": ["open", "closed", "all"],
                    "default": "open"
                },
                "per_page": {
                    "type": "integer",
                    "description": "Max items to return (1-100)",
                    "default": 15
                }
            },
            "required": ["action"]
        }

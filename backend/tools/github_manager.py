# -*- coding: utf-8 -*-
"""
GitHub Management Tool

Lets the release agent (and others) manage GitHub projects via the REST API.

Supported actions:
  - repo_info:        Get repository info
  - list_branches:    List branches
  - list_tags:        List tags
  - create_tag:       Create a tag (annotated, lightweight, or from existing commit)
  - list_releases:    List releases
  - create_release:   Create a release (with tag, body, draft/prerelease)
  - update_release:   Update an existing release
  - delete_release:   Delete a release
  - list_issues:      List issues (with filters)
  - create_issue:     Create an issue
  - close_issue:      Close an issue
  - list_prs:         List pull requests
  - create_pr:        Create a pull request

Authentication: set GITHUB_TOKEN env var (classic PAT or fine-grained with repo scope).
Repository:     set GITHUB_REPO env var as "owner/repo", or pass repo param per call.
"""
from .base import BaseTool
from typing import Optional, Dict, Any, List
import os
import json
import logging

logger = logging.getLogger("catown.github")

API_BASE = "https://api.github.com"


def _get_headers() -> dict:
    """Build GitHub API request headers."""
    token = os.getenv("GITHUB_TOKEN", "")
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _get_default_repo() -> Optional[str]:
    """Get default repo from env or git remote."""
    repo = os.getenv("GITHUB_REPO", "").strip()
    if repo:
        return repo
    # Try to detect from git remote
    try:
        import subprocess
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
            cwd=os.getenv("CATOWN_WORKSPACE", os.getcwd())
        )
        url = result.stdout.strip()
        # git@github.com:owner/repo.git or https://github.com/owner/repo.git
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
    """Make a GitHub API request and return parsed response."""
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
    """Manage a GitHub repository: releases, tags, issues, PRs, and repo info."""

    name = "github_manager"
    description = (
        "Manage a GitHub repository. Actions: repo_info, list_branches, list_tags, "
        "create_tag, list_releases, create_release, update_release, delete_release, "
        "list_issues, create_issue, close_issue, list_prs, create_pr. "
        "Requires GITHUB_TOKEN env var. Default repo is auto-detected from git remote "
        "or set GITHUB_REPO env var (owner/repo format)."
    )

    async def execute(self, action: str, repo: str = "", **kwargs) -> str:
        # Resolve repo
        repo = repo.strip() or (_get_default_repo() or "")
        if not repo:
            return (
                "[github_manager] Error: No repository specified. "
                "Set GITHUB_REPO=owner/repo or pass repo parameter."
            )

        # Check token for write operations
        write_actions = {
            "create_tag", "create_release", "update_release", "delete_release",
            "create_issue", "close_issue", "create_pr"
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

    # ──────────────────────── actions ────────────────────────

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

    async def _action_list_branches(self, repo: str, **kw) -> str:
        data = await _api_request("GET", f"/repos/{repo}/branches", params={"per_page": 30})
        if not data:
            return f"[GitHub] No branches found in {repo}."
        lines = [f"  - {b['name']}" + (" (protected)" if b.get('protected') else "") for b in data]
        return f"[GitHub] Branches in {repo} ({len(data)}):\n" + "\n".join(lines)

    async def _action_list_tags(self, repo: str, **kw) -> str:
        data = await _api_request("GET", f"/repos/{repo}/tags", params={"per_page": 30})
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
        """
        Create an annotated tag. If message is empty, creates a lightweight tag
        (just a ref pointing at ref/commit SHA).

        Args:
            tag: Tag name (e.g. "v1.0.0")
            ref: Commit SHA or branch name to tag (defaults to HEAD of default branch)
            message: Tag message (makes it annotated; omit for lightweight)
            tagger_name: Tagger name (default: from env GIT_AUTHOR_NAME or "Catown")
            tagger_email: Tagger email (default: from env GIT_AUTHOR_EMAIL or "catown@bot")
        """
        if not tag:
            return "[github_manager] Error: 'tag' is required for create_tag."

        # Resolve ref to commit SHA
        if not ref:
            # Get default branch HEAD
            repo_info = await _api_request("GET", f"/repos/{repo}")
            default_branch = repo_info["default_branch"]
            branch_data = await _api_request("GET", f"/repos/{repo}/branches/{default_branch}")
            ref = branch_data["commit"]["sha"]

        # If ref is a branch name, resolve to SHA
        if len(ref) < 40:
            try:
                branch_data = await _api_request("GET", f"/repos/{repo}/branches/{ref}")
                ref = branch_data["commit"]["sha"]
            except Exception:
                pass  # assume it's already a SHA

        if message:
            # Annotated tag: create a tag object first
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
            # Create ref pointing to the tag object
            sha = tag_obj["sha"]
            await _api_request("POST", f"/repos/{repo}/git/refs", data={
                "ref": f"refs/tags/{tag}",
                "sha": sha
            })
            return f"[GitHub] Annotated tag '{tag}' created on {ref[:8]} (tag object: {sha[:8]})"
        else:
            # Lightweight tag: just a ref
            await _api_request("POST", f"/repos/{repo}/git/refs", data={
                "ref": f"refs/tags/{tag}",
                "sha": ref
            })
            return f"[GitHub] Lightweight tag '{tag}' created on {ref[:8]}"

    async def _action_list_releases(
        self, repo: str, per_page: int = 10, **kw
    ) -> str:
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
        tag: str = "",
        name: str = "",
        body: str = "",
        target: str = "",
        draft: bool = False,
        prerelease: bool = False,
        **kw
    ) -> str:
        """
        Create a GitHub release.

        Args:
            tag: Tag name for the release (e.g. "v1.0.0")
            name: Release title (defaults to tag name)
            body: Release notes / description (Markdown)
            target: Commit SHA or branch to tag (if tag doesn't exist yet)
            draft: Create as draft (default false)
            prerelease: Mark as prerelease (default false)
        """
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
        release_id: int = 0,
        name: str = "",
        body: str = "",
        draft: Optional[bool] = None,
        prerelease: Optional[bool] = None,
        tag: str = "",
        **kw
    ) -> str:
        """Update an existing release by ID or tag name."""
        if not release_id and not tag:
            return "[github_manager] Error: 'release_id' or 'tag' is required for update_release."

        # Find by tag if no ID
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
        release_id: int = 0,
        tag: str = "",
        **kw
    ) -> str:
        """Delete a release by ID or tag name."""
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

    async def _action_list_issues(
        self, repo: str,
        state: str = "open",
        labels: str = "",
        per_page: int = 15,
        **kw
    ) -> str:
        params = {"state": state, "per_page": min(per_page, 100), "sort": "created", "direction": "desc"}
        if labels:
            params["labels"] = labels
        data = await _api_request("GET", f"/repos/{repo}/issues", params=params)
        # Filter out pull requests (GitHub API returns PRs in issues endpoint)
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
        title: str = "",
        body: str = "",
        labels: str = "",
        assignees: str = "",
        **kw
    ) -> str:
        """
        Create an issue.

        Args:
            title: Issue title
            body: Issue body (Markdown)
            labels: Comma-separated label names (e.g. "bug,priority:high")
            assignees: Comma-separated GitHub usernames
        """
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

    async def _action_close_issue(
        self, repo: str,
        issue_number: int = 0,
        **kw
    ) -> str:
        """Close an issue by number."""
        if not issue_number:
            return "[github_manager] Error: 'issue_number' is required for close_issue."

        data = await _api_request(
            "PATCH", f"/repos/{repo}/issues/{issue_number}",
            data={"state": "closed", "state_reason": "completed"}
        )
        return f"[GitHub] Issue #{data['number']} closed: {data['title']}"

    async def _action_list_prs(
        self, repo: str,
        state: str = "open",
        per_page: int = 15,
        **kw
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
        title: str = "",
        head: str = "",
        base: str = "",
        body: str = "",
        draft: bool = False,
        **kw
    ) -> str:
        """
        Create a pull request.

        Args:
            title: PR title
            head: Source branch (the one with changes)
            base: Target branch (default: repo's default branch)
            body: PR description (Markdown)
            draft: Create as draft PR
        """
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

    def _list_actions(self) -> str:
        return (
            "repo_info, list_branches, list_tags, create_tag, "
            "list_releases, create_release, update_release, delete_release, "
            "list_issues, create_issue, close_issue, list_prs, create_pr"
        )

    def _get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "Action to perform",
                    "enum": [
                        "repo_info", "list_branches", "list_tags", "create_tag",
                        "list_releases", "create_release", "update_release", "delete_release",
                        "list_issues", "create_issue", "close_issue", "list_prs", "create_pr"
                    ]
                },
                "repo": {
                    "type": "string",
                    "description": "Repository in owner/repo format. Auto-detected from git remote if omitted.",
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
                    "description": "Tag message for annotated tags (omit for lightweight)",
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

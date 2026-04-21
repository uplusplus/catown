# -*- coding: utf-8 -*-
"""
Execute Code Tool — 带安全沙箱限制，支持 Python 和 Node.js
"""
from .base import BaseTool
from .file_operations import get_active_workspace
import asyncio
import subprocess
import tempfile
import os
import sys

# ─── Python 沙箱 ─────────────────────────────────────────────

PY_DENY_IMPORTS = [
    "os", "sys", "subprocess", "shutil", "ctypes", "pickle", "marshal",
    "multiprocessing", "socket", "httplib", "http", "urllib", "requests",
    "ftplib", "smtplib", "telnetlib", "xmlrpc", "pathlib", "glob",
    "webbrowser", "cgi", "cgitb", "pty", "ptyprocess",
]

PY_SANDBOX_WRAPPER = r"""
import builtins as _sb

_original_import = _sb.__import__
_deny_modules = {deny_modules_placeholder}

def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    top_level = name.split('.')[0]
    if top_level in _deny_modules:
        raise ImportError(f"Import of '{name}' is not allowed in sandbox")
    return _original_import(name, globals, locals, fromlist, level)

if isinstance(_sb.__import__, type(_original_import)):
    if isinstance(_sb.__dict__, dict):
        _sb.__dict__['__import__'] = _safe_import

# 用户代码在此之下执行
"""

# ─── Node.js 沙箱 ────────────────────────────────────────────

NODE_BLOCKED_MODULES = [
    "child_process", "cluster", "dgram",
    "net", "tls", "dns", "dgram",
    "http", "https", "http2",
    "worker_threads",
]

NODE_SANDBOX_WRAPPER = r"""
// 拦截危险模块
const _origRequire = require;
const _blocked = {blocked_modules_placeholder};

require = function(name) {
    const top = name.split('/')[0];
    if (_blocked.includes(top)) {
        throw new Error(`Module '${name}' is not allowed in sandbox`);
    }
    return _origRequire(name);
};

// 拦截 process.exit 和危险的 child_process 方法
const _origExit = process.exit;
process.exit = function() { throw new Error('process.exit() is not allowed in sandbox'); };

// 用户代码在此之下执行
"""

# ─── 超时和输出限制 ─────────────────────────────────────────

TIMEOUT_SECONDS = 15
MAX_OUTPUT_CHARS = 50000


class ExecuteCodeTool(BaseTool):
    """Tool for executing code snippets with sandbox restrictions"""

    name = "execute_code"
    description = (
        "Execute code in a sandboxed environment. "
        "Supports 'python' and 'node' (JavaScript/Node.js). "
        "Dangerous imports/modules are blocked. "
        "Timeout: 15s, max output: 50KB."
    )

    def __init__(self, workspace: str = None):
        self.workspace = os.path.realpath(workspace or os.environ.get("CATOWN_WORKSPACE", os.getcwd()))

    async def execute(self, code: str, language: str = "python", **kwargs) -> str:
        """
        Execute code snippet in sandbox.

        Args:
            code: Code to execute
            language: 'python' or 'node'

        Returns:
            Execution output
        """
        lang = language.lower().strip()

        if lang in ("python", "py"):
            return await self._exec_python(code)
        elif lang in ("node", "nodejs", "javascript", "js"):
            return await self._exec_node(code)
        else:
            return f"[Execute Code] Language '{language}' not supported. Use 'python' or 'node'."

    # ─── Python 执行 ──────────────────────────────────────────

    async def _exec_python(self, code: str) -> str:
        try:
            working_dir = self._working_directory()
            # 文本扫描拦截明显恶意代码
            for keyword in ["__import__", "subprocess", "eval(", "exec(", "os.system", "shutil"]:
                if keyword in code:
                    return f"[Execute Code] Blocked: '{keyword}' is not allowed in sandbox."

            wrapper = PY_SANDBOX_WRAPPER.replace(
                "{deny_modules_placeholder}", repr(set(PY_DENY_IMPORTS))
            )
            full_code = wrapper + "\n" + code

            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False, encoding="utf-8"
            ) as f:
                f.write(full_code)
                temp_path = f.name

            try:
                result = await asyncio.to_thread(
                    subprocess.run,
                    [sys.executable, "-u", temp_path],
                    capture_output=True,
                    text=True,
                    timeout=TIMEOUT_SECONDS,
                    cwd=working_dir,
                    env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                )
            finally:
                os.unlink(temp_path)

            if result.returncode == 0:
                output = result.stdout.strip()[:MAX_OUTPUT_CHARS]
                return (
                    f"[Execute Code] Success:\n{output}"
                    if output
                    else "[Execute Code] Success (no output)"
                )
            else:
                return f"[Execute Code] Error:\n{self._clean_stderr(result.stderr)}"

        except subprocess.TimeoutExpired:
            return f"[Execute Code] Error: Execution timed out ({TIMEOUT_SECONDS}s limit)"
        except Exception as e:
            return f"[Execute Code] Error: {e}"

    # ─── Node.js 执行 ────────────────────────────────────────

    async def _exec_node(self, code: str) -> str:
        # 检查 node 是否可用
        node_bin = await self._find_node()
        if not node_bin:
            return "[Execute Code] Error: Node.js not found. Install node or set NODE_PATH env var."

        try:
            working_dir = self._working_directory()
            # 文本扫描
            for keyword in ["child_process", "cluster.fork", "eval(", ".exec("]:
                if keyword in code:
                    # eval 在 JS 中常用，只拦截 child_process 等
                    if "child_process" in keyword or "cluster" in keyword:
                        return f"[Execute Code] Blocked: '{keyword}' is not allowed in sandbox."

            wrapper = NODE_SANDBOX_WRAPPER.replace(
                "{blocked_modules_placeholder}", repr(NODE_BLOCKED_MODULES)
            )
            full_code = wrapper + "\n" + code

            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".js", delete=False, encoding="utf-8"
            ) as f:
                f.write(full_code)
                temp_path = f.name

            try:
                result = await asyncio.to_thread(
                    subprocess.run,
                    [node_bin, temp_path],
                    capture_output=True,
                    text=True,
                    timeout=TIMEOUT_SECONDS,
                    cwd=working_dir,
                    env={**os.environ, "NODE_NO_WARNINGS": "1"},
                )
            finally:
                os.unlink(temp_path)

            if result.returncode == 0:
                output = result.stdout.strip()[:MAX_OUTPUT_CHARS]
                return (
                    f"[Execute Code] Success:\n{output}"
                    if output
                    else "[Execute Code] Success (no output)"
                )
            else:
                return f"[Execute Code] Error:\n{self._clean_stderr(result.stderr)}"

        except subprocess.TimeoutExpired:
            return f"[Execute Code] Error: Execution timed out ({TIMEOUT_SECONDS}s limit)"
        except Exception as e:
            return f"[Execute Code] Error: {e}"

    # ─── 辅助方法 ────────────────────────────────────────────

    async def _find_node(self) -> str | None:
        return await asyncio.to_thread(self._find_node_sync)

    def _find_node_sync(self) -> str | None:
        """Find Node.js binary"""
        candidates = [
            os.environ.get("NODE_PATH", ""),
            "/usr/bin/node",
            "/usr/local/bin/node",
        ]
        for p in candidates:
            if p and os.path.isfile(p) and os.access(p, os.X_OK):
                return p
        # 尝试 which
        try:
            result = subprocess.run(
                ["which", "node"], capture_output=True, text=True, timeout=3
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return None

    def _working_directory(self) -> str:
        return os.path.realpath(get_active_workspace() or self.workspace)

    @staticmethod
    def _clean_stderr(stderr: str) -> str:
        """Filter internal stack frames, keep useful error info"""
        stderr = stderr.strip()
        if not stderr:
            return "(no error details)"
        if "Traceback" in stderr or "Error:" in stderr:
            lines = stderr.split("\n")
            useful = [
                l for l in lines if not l.strip().startswith("File ") or "tmp" in l
            ]
            return "\n".join(useful[-8:])[:MAX_OUTPUT_CHARS]
        return stderr[:MAX_OUTPUT_CHARS]

    def _get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Code to execute",
                },
                "language": {
                    "type": "string",
                    "description": "Programming language (default: python)",
                    "enum": ["python", "node"],
                },
            },
            "required": ["code"],
        }

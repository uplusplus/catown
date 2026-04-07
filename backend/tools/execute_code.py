# -*- coding: utf-8 -*-
"""
Execute Code Tool — 带安全沙箱限制
"""
from .base import BaseTool
import subprocess
import tempfile
import os
import sys

# 禁止导入的危险模块
DENY_IMPORTS = [
    "os", "sys", "subprocess", "shutil", "ctypes", "pickle", "marshal",
    "multiprocessing", "socket", "httplib", "http", "urllib", "requests",
    "ftplib", "smtplib", "telnetlib", "xmlrpc", "pathlib", "glob",
    "webbrowser", "cgi", "cgitb", "pty", "ptyprocess",
]

# 沙箱包装代码：仅通过 import hook 拦截危险导入
SANDBOX_WRAPPER = r"""
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

class ExecuteCodeTool(BaseTool):
    """Tool for executing code snippets with sandbox restrictions"""
    
    name = "execute_code"
    description = "Execute Python code snippets in a sandboxed environment. Dangerous imports (os, subprocess, socket, etc.) are blocked."
    
    async def execute(self, code: str, language: str = "python", **kwargs) -> str:
        """
        Execute code snippet in sandbox
        
        Args:
            code: Code to execute
            language: Programming language (python only)
            
        Returns:
            Execution output
        """
        if language != "python":
            return f"[Execute Code] Language '{language}' not supported. Only Python currently."
        
        try:
            # 先做简单文本扫描，拦截明显恶意代码
            for keyword in ['__import__', 'subprocess', 'eval(', 'exec(', 'os.system', 'shutil']:
                if keyword in code:
                    return f"[Execute Code] Blocked: '{keyword}' is not allowed in sandbox."
            
            # 准备沙箱代码
            wrapper = SANDBOX_WRAPPER.replace("{deny_modules_placeholder}", repr(set(DENY_IMPORTS)))
            full_code = wrapper + "\n" + code
            
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
                f.write(full_code)
                temp_path = f.name
            
            python_cmd = sys.executable
            
            result = subprocess.run(
                [python_cmd, "-u", temp_path],
                capture_output=True,
                text=True,
                timeout=15,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"}
            )
            
            os.unlink(temp_path)
            
            if result.returncode == 0:
                output = result.stdout.strip()
                return f"[Execute Code] Success:\n{output}" if output else "[Execute Code] Success (no output)"
            else:
                stderr = result.stderr.strip()
                # 过滤内部堆栈，只保留有用信息
                if "Traceback" in stderr:
                    lines = stderr.split("\n")
                    # 取最后几行（实际的错误信息）
                    useful = [l for l in lines if not l.startswith("  File") or "tmp" in l]
                    return f"[Execute Code] Error:\n" + "\n".join(useful[-5:])
                return f"[Execute Code] Error:\n{stderr}"
                
        except subprocess.TimeoutExpired:
            return "[Execute Code] Error: Execution timed out (15s limit)"
        except Exception as e:
            return f"[Execute Code] Error: {str(e)}"
    
    def _get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python code to execute"
                },
                "language": {
                    "type": "string",
                    "description": "Programming language (default: python)",
                    "enum": ["python"]
                }
            },
            "required": ["code"]
        }

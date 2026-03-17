# -*- coding: utf-8 -*-
"""
Execute Code Tool
"""
from .base import BaseTool
import subprocess
import tempfile
import os


class ExecuteCodeTool(BaseTool):
    """Tool for executing code snippets"""
    
    name = "execute_code"
    description = "Execute Python code snippets. Use this to run calculations, data processing, or code examples. Use with caution."
    
    async def execute(self, code: str, language: str = "python", **kwargs) -> str:
        """
        Execute code snippet
        
        Args:
            code: Code to execute
            language: Programming language (currently only python supported)
            
        Returns:
            Execution output
        """
        if language != "python":
            return f"[Execute Code] Language '{language}' not supported. Only Python is currently supported."
        
        try:
            # Create a temporary file and execute
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
                f.write(code)
                temp_path = f.name
            
            # Execute with timeout
            result = subprocess.run(
                ['python3', temp_path],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            # Clean up
            os.unlink(temp_path)
            
            if result.returncode == 0:
                return f"[Execute Code] Success:\n{result.stdout}"
            else:
                return f"[Execute Code] Error:\n{result.stderr}"
                
        except subprocess.TimeoutExpired:
            return "[Execute Code] Error: Execution timed out (10s limit)"
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

# -*- coding: utf-8 -*-
"""
File Operations Tools
"""
from .base import BaseTool
from typing import Optional, List
import os
import glob as glob_module
import json


class ReadFileTool(BaseTool):
    """Tool for reading file contents"""
    
    name = "read_file"
    description = "Read the contents of a file. Returns file content as text. Supports text files, JSON, and code files."
    
    def __init__(self, workspace: str = None):
        """
        Initialize with optional workspace directory
        
        Args:
            workspace: Base directory for file operations (for security)
        """
        self.workspace = workspace or os.getcwd()
    
    async def execute(self, file_path: str, encoding: str = "utf-8", **kwargs) -> str:
        """
        Read file contents
        
        Args:
            file_path: Path to the file (relative to workspace or absolute)
            encoding: File encoding (default: utf-8)
            
        Returns:
            File contents or error message
        """
        try:
            # Resolve path
            full_path = self._resolve_path(file_path)
            
            # Security check
            if not self._is_safe_path(full_path):
                return f"[Read File] Error: Access denied. Path outside workspace."
            
            if not os.path.exists(full_path):
                return f"[Read File] Error: File not found: '{file_path}'"
            
            if not os.path.isfile(full_path):
                return f"[Read File] Error: Not a file: '{file_path}'"
            
            # Read file
            with open(full_path, 'r', encoding=encoding) as f:
                content = f.read()
            
            # Limit output size
            max_size = 10000
            if len(content) > max_size:
                content = content[:max_size] + f"\n... (truncated, file has {len(content)} characters)"
            
            return f"[Read File] Content of '{file_path}':\n{content}"
            
        except UnicodeDecodeError:
            return f"[Read File] Error: Cannot decode file with encoding '{encoding}'. Try a different encoding or the file may be binary."
        except Exception as e:
            return f"[Read File] Error: {str(e)}"
    
    def _resolve_path(self, file_path: str) -> str:
        """Resolve relative path to absolute path"""
        if os.path.isabs(file_path):
            return os.path.normpath(file_path)
        return os.path.normpath(os.path.join(self.workspace, file_path))
    
    def _is_safe_path(self, path: str) -> bool:
        """Check if path is within workspace"""
        try:
            real_path = os.path.realpath(path)
            real_workspace = os.path.realpath(self.workspace)
            return real_path.startswith(real_workspace)
        except:
            return False
    
    def _get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the file to read (relative to workspace or absolute)"
                },
                "encoding": {
                    "type": "string",
                    "description": "File encoding (default: utf-8)",
                    "default": "utf-8"
                }
            },
            "required": ["file_path"]
        }


class WriteFileTool(BaseTool):
    """Tool for writing content to files"""
    
    name = "write_file"
    description = "Write content to a file. Creates the file if it doesn't exist, overwrites if it does. Use for creating or updating files."
    
    def __init__(self, workspace: str = None):
        self.workspace = workspace or os.getcwd()
    
    async def execute(self, file_path: str, content: str, mode: str = "write", **kwargs) -> str:
        """
        Write content to file
        
        Args:
            file_path: Path to the file
            content: Content to write
            mode: Write mode - 'write' (overwrite) or 'append'
            
        Returns:
            Success message or error
        """
        try:
            full_path = self._resolve_path(file_path)
            
            if not self._is_safe_path(full_path):
                return f"[Write File] Error: Access denied. Path outside workspace."
            
            # Create directory if needed
            dir_path = os.path.dirname(full_path)
            if dir_path and not os.path.exists(dir_path):
                os.makedirs(dir_path)
            
            # Write mode
            write_mode = 'a' if mode == 'append' else 'w'
            
            with open(full_path, write_mode, encoding='utf-8') as f:
                f.write(content)
            
            action = "Appended to" if mode == 'append' else "Wrote to"
            return f"[Write File] {action} '{file_path}' successfully ({len(content)} characters)"
            
        except Exception as e:
            return f"[Write File] Error: {str(e)}"
    
    def _resolve_path(self, file_path: str) -> str:
        if os.path.isabs(file_path):
            return os.path.normpath(file_path)
        return os.path.normpath(os.path.join(self.workspace, file_path))
    
    def _is_safe_path(self, path: str) -> bool:
        try:
            real_path = os.path.realpath(path)
            real_workspace = os.path.realpath(self.workspace)
            return real_path.startswith(real_workspace)
        except:
            return False
    
    def _get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the file to write"
                },
                "content": {
                    "type": "string",
                    "description": "Content to write to the file"
                },
                "mode": {
                    "type": "string",
                    "description": "Write mode: 'write' to overwrite, 'append' to add to end",
                    "enum": ["write", "append"],
                    "default": "write"
                }
            },
            "required": ["file_path", "content"]
        }


class ListFilesTool(BaseTool):
    """Tool for listing files in a directory"""
    
    name = "list_files"
    description = "List files and directories in a given path. Returns file names, sizes, and types."
    
    def __init__(self, workspace: str = None):
        self.workspace = workspace or os.getcwd()
    
    async def execute(self, directory: str = ".", pattern: str = "*", recursive: bool = False, **kwargs) -> str:
        """
        List files in directory
        
        Args:
            directory: Directory to list (default: current)
            pattern: Glob pattern to filter files (default: *)
            recursive: List recursively (default: False)
            
        Returns:
            List of files with details
        """
        try:
            full_path = self._resolve_path(directory)
            
            if not self._is_safe_path(full_path):
                return f"[List Files] Error: Access denied. Path outside workspace."
            
            if not os.path.exists(full_path):
                return f"[List Files] Error: Directory not found: '{directory}'"
            
            if not os.path.isdir(full_path):
                return f"[List Files] Error: Not a directory: '{directory}'"
            
            results = []
            
            if recursive:
                # Use glob for recursive listing
                search_pattern = os.path.join(full_path, '**', pattern)
                matches = glob_module.glob(search_pattern, recursive=True)
                
                for match in matches[:100]:  # Limit to 100 results
                    rel_path = os.path.relpath(match, full_path)
                    results.append(self._get_file_info(match, rel_path))
            else:
                # List direct children
                items = glob_module.glob(os.path.join(full_path, pattern))
                
                for item in items[:100]:
                    name = os.path.basename(item)
                    results.append(self._get_file_info(item, name))
            
            if not results:
                return f"[List Files] No files found in '{directory}' matching '{pattern}'"
            
            # Format output
            output = f"[List Files] Contents of '{directory}' (pattern: {pattern}):\n"
            for r in results:
                type_icon = "📁" if r['is_dir'] else "📄"
                output += f"  {type_icon} {r['name']} ({r['size']})\n"
            
            return output
            
        except Exception as e:
            return f"[List Files] Error: {str(e)}"
    
    def _resolve_path(self, path: str) -> str:
        if os.path.isabs(path):
            return os.path.normpath(path)
        return os.path.normpath(os.path.join(self.workspace, path))
    
    def _is_safe_path(self, path: str) -> bool:
        try:
            real_path = os.path.realpath(path)
            real_workspace = os.path.realpath(self.workspace)
            return real_path.startswith(real_workspace)
        except:
            return False
    
    def _get_file_info(self, path: str, display_name: str) -> dict:
        """Get file info dict"""
        try:
            is_dir = os.path.isdir(path)
            if is_dir:
                size = "dir"
            else:
                size_bytes = os.path.getsize(path)
                if size_bytes < 1024:
                    size = f"{size_bytes}B"
                elif size_bytes < 1024 * 1024:
                    size = f"{size_bytes // 1024}KB"
                else:
                    size = f"{size_bytes // (1024 * 1024)}MB"
            
            return {
                'name': display_name,
                'is_dir': is_dir,
                'size': size
            }
        except:
            return {
                'name': display_name,
                'is_dir': False,
                'size': 'unknown'
            }
    
    def _get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "directory": {
                    "type": "string",
                    "description": "Directory path to list (default: current directory)",
                    "default": "."
                },
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern to filter files (e.g., '*.py', '*.txt')",
                    "default": "*"
                },
                "recursive": {
                    "type": "boolean",
                    "description": "List files recursively",
                    "default": False
                }
            },
            "required": []
        }


class DeleteFileTool(BaseTool):
    """Tool for deleting files"""
    
    name = "delete_file"
    description = "Delete a file or empty directory. Use with caution - this operation cannot be undone."
    
    def __init__(self, workspace: str = None):
        self.workspace = workspace or os.getcwd()
    
    async def execute(self, file_path: str, force: bool = False, **kwargs) -> str:
        """
        Delete file or directory
        
        Args:
            file_path: Path to file or directory to delete
            force: Force delete non-empty directory (dangerous)
            
        Returns:
            Success message or error
        """
        try:
            full_path = self._resolve_path(file_path)
            
            if not self._is_safe_path(full_path):
                return f"[Delete File] Error: Access denied. Path outside workspace."
            
            if not os.path.exists(full_path):
                return f"[Delete File] Error: File not found: '{file_path}'"
            
            if os.path.isfile(full_path):
                os.remove(full_path)
                return f"[Delete File] Deleted file: '{file_path}'"
            
            if os.path.isdir(full_path):
                if force:
                    import shutil
                    shutil.rmtree(full_path)
                    return f"[Delete File] Deleted directory and contents: '{file_path}'"
                else:
                    os.rmdir(full_path)  # Only works for empty directories
                    return f"[Delete File] Deleted empty directory: '{file_path}'"
            
            return f"[Delete File] Error: Unknown file type: '{file_path}'"
            
        except OSError as e:
            if "not empty" in str(e).lower():
                return f"[Delete File] Error: Directory not empty. Use force=true to delete non-empty directories."
            return f"[Delete File] Error: {str(e)}"
        except Exception as e:
            return f"[Delete File] Error: {str(e)}"
    
    def _resolve_path(self, file_path: str) -> str:
        if os.path.isabs(file_path):
            return os.path.normpath(file_path)
        return os.path.normpath(os.path.join(self.workspace, file_path))
    
    def _is_safe_path(self, path: str) -> bool:
        try:
            real_path = os.path.realpath(path)
            real_workspace = os.path.realpath(self.workspace)
            return real_path.startswith(real_workspace)
        except:
            return False
    
    def _get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to file or directory to delete"
                },
                "force": {
                    "type": "boolean",
                    "description": "Force delete non-empty directory (dangerous)",
                    "default": False
                }
            },
            "required": ["file_path"]
        }


class SearchFilesTool(BaseTool):
    """Tool for searching file contents"""
    
    name = "search_files"
    description = "Search for text within files. Returns files and lines containing the search term."
    
    def __init__(self, workspace: str = None):
        self.workspace = workspace or os.getcwd()
    
    async def execute(self, search_term: str, directory: str = ".", file_pattern: str = "*", **kwargs) -> str:
        """
        Search for text in files
        
        Args:
            search_term: Text to search for
            directory: Directory to search in
            file_pattern: File pattern to search (e.g., '*.py')
            
        Returns:
            Search results with file names and line numbers
        """
        try:
            full_path = self._resolve_path(directory)
            
            if not self._is_safe_path(full_path):
                return f"[Search Files] Error: Access denied. Path outside workspace."
            
            results = []
            matches_count = 0
            max_results = 50
            
            for root, dirs, files in os.walk(full_path):
                # Filter files by pattern
                import fnmatch
                matching_files = fnmatch.filter(files, file_pattern)
                
                for filename in matching_files:
                    if matches_count >= max_results:
                        break
                    
                    file_path = os.path.join(root, filename)
                    
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            for line_num, line in enumerate(f, 1):
                                if search_term.lower() in line.lower():
                                    rel_path = os.path.relpath(file_path, full_path)
                                    results.append({
                                        'file': rel_path,
                                        'line': line_num,
                                        'content': line.strip()[:100]
                                    })
                                    matches_count += 1
                                    
                                    if matches_count >= max_results:
                                        break
                    except (UnicodeDecodeError, IOError):
                        # Skip binary or unreadable files
                        continue
                
                if matches_count >= max_results:
                    break
            
            if not results:
                return f"[Search Files] No matches found for '{search_term}' in {directory}"
            
            # Format output
            output = f"[Search Files] Found {len(results)} matches for '{search_term}':\n"
            for r in results:
                output += f"  {r['file']}:{r['line']}: {r['content']}\n"
            
            return output
            
        except Exception as e:
            return f"[Search Files] Error: {str(e)}"
    
    def _resolve_path(self, path: str) -> str:
        if os.path.isabs(path):
            return os.path.normpath(path)
        return os.path.normpath(os.path.join(self.workspace, path))
    
    def _is_safe_path(self, path: str) -> bool:
        try:
            real_path = os.path.realpath(path)
            real_workspace = os.path.realpath(self.workspace)
            return real_path.startswith(real_workspace)
        except:
            return False
    
    def _get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "search_term": {
                    "type": "string",
                    "description": "Text to search for within files"
                },
                "directory": {
                    "type": "string",
                    "description": "Directory to search in (default: current)",
                    "default": "."
                },
                "file_pattern": {
                    "type": "string",
                    "description": "File pattern to search (e.g., '*.py', '*.txt')",
                    "default": "*"
                }
            },
            "required": ["search_term"]
        }

"""
文件操作工具测试

覆盖 read_file / write_file / list_files / delete_file / search_files
"""
import pytest
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestReadFileTool:
    """ReadFileTool 测试"""

    @pytest.mark.asyncio
    async def test_read_existing_file(self, tmp_path):
        from tools.file_operations import ReadFileTool
        f = tmp_path / "hello.txt"
        f.write_text("Hello, World!")

        tool = ReadFileTool(workspace=str(tmp_path))
        result = await tool.execute(file_path="hello.txt")
        assert "Hello, World!" in result

    @pytest.mark.asyncio
    async def test_read_nonexistent_file(self, tmp_path):
        from tools.file_operations import ReadFileTool
        tool = ReadFileTool(workspace=str(tmp_path))
        result = await tool.execute(file_path="nope.txt")
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_read_directory_not_file(self, tmp_path):
        from tools.file_operations import ReadFileTool
        (tmp_path / "subdir").mkdir()
        tool = ReadFileTool(workspace=str(tmp_path))
        result = await tool.execute(file_path="subdir")
        assert "not a file" in result.lower()

    @pytest.mark.asyncio
    async def test_read_outside_workspace(self, tmp_path):
        from tools.file_operations import ReadFileTool
        tool = ReadFileTool(workspace=str(tmp_path / "safe"))
        (tmp_path / "safe").mkdir()
        result = await tool.execute(file_path="../../etc/passwd")
        assert "denied" in result.lower() or "outside" in result.lower()

    @pytest.mark.asyncio
    async def test_read_large_file_truncation(self, tmp_path):
        from tools.file_operations import ReadFileTool
        f = tmp_path / "big.txt"
        f.write_text("x" * 20000)
        tool = ReadFileTool(workspace=str(tmp_path))
        result = await tool.execute(file_path="big.txt")
        assert "truncated" in result.lower()

    @pytest.mark.asyncio
    async def test_read_custom_encoding(self, tmp_path):
        from tools.file_operations import ReadFileTool
        f = tmp_path / "encoded.txt"
        f.write_text("中文内容", encoding="utf-8")
        tool = ReadFileTool(workspace=str(tmp_path))
        result = await tool.execute(file_path="encoded.txt", encoding="utf-8")
        assert "中文内容" in result

    def test_schema_structure(self):
        from tools.file_operations import ReadFileTool
        tool = ReadFileTool()
        schema = tool.get_schema()
        assert schema["function"]["name"] == "read_file"
        assert "file_path" in schema["function"]["parameters"]["properties"]
        assert "file_path" in schema["function"]["parameters"]["required"]


class TestWriteFileTool:
    """WriteFileTool 测试"""

    @pytest.mark.asyncio
    async def test_write_new_file(self, tmp_path):
        from tools.file_operations import WriteFileTool
        tool = WriteFileTool(workspace=str(tmp_path))
        result = await tool.execute(file_path="new.txt", content="new content")
        assert "wrote" in result.lower()
        assert (tmp_path / "new.txt").read_text() == "new content"

    @pytest.mark.asyncio
    async def test_write_overwrite(self, tmp_path):
        from tools.file_operations import WriteFileTool
        (tmp_path / "exist.txt").write_text("old")
        tool = WriteFileTool(workspace=str(tmp_path))
        await tool.execute(file_path="exist.txt", content="new")
        assert (tmp_path / "exist.txt").read_text() == "new"

    @pytest.mark.asyncio
    async def test_write_append(self, tmp_path):
        from tools.file_operations import WriteFileTool
        (tmp_path / "append.txt").write_text("line1\n")
        tool = WriteFileTool(workspace=str(tmp_path))
        result = await tool.execute(file_path="append.txt", content="line2", mode="append")
        assert "appended" in result.lower()
        assert (tmp_path / "append.txt").read_text() == "line1\nline2"

    @pytest.mark.asyncio
    async def test_write_creates_directory(self, tmp_path):
        from tools.file_operations import WriteFileTool
        tool = WriteFileTool(workspace=str(tmp_path))
        await tool.execute(file_path="sub/deep/file.txt", content="deep")
        assert (tmp_path / "sub" / "deep" / "file.txt").read_text() == "deep"

    @pytest.mark.asyncio
    async def test_write_outside_workspace(self, tmp_path):
        from tools.file_operations import WriteFileTool
        tool = WriteFileTool(workspace=str(tmp_path / "safe"))
        (tmp_path / "safe").mkdir()
        result = await tool.execute(file_path="../../etc/evil", content="bad")
        assert "denied" in result.lower()

    def test_schema_structure(self):
        from tools.file_operations import WriteFileTool
        tool = WriteFileTool()
        schema = tool.get_schema()
        assert schema["function"]["name"] == "write_file"
        props = schema["function"]["parameters"]["properties"]
        assert "file_path" in props
        assert "content" in props
        assert "mode" in props


class TestListFilesTool:
    """ListFilesTool 测试"""

    @pytest.mark.asyncio
    async def test_list_directory(self, tmp_path):
        from tools.file_operations import ListFilesTool
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.py").write_text("b")
        (tmp_path / "sub").mkdir()

        tool = ListFilesTool(workspace=str(tmp_path))
        result = await tool.execute(directory=".")
        assert "a.txt" in result
        assert "b.py" in result
        assert "sub" in result

    @pytest.mark.asyncio
    async def test_list_with_pattern(self, tmp_path):
        from tools.file_operations import ListFilesTool
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.py").write_text("b")

        tool = ListFilesTool(workspace=str(tmp_path))
        result = await tool.execute(directory=".", pattern="*.py")
        assert "b.py" in result
        assert "a.txt" not in result

    @pytest.mark.asyncio
    async def test_list_nonexistent_directory(self, tmp_path):
        from tools.file_operations import ListFilesTool
        tool = ListFilesTool(workspace=str(tmp_path))
        result = await tool.execute(directory="nope")
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_list_empty_directory(self, tmp_path):
        from tools.file_operations import ListFilesTool
        (tmp_path / "empty").mkdir()
        tool = ListFilesTool(workspace=str(tmp_path))
        result = await tool.execute(directory="empty")
        assert "no files" in result.lower()

    @pytest.mark.asyncio
    async def test_list_recursive(self, tmp_path):
        from tools.file_operations import ListFilesTool
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "deep.txt").write_text("deep")

        tool = ListFilesTool(workspace=str(tmp_path))
        result = await tool.execute(directory=".", recursive=True)
        assert "deep.txt" in result

    @pytest.mark.asyncio
    async def test_list_outside_workspace(self, tmp_path):
        from tools.file_operations import ListFilesTool
        tool = ListFilesTool(workspace=str(tmp_path / "safe"))
        (tmp_path / "safe").mkdir()
        result = await tool.execute(directory="../../etc")
        assert "denied" in result.lower()

    @pytest.mark.asyncio
    async def test_list_uses_active_workspace_override(self, tmp_path):
        from tools.file_operations import ListFilesTool, reset_active_workspace, set_active_workspace

        fallback = tmp_path / "fallback"
        project = tmp_path / "project"
        fallback.mkdir()
        project.mkdir()
        (project / "README.md").write_text("hello")

        tool = ListFilesTool(workspace=str(fallback))
        token = set_active_workspace(str(project))
        try:
            result = await tool.execute(directory=".")
        finally:
            reset_active_workspace(token)

        assert "README.md" in result


class TestDeleteFileTool:
    """DeleteFileTool 测试"""

    @pytest.mark.asyncio
    async def test_delete_file(self, tmp_path):
        from tools.file_operations import DeleteFileTool
        f = tmp_path / "del.txt"
        f.write_text("bye")
        tool = DeleteFileTool(workspace=str(tmp_path))
        result = await tool.execute(file_path="del.txt")
        assert "deleted" in result.lower()
        assert not f.exists()

    @pytest.mark.asyncio
    async def test_delete_empty_directory(self, tmp_path):
        from tools.file_operations import DeleteFileTool
        (tmp_path / "empty_dir").mkdir()
        tool = DeleteFileTool(workspace=str(tmp_path))
        result = await tool.execute(file_path="empty_dir")
        assert "deleted" in result.lower()

    @pytest.mark.asyncio
    async def test_delete_nonempty_directory_no_force(self, tmp_path):
        from tools.file_operations import DeleteFileTool
        d = tmp_path / "nonempty"
        d.mkdir()
        (d / "file.txt").write_text("x")
        tool = DeleteFileTool(workspace=str(tmp_path))
        result = await tool.execute(file_path="nonempty", force=False)
        assert "not empty" in result.lower()

    @pytest.mark.asyncio
    async def test_delete_nonempty_directory_with_force(self, tmp_path):
        from tools.file_operations import DeleteFileTool
        d = tmp_path / "force_dir"
        d.mkdir()
        (d / "file.txt").write_text("x")
        tool = DeleteFileTool(workspace=str(tmp_path))
        result = await tool.execute(file_path="force_dir", force=True)
        assert "deleted" in result.lower()
        assert not d.exists()

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, tmp_path):
        from tools.file_operations import DeleteFileTool
        tool = DeleteFileTool(workspace=str(tmp_path))
        result = await tool.execute(file_path="ghost.txt")
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_delete_outside_workspace(self, tmp_path):
        from tools.file_operations import DeleteFileTool
        tool = DeleteFileTool(workspace=str(tmp_path / "safe"))
        (tmp_path / "safe").mkdir()
        result = await tool.execute(file_path="../../etc/passwd")
        assert "denied" in result.lower()


class TestSearchFilesTool:
    """SearchFilesTool 测试"""

    @pytest.mark.asyncio
    async def test_search_finds_matches(self, tmp_path):
        from tools.file_operations import SearchFilesTool
        (tmp_path / "a.py").write_text("import os\nprint('hello')\n")
        (tmp_path / "b.py").write_text("import sys\nprint('world')\n")

        tool = SearchFilesTool(workspace=str(tmp_path))
        result = await tool.execute(search_term="import")
        assert "a.py" in result
        assert "b.py" in result

    @pytest.mark.asyncio
    async def test_search_with_file_pattern(self, tmp_path):
        from tools.file_operations import SearchFilesTool
        (tmp_path / "a.py").write_text("hello python")
        (tmp_path / "b.txt").write_text("hello text")

        tool = SearchFilesTool(workspace=str(tmp_path))
        result = await tool.execute(search_term="hello", file_pattern="*.py")
        assert "a.py" in result
        assert "b.txt" not in result

    @pytest.mark.asyncio
    async def test_search_no_matches(self, tmp_path):
        from tools.file_operations import SearchFilesTool
        (tmp_path / "a.py").write_text("hello")
        tool = SearchFilesTool(workspace=str(tmp_path))
        result = await tool.execute(search_term="zzzznotfound")
        assert "no matches" in result.lower()

    @pytest.mark.asyncio
    async def test_search_case_insensitive(self, tmp_path):
        from tools.file_operations import SearchFilesTool
        (tmp_path / "a.py").write_text("Hello World")
        tool = SearchFilesTool(workspace=str(tmp_path))
        result = await tool.execute(search_term="hello")
        assert "a.py" in result

    @pytest.mark.asyncio
    async def test_search_returns_workspace_relative_paths_for_subdirectories(self, tmp_path):
        from tools.file_operations import SearchFilesTool

        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "PRD.md").write_text("### 已完成\n")

        tool = SearchFilesTool(workspace=str(tmp_path))
        result = await tool.execute(search_term="已完成", directory="docs", file_pattern="*.md")

        assert "docs/PRD.md:1:" in result
        assert "./PRD.md" not in result
        assert "\n  PRD.md:" not in result

    @pytest.mark.asyncio
    async def test_search_outside_workspace(self, tmp_path):
        from tools.file_operations import SearchFilesTool
        tool = SearchFilesTool(workspace=str(tmp_path / "safe"))
        (tmp_path / "safe").mkdir()
        result = await tool.execute(search_term="x", directory="../../etc")
        assert "denied" in result.lower()


class TestWorkspaceOverride:
    @pytest.mark.asyncio
    async def test_read_uses_active_workspace_override(self, tmp_path):
        from tools.file_operations import ReadFileTool, reset_active_workspace, set_active_workspace

        fallback = tmp_path / "fallback"
        project = tmp_path / "project"
        fallback.mkdir()
        project.mkdir()
        (project / "notes.md").write_text("project context")

        tool = ReadFileTool(workspace=str(fallback))
        token = set_active_workspace(str(project))
        try:
            result = await tool.execute(file_path="notes.md")
        finally:
            reset_active_workspace(token)

        assert "project context" in result

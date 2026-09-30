import os
import tempfile
import pytest
from arch_ai.tools import SystemTools, TOOL_MAP, TOOLS_SCHEMA

def test_system_tools_schema():
    assert len(TOOLS_SCHEMA) >= 12
    assert "read_file" in TOOL_MAP
    assert "write_file" in TOOL_MAP
    assert "patch_file" in TOOL_MAP
    assert "list_files" in TOOL_MAP
    assert "get_system_info" in TOOL_MAP
    assert "check_process" in TOOL_MAP
    assert "scan_gaming_system" in TOOL_MAP
    assert "optimize_gaming_system" in TOOL_MAP
    assert "execute_host_command" in TOOL_MAP
    assert "execute_code_in_sandbox" in TOOL_MAP
    assert "write_to_sandbox_file" in TOOL_MAP
    assert "generate_image" in TOOL_MAP
    assert "get_steam_game_compatibility" in TOOL_MAP
    assert "get_steam_library" in TOOL_MAP


def test_function_response_content_role():
    from google.genai import types
    part = types.Part.from_function_response(
        name="list_files",
        response={"result": "file1.txt\nfile2.txt"}
    )
    content = types.Content(role="user", parts=[part])
    assert content.role == "user"
    assert content.parts[0].function_response.name == "list_files"

@pytest.mark.asyncio
async def test_execute_host_command_safe():
    res = await SystemTools.execute_host_command("echo 'hello arch'")
    assert "Exit Code: 0" in res
    assert "hello arch" in res

@pytest.mark.asyncio
async def test_execute_host_command_blocked():
    res = await SystemTools.execute_host_command("rm -rf /tmp/test_dir")
    assert "Error: Command contains dangerous token" in res



def test_read_file_fewer_lines():
    with tempfile.NamedTemporaryFile(mode='w+', delete=False) as tmp:
        tmp.write("line 1\nline 2\n")
        tmp_path = tmp.name

    try:
        # Requesting 50 lines on a 2 line file should safely return the 2 lines without StopIteration error
        result = SystemTools.read_file(tmp_path, lines=50)
        assert "line 1\nline 2\n" == result
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

def test_read_file_non_existent():
    result = SystemTools.read_file("/non/existent/path/for/test_file.txt")
    assert "Error: File not found" in result

def test_write_and_patch_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        target = os.path.join(tmpdir, "subdir", "test.txt")
        # 1. Write file
        res = SystemTools.write_file(target, "Hello world!\nVersion: 1.0\n")
        assert "Successfully wrote" in res
        assert os.path.exists(target)

        # 2. Patch file
        res_patch = SystemTools.patch_file(target, "Version: 1.0", "Version: 2.0")
        assert "Successfully patched" in res_patch
        with open(target, "r") as f:
            content = f.read()
        assert "Version: 2.0" in content

        # 3. Patch missing target
        res_fail = SystemTools.patch_file(target, "non-existent", "fail")
        assert "Error: Target text block not found" in res_fail

        # 4. Block dangerous root path
        res_sec = SystemTools.write_file("/etc/shadow", "bad")
        assert "Error: Writing to protected system path" in res_sec

def test_scan_and_optimize_gaming_system_tool():
    report = SystemTools.scan_gaming_system()
    assert "Arch Linux Gaming System Diagnostic Report" in report
    assert "Overall Gaming Score:" in report

    opt_report = SystemTools.optimize_gaming_system()
    assert "Gaming Optimization Report:" in opt_report

def test_list_files():
    result = SystemTools.list_files(".")
    assert "pyproject.toml" in result
    assert "src" in result

def test_list_files_invalid_dir():
    result = SystemTools.list_files("/non/existent/dir/xyz_123")
    assert "Error: Directory not found" in result

def test_get_system_info():
    info = SystemTools.get_system_info()
    assert "os:" in info
    assert "memory_total_gb:" in info
    assert "cpu_count:" in info

def test_check_process():
    # Look for a common process like python or pytest
    res = SystemTools.check_process("pytest")
    assert "PID:" in res or "No process found" in res

def test_analyze_gaming_session_tool():
    report = SystemTools.analyze_gaming_session()
    assert "Live Gaming Session Forensic Report" in report
    assert "Display & System Configuration:" in report

@pytest.mark.asyncio
async def test_execute_host_command_timeout():
    import time
    t0 = time.time()
    res = await SystemTools.execute_host_command("sleep 5", timeout=1)
    duration = time.time() - t0
    assert "timed out" in res.lower()
    assert duration < 3.0  # Must terminate promptly

def test_execute_code_in_sandbox_is_async():
    import inspect
    assert inspect.iscoroutinefunction(SystemTools.execute_code_in_sandbox)
    assert inspect.iscoroutinefunction(SystemTools.execute_sandbox_command)

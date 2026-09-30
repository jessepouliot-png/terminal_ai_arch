import pytest
import signal
import sys
from unittest.mock import patch, MagicMock
from sandbox import (
    SandboxManager,
    _active_sandboxes,
    _cleanup_all_sandboxes,
    setup_signal_handlers,
)

def test_sandbox_initialization():
    sm = SandboxManager()
    assert sm.cwd == "/workspace"
    assert sm.prev_cwd == "/workspace"
    assert sm.env == {}
    assert not sm.is_active

def test_sandbox_handle_export():
    sm = SandboxManager()
    
    # Test valid export
    assert sm.handle_export("export FOO=BAR")
    assert sm.env.get("FOO") == "BAR"

    # Test export with quotes
    assert sm.handle_export('export TEST_VAR="hello world"')
    assert sm.env.get("TEST_VAR") == "hello world"

    # Test build env args
    args = sm._build_env_args()
    assert "-e" in args
    assert "FOO=BAR" in args
    assert "TEST_VAR=hello world" in args

    # Test unset
    assert sm.handle_export("unset FOO")
    assert "FOO" not in sm.env

    # Non-export command
    assert not sm.handle_export("ls -la")

def test_sandbox_handle_cd_non_cd():
    sm = SandboxManager()
    is_cd, err = sm.handle_cd("pwd")
    assert not is_cd
    assert err is None

def test_sandbox_stop_when_active():
    sm = SandboxManager()
    sm.is_active = True
    _active_sandboxes.add(sm)

    with patch("subprocess.run") as mock_run:
        sm.stop()
        assert not sm.is_active
        assert sm not in _active_sandboxes
        mock_run.assert_called_once_with(
            ["docker", "stop", "-t", "1", sm.container_name],
            stdout=subprocess_devnull(),
            stderr=subprocess_devnull(),
            timeout=3,
        )

def test_sandbox_stop_fallback_to_rm_on_error():
    sm = SandboxManager()
    sm.is_active = True
    _active_sandboxes.add(sm)

    with patch("subprocess.run", side_effect=[Exception("stop failed"), MagicMock()]) as mock_run:
        sm.stop()
        assert not sm.is_active
        assert sm not in _active_sandboxes
        assert mock_run.call_count == 2
        mock_run.assert_called_with(
            ["docker", "rm", "-f", sm.container_name],
            stdout=subprocess_devnull(),
            stderr=subprocess_devnull(),
            timeout=3,
        )

def test_cleanup_all_sandboxes():
    sm1 = SandboxManager()
    sm1.is_active = True
    _active_sandboxes.add(sm1)

    sm2 = SandboxManager()
    sm2.is_active = True
    _active_sandboxes.add(sm2)

    with patch("subprocess.run"):
        _cleanup_all_sandboxes()
        assert not sm1.is_active
        assert not sm2.is_active
        assert len(_active_sandboxes) == 0

def test_signal_handling_cleans_up_sandboxes():
    sm = SandboxManager()
    sm.is_active = True
    _active_sandboxes.add(sm)

    with patch("subprocess.run"), pytest.raises(SystemExit) as exc_info:
        # Simulate signal handler invocation for SIGHUP
        sighup = getattr(signal, "SIGHUP", signal.SIGTERM)
        handler = signal.getsignal(sighup)
        assert callable(handler)
        handler(sighup, None)

    assert exc_info.value.code == 128 + sighup
    assert not sm.is_active
    assert sm not in _active_sandboxes

def test_cleanup_orphaned_containers():
    with patch("subprocess.run") as mock_run:
        # Mock docker ps returning 2 container IDs
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="cid1\ncid2\n"),
            MagicMock(returncode=0),
            MagicMock(returncode=0),
        ]
        SandboxManager.cleanup_orphaned_containers()
        assert mock_run.call_count == 3
        mock_run.assert_any_call(
            ["docker", "rm", "-f", "cid1"],
            stdout=subprocess_devnull(),
            stderr=subprocess_devnull(),
            timeout=5,
        )
        mock_run.assert_any_call(
            ["docker", "rm", "-f", "cid2"],
            stdout=subprocess_devnull(),
            stderr=subprocess_devnull(),
            timeout=5,
        )

def subprocess_devnull():
    import subprocess
    return subprocess.DEVNULL

def test_sandbox_get_status():
    sm = SandboxManager()
    status = sm.get_status()
    assert "is_active" in status
    assert "engine" in status
    assert "pids_limit" in status
    assert "mem_limit" in status
    assert status["is_active"] is False

def test_sandbox_engine_health_check_failure():
    sm = SandboxManager(engine="nonexistent_engine_xyz")
    healthy, err = sm.check_engine_health()
    assert not healthy
    assert "not found in PATH" in err

def test_sandbox_engine_health_check_daemon_down():
    sm = SandboxManager(engine="docker")
    with patch("shutil.which", return_value="/usr/bin/docker"), patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr="connect: connection refused", stdout="")
        healthy, err = sm.check_engine_health()
        assert not healthy
        assert "Cannot connect to docker daemon" in err

def test_sandbox_handle_cd_compound_passthrough():
    sm = SandboxManager()
    is_cd, err = sm.handle_cd("cd /tmp && ls -la")
    assert not is_cd
    assert err is None

def test_sandbox_handle_cd_with_spaces_inactive():
    sm = SandboxManager()
    is_cd, err = sm.handle_cd('cd "/custom path/with spaces"')
    assert is_cd
    assert err is None
    assert sm.cwd == "/custom path/with spaces"

def test_sandbox_read_file_inactive():
    sm = SandboxManager()
    res = sm.read_file("some_file.txt")
    assert "Sandbox is not active" in res

@pytest.mark.asyncio
async def test_sandbox_execute_async():
    sm = SandboxManager()
    sm.is_active = True
    _active_sandboxes.add(sm)
    try:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="hello", stderr="")
            rc, out = await sm.execute_async("echo hello")
            assert rc == 0
            assert "hello" in out
    finally:
        sm.is_active = False
        _active_sandboxes.discard(sm)

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from arch_ai import clipboard_utils


def test_extract_commands_from_fenced_code():
    text = """
Here are the steps to fix the issue:
```bash
sudo pacman -Syu --noconfirm
```
And then restart:
```sh
systemctl restart NetworkManager
```
    """
    cmds = clipboard_utils.extract_commands_from_text(text)
    assert len(cmds) == 2
    assert cmds[0] == "sudo pacman -Syu --noconfirm"
    assert cmds[1] == "systemctl restart NetworkManager"
    assert clipboard_utils.extract_primary_command(text) == "sudo pacman -Syu --noconfirm"


def test_extract_commands_from_generic_code_block():
    text = """
Run the following:
```
git status
git diff
```
    """
    cmds = clipboard_utils.extract_commands_from_text(text)
    assert len(cmds) == 1
    assert "git status" in cmds[0]
    assert "git diff" in cmds[0]


def test_extract_commands_from_inline_code():
    text = "You can update your system using `sudo pacman -Syyu` or check `systemctl status`."
    cmds = clipboard_utils.extract_commands_from_text(text)
    assert len(cmds) == 2
    assert "sudo pacman -Syyu" in cmds
    assert "systemctl status" in cmds


def test_extract_empty_or_no_commands():
    assert clipboard_utils.extract_commands_from_text("") == []
    assert clipboard_utils.extract_commands_from_text(None) == []
    assert clipboard_utils.extract_primary_command("") is None
    assert clipboard_utils.extract_commands_from_text("This has no code at all.") == []


def test_copy_to_clipboard_none():
    assert clipboard_utils.copy_to_clipboard(None) is False


def test_emit_osc52():
    res = clipboard_utils.emit_osc52("test command")
    assert res is True
    assert clipboard_utils.emit_osc52("") is False


def test_copy_to_clipboard_real():
    res = clipboard_utils.copy_to_clipboard("echo 'arch-terminal clipboard test'")
    assert res is True
    # If wl-paste is available, verify roundtrip
    read_text = clipboard_utils.get_clipboard_text()
    if read_text is not None:
        assert "echo 'arch-terminal clipboard test'" in read_text


def test_copy_to_clipboard_fallbacks():
    # Test when wl-copy, xclip, and xsel do not exist, OSC 52 still succeeds
    with patch("shutil.which", return_value=None):
        res = clipboard_utils.copy_to_clipboard("test fallback")
        assert res is True


@pytest.mark.asyncio
async def test_terminal_clean_prompt_and_native_mouse():
    with patch("google.genai.Client"), patch("arch_ai.agent_terminal.Config.validate"):
        from arch_ai.agent_terminal import AITerminal

        term = AITerminal()

        # Mouse support must be False to allow unrestricted native terminal text/code selection and copying
        assert term.session.mouse_support is False

        # Get prompt and verify it is clean without any copy buttons, suggestions, or spacing issues
        prompt_ft = term._get_prompt()
        prompt_text = "".join([piece[1] for piece in prompt_ft])
        assert "[📋 Copy]" not in prompt_text
        assert "[✓ Copied!]" not in prompt_text
        assert "💡" not in prompt_text
        assert "\n" not in prompt_text
        assert "❯" in prompt_text
        assert "Hold Shift" not in str(term.session.bottom_toolbar())

        # Verify auto-suggestions enabled
        assert term.session.auto_suggest is not None


@pytest.mark.asyncio
async def test_terminal_advanced_features():
    with (
        patch("google.genai.Client") as mock_client,
        patch("arch_ai.agent_terminal.Config.validate"),
    ):
        mock_instance = MagicMock()
        mock_client.return_value = mock_instance
        from arch_ai.agent_terminal import AITerminal, _BASE_COMMANDS, _GAMING_SUBS

        term = AITerminal()

        # 1. Base commands check
        assert "/audit" in _BASE_COMMANDS
        assert "scan" in _GAMING_SUBS
        assert "optimize" in _GAMING_SUBS

        # 2. Audit health check
        await term.audit_system_health()

        # 3. Natural language command synthesis
        mock_resp = MagicMock()
        mock_resp.text = "ls -la"
        mock_instance.aio.models.generate_content = (
            AsyncMock(return_value=mock_resp) if hasattr(pytest, "mark") else None
        )
        with patch.object(mock_instance.aio.models, "generate_content", return_value=mock_resp):
            with patch.object(term.session, "prompt_async", return_value="n"):
                await term.synthesize_nl_command("list all files detailed")

        # 4. _ensure_sandbox_active helper check
        with patch("arch_ai.agent_terminal.sandbox_manager.is_active", True):
            assert term._ensure_sandbox_active() is True

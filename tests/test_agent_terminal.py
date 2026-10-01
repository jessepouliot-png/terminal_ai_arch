import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from google.genai import types
from arch_ai.agent_terminal import AITerminal


async def _make_stream(items):
    """Adapter to mock Gemini response streaming chunk iterators."""
    for item in items:
        yield item


@pytest.fixture
def mock_terminal():
    with (
        patch("arch_ai.agent_terminal.genai.Client"),
        patch("arch_ai.agent_terminal.Config.validate"),
    ):
        terminal = AITerminal()
        terminal.client = MagicMock()
        terminal.client.aio = MagicMock()
        terminal.client.aio.models = MagicMock()

        # Configure streaming generator and scalar fallbacks
        terminal.client.aio.models.generate_content_stream = AsyncMock(
            return_value=_make_stream([])
        )
        terminal.client.aio.models.generate_content = AsyncMock()

        terminal.analyzer = MagicMock()
        terminal.analyzer.knowledge = MagicMock()
        terminal.analyzer.knowledge.search = AsyncMock(return_value=[])
        terminal.analyzer.get_chat_history = AsyncMock(
            return_value=[
                {"user": "valid user", "agent": "valid agent"},
                {"user": "empty agent", "agent": ""},
                {"user": "", "agent": "empty user"},
            ]
        )
        terminal.analyzer.log_chat = AsyncMock()
        terminal.analyzer.telemetry = MagicMock()
        terminal.analyzer.telemetry.log_metric = AsyncMock()
        terminal.console = MagicMock()
        return terminal


@pytest.mark.asyncio
async def test_query_gemini_filters_empty_history(mock_terminal):
    mock_candidate = MagicMock()
    mock_candidate.grounding_metadata = None
    mock_candidate.content = types.Content(role="model", parts=[types.Part(text="Hello response")])

    mock_response = MagicMock()
    mock_response.candidates = [mock_candidate]
    mock_response.text = "Hello response"

    mock_terminal._execute_gemini_turn_stream = AsyncMock(
        return_value=_make_stream([mock_response])
    )

    await mock_terminal.query_gemini("Test prompt")

    assert mock_terminal._execute_gemini_turn_stream.called
    call_args = mock_terminal._execute_gemini_turn_stream.call_args[0]
    contents = call_args[0]

    assert len(contents) == 3
    assert contents[0].role == "user"
    assert contents[0].parts[0].text == "valid user"
    assert contents[1].role == "model"
    assert contents[1].parts[0].text == "valid agent"
    assert contents[2].role == "user"
    assert "Test prompt" in contents[2].parts[0].text


@pytest.mark.asyncio
async def test_query_gemini_fallback_on_tool_iteration_limit(mock_terminal):
    fc = types.FunctionCall(name="get_system_info", args={})
    fc_part = types.Part(function_call=fc)
    fc_candidate = MagicMock()
    fc_candidate.grounding_metadata = None
    fc_candidate.content = types.Content(role="model", parts=[fc_part])
    mock_tool_response = MagicMock()
    mock_tool_response.candidates = [fc_candidate]
    mock_tool_response.text = None

    text_part = types.Part(text="Synthesized final answer")
    final_candidate = MagicMock()
    final_candidate.grounding_metadata = None
    final_candidate.content = types.Content(role="model", parts=[text_part])
    mock_final_response = MagicMock()
    mock_final_response.candidates = [final_candidate]
    mock_final_response.text = "Synthesized final answer"

    mock_terminal.client.aio.models.generate_content_stream = AsyncMock(
        return_value=_make_stream([mock_final_response])
    )

    with patch.object(
        mock_terminal,
        "_execute_gemini_turn_stream",
        side_effect=[_make_stream([mock_tool_response]) for _ in range(10)],
    ):
        await mock_terminal.query_gemini("Diagnose this issue")

        assert mock_terminal.client.aio.models.generate_content_stream.called
        fallback_call_kwargs = (
            mock_terminal.client.aio.models.generate_content_stream.call_args.kwargs
        )
        cfg = fallback_call_kwargs["config"]
        assert cfg.tool_config.function_calling_config.mode == types.FunctionCallingConfigMode.NONE
        mock_terminal.analyzer.log_chat.assert_called_once_with(
            "Diagnose this issue", "Synthesized final answer"
        )


@pytest.mark.asyncio
async def test_query_gemini_handles_closing_tags_in_tool_args(mock_terminal):
    from rich.console import Console

    mock_terminal.console = Console()
    fc = types.FunctionCall(
        name="execute_host_command",
        args={"command": 'grep -i "[/Script/Engine.GameUserSettings]" file.ini'},
    )
    fc_part = types.Part(function_call=fc)
    fc_candidate = MagicMock()
    fc_candidate.grounding_metadata = None
    fc_candidate.content = types.Content(role="model", parts=[fc_part])
    mock_tool_response = MagicMock()
    mock_tool_response.candidates = [fc_candidate]
    mock_tool_response.text = None

    text_part = types.Part(text="Done checking settings.")
    final_candidate = MagicMock()
    final_candidate.grounding_metadata = None
    final_candidate.content = types.Content(role="model", parts=[text_part])
    mock_final_response = MagicMock()
    mock_final_response.candidates = [final_candidate]
    mock_final_response.text = "Done checking settings."

    mock_terminal._execute_gemini_turn_stream = AsyncMock(
        side_effect=[_make_stream([mock_tool_response]), _make_stream([mock_final_response])]
    )

    await mock_terminal.query_gemini("Check game user settings")
    mock_terminal.analyzer.log_chat.assert_called_once_with(
        "Check game user settings", "Done checking settings."
    )


@pytest.mark.asyncio
async def test_query_gemini_preserves_text_accompanying_function_call(mock_terminal):
    intro_text = types.Part(text="Checking running processes on the system...")
    fc = types.FunctionCall(name="check_process", args={"process_name": "docker"})
    fc_part = types.Part(function_call=fc)
    cand1 = types.Candidate(content=types.Content(role="model", parts=[intro_text, fc_part]))
    resp1 = types.GenerateContentResponse(candidates=[cand1])

    final_text = types.Part(text="Docker daemon is active and running.")
    cand2 = types.Candidate(content=types.Content(role="model", parts=[final_text]))
    resp2 = types.GenerateContentResponse(candidates=[cand2])

    mock_terminal._execute_gemini_turn_stream = AsyncMock(
        side_effect=[_make_stream([resp1]), _make_stream([resp2])]
    )

    with patch.dict(
        "arch_ai.agent_terminal.TOOL_MAP",
        {"check_process": MagicMock(return_value="Process docker is running")},
    ):
        await mock_terminal.query_gemini("Is docker running?")

    assert mock_terminal.analyzer.log_chat.called
    logged_prompt, logged_reply = mock_terminal.analyzer.log_chat.call_args[0]
    assert logged_prompt == "Is docker running?"
    assert "Checking running processes on the system..." in logged_reply
    assert "Docker daemon is active and running." in logged_reply


@pytest.mark.asyncio
async def test_query_gemini_no_warning_with_real_genai_response(mock_terminal, caplog):
    fc = types.FunctionCall(name="get_system_info", args={})
    cand1 = types.Candidate(
        content=types.Content(role="model", parts=[types.Part(function_call=fc)])
    )
    resp1 = types.GenerateContentResponse(candidates=[cand1])

    final_cand = types.Candidate(
        content=types.Content(role="model", parts=[types.Part(text="System is Arch Linux.")])
    )
    resp2 = types.GenerateContentResponse(candidates=[final_cand])

    mock_terminal._execute_gemini_turn_stream = AsyncMock(
        side_effect=[_make_stream([resp1]), _make_stream([resp2])]
    )

    with patch.dict(
        "arch_ai.agent_terminal.TOOL_MAP",
        {"get_system_info": MagicMock(return_value="Arch Linux x86_64")},
    ):
        await mock_terminal.query_gemini("What system is this?")

    assert "Warning: there are non-text parts in the response" not in caplog.text
    mock_terminal.analyzer.log_chat.assert_called_once_with(
        "What system is this?", "System is Arch Linux."
    )


@pytest.mark.asyncio
async def test_query_gemini_executes_tool_if_synthesis_requests_action(mock_terminal):
    fc_loop = types.FunctionCall(name="get_system_info", args={})
    loop_cand = types.Candidate(
        content=types.Content(role="model", parts=[types.Part(function_call=fc_loop)])
    )
    mock_loop_resp = types.GenerateContentResponse(candidates=[loop_cand])

    final_cand = types.Candidate(
        content=types.Content(role="model", parts=[types.Part(text="Kernel is 7.2.6-arch1-2.")])
    )
    mock_synth_resp = types.GenerateContentResponse(candidates=[final_cand])

    mock_terminal._execute_gemini_turn_stream = AsyncMock(
        side_effect=[_make_stream([mock_loop_resp]) for _ in range(10)]
    )
    mock_terminal.client.aio.models.generate_content_stream = AsyncMock(
        return_value=_make_stream([mock_synth_resp])
    )

    executed_tools = []

    def fake_sys_info():
        executed_tools.append("get_system_info")
        return "Arch Linux x86_64"

    with patch.dict("arch_ai.agent_terminal.TOOL_MAP", {"get_system_info": fake_sys_info}):
        await mock_terminal.query_gemini("Check kernel version")

    assert "get_system_info" in executed_tools
    mock_terminal.analyzer.log_chat.assert_called_once_with(
        "Check kernel version", "Kernel is 7.2.6-arch1-2."
    )


@pytest.mark.asyncio
async def test_run_cli_command_troubleshooter_executes_fix(mock_terminal):
    mock_terminal.analyzer.async_classify_risk = AsyncMock(return_value=("low", "Safe"))
    mock_terminal.analyzer.log_command = AsyncMock()
    mock_terminal.troubleshooter = MagicMock()
    mock_terminal.troubleshooter.troubleshoot = AsyncMock(return_value="sudo pacman -S missing-pkg")
    mock_terminal.session = MagicMock()
    mock_terminal.session.prompt_async = AsyncMock(side_effect=["y", "y"])

    # Simulate command failure followed by fix execution success
    mock_terminal._execute_command_with_capture = AsyncMock(
        side_effect=[(127, "command not found"), (0, "Installed successfully")]
    )

    await mock_terminal.run_cli_command("broken-command")

    mock_terminal.troubleshooter.troubleshoot.assert_called_once()
    assert mock_terminal._execute_command_with_capture.call_count == 2
    assert (
        mock_terminal._execute_command_with_capture.call_args_list[1][0][0]
        == "sudo pacman -S missing-pkg"
    )


@pytest.mark.asyncio
async def test_query_gemini_fallback_synthesizes_from_executed_tools(mock_terminal):
    fc_loop = types.FunctionCall(name="get_system_info", args={})
    loop_cand = types.Candidate(
        content=types.Content(role="model", parts=[types.Part(function_call=fc_loop)])
    )
    mock_loop_resp = types.GenerateContentResponse(candidates=[loop_cand])

    # Return empty stream to trigger fallback synthesis
    mock_terminal.client.aio.models.generate_content_stream = AsyncMock(
        return_value=_make_stream([])
    )

    retry_cand = types.Candidate(
        content=types.Content(
            role="model", parts=[types.Part(text="Report based on executed tool findings.")]
        )
    )
    mock_retry_resp = types.GenerateContentResponse(candidates=[retry_cand])
    mock_terminal.client.aio.models.generate_content = AsyncMock(return_value=mock_retry_resp)

    with (
        patch.object(
            mock_terminal,
            "_execute_gemini_turn_stream",
            side_effect=[_make_stream([mock_loop_resp]) for _ in range(10)],
        ),
        patch.dict(
            "arch_ai.agent_terminal.TOOL_MAP",
            {"get_system_info": MagicMock(return_value="Arch Linux x86_64")},
        ),
    ):
        await mock_terminal.query_gemini("Scan my system")

    assert mock_terminal.analyzer.log_chat.called
    logged_prompt, logged_reply = mock_terminal.analyzer.log_chat.call_args[0]
    assert logged_prompt == "Scan my system"
    assert "no written analysis was generated" not in logged_reply
    assert "Report based on executed tool findings." in logged_reply

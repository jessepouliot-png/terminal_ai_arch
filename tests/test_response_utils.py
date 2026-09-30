import pytest
from unittest.mock import MagicMock
from google.genai import types

from response_utils import (
    extract_parts_from_response,
    extract_function_calls,
    extract_full_model_response
)


def test_extract_response_with_text_and_function_call(caplog):
    """
    Verifies that a response with both text and function call parts extracts full text
    and function calls without triggering the SDK non-text parts warning.
    """
    text_part = types.Part(text="Analyzing your system processes...")
    fc = types.FunctionCall(name="check_process", args={"process_name": "docker"})
    fc_part = types.Part(function_call=fc)
    content = types.Content(role="model", parts=[text_part, fc_part])
    cand = types.Candidate(content=content)
    resp = types.GenerateContentResponse(candidates=[cand])

    # 1. Verify extract_parts_from_response
    parts = extract_parts_from_response(resp)
    assert len(parts) == 2

    # 2. Verify extract_function_calls
    fcs = extract_function_calls(resp)
    assert len(fcs) == 1
    assert fcs[0].name == "check_process"
    assert fcs[0].args["process_name"] == "docker"

    # 3. Verify extract_full_model_response (text only)
    text = extract_full_model_response(resp, include_function_calls=False)
    assert text == "Analyzing your system processes..."

    # 4. Verify extract_full_model_response (with function call description)
    full = extract_full_model_response(resp, include_function_calls=True)
    assert "Analyzing your system processes..." in full
    assert "check_process" in full

    # Ensure the SDK warning was NOT logged
    assert "Warning: there are non-text parts in the response" not in caplog.text


def test_extract_response_only_function_call(caplog):
    """
    Verifies that a response with only a function call returns the function call details
    when include_function_calls=True without warning.
    """
    fc = types.FunctionCall(name="list_files", args={"directory": "/home"})
    fc_part = types.Part(function_call=fc)
    content = types.Content(role="model", parts=[fc_part])
    cand = types.Candidate(content=content)
    resp = types.GenerateContentResponse(candidates=[cand])

    full = extract_full_model_response(resp, include_function_calls=True)
    assert "list_files" in full
    assert "/home" in full
    assert "Warning: there are non-text parts in the response" not in caplog.text


def test_extract_response_filters_thoughts():
    """
    Verifies that thinking/reasoning parts are filtered out when regular text is present.
    """
    thought_part = types.Part(thought=True, text="I need to check the disk space.")
    text_part = types.Part(text="Disk usage is currently at 45%.")
    content = types.Content(role="model", parts=[thought_part, text_part])
    cand = types.Candidate(content=content)
    resp = types.GenerateContentResponse(candidates=[cand])

    res = extract_full_model_response(resp)
    assert res == "Disk usage is currently at 45%."


def test_extract_response_with_magicmock_text():
    """
    Verifies compatibility with tests that mock response.text directly without candidates.
    """
    mock_resp = MagicMock()
    mock_resp.text = "ls -la"
    del mock_resp.candidates
    assert extract_full_model_response(mock_resp) == "ls -la"


def test_extract_response_with_empty_candidates():
    """
    Verifies compatibility when candidates is empty list.
    """
    mock_resp = MagicMock()
    mock_resp.text = "fallback text"
    mock_resp.candidates = []
    assert extract_full_model_response(mock_resp) == "fallback text"

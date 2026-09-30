"""
Utilities for safely extracting full model responses from Google GenAI SDK
GenerateContentResponse, Candidate, and streaming chunks.

Directly accessing `response.text` triggers a warning when the response contains
non-text parts such as `function_call`:
"Warning: there are non-text parts in the response: ['function_call'], returning
concatenated text result from text parts. Check the full candidates.content.parts
accessor to get the full model response."

This module inspects `candidates.content.parts` to ensure the complete response
is captured without warnings and without discarding tool calls or intermediate text.
"""

from typing import Any, List, Optional


def extract_parts_from_response(response: Any) -> List[Any]:
    """Safely retrieves the list of parts from a GenerateContentResponse,
    Candidate, Content, or streaming chunk without calling `.text` or triggering SDK warnings.
    """
    if response is None:
        return []

    # 1. Check if response has candidates list
    candidates = getattr(response, "candidates", None)
    if candidates and isinstance(candidates, (list, tuple)) and len(candidates) > 0:
        cand = candidates[0]
        content = getattr(cand, "content", None)
        if content:
            parts = getattr(content, "parts", None)
            if parts and isinstance(parts, (list, tuple)):
                return list(parts)
        parts = getattr(cand, "parts", None)
        if parts and isinstance(parts, (list, tuple)):
            return list(parts)

    # 2. Check if response has content.parts directly
    content = getattr(response, "content", None)
    if content:
        parts = getattr(content, "parts", None)
        if parts and isinstance(parts, (list, tuple)):
            return list(parts)

    # 3. Check if response has parts directly
    parts = getattr(response, "parts", None)
    if parts and isinstance(parts, (list, tuple)):
        return list(parts)

    return []


def extract_function_calls(response: Any) -> List[Any]:
    """Extracts all FunctionCall objects from response candidates.content.parts."""
    parts = extract_parts_from_response(response)
    return [p.function_call for p in parts if getattr(p, "function_call", None)]


def extract_full_model_response(response: Any, include_function_calls: bool = False) -> str:
    """Extracts the full text and model output from a response, chunk, or candidate
    by inspecting `candidates.content.parts` directly.

    Avoids accessing `response.text` when non-text parts (like function_call) exist,
    preventing SDK warnings and ensuring full response coverage.

    Args:
        response: The model response, candidate, or streaming chunk.
        include_function_calls: If True, formats requested function calls into readable
            markdown text when function calls are present.

    Returns:
        The extracted response string.
    """
    parts = extract_parts_from_response(response)
    if parts:
        # Collect regular text parts (excluding thoughts)
        text_parts = [
            p.text for p in parts
            if getattr(p, "text", None) and not getattr(p, "thought", False)
        ]

        # If no non-thought text was found, check if there's any text at all
        if not text_parts:
            text_parts = [p.text for p in parts if getattr(p, "text", None)]

        full_text = "".join(text_parts).strip()

        # Check for code execution parts if present
        code_parts = []
        for p in parts:
            if getattr(p, "executable_code", None) and getattr(p.executable_code, "code", None):
                code_parts.append(f"```python\n{p.executable_code.code}\n```")
            if getattr(p, "code_execution_result", None) and getattr(p.code_execution_result, "output", None):
                code_parts.append(f"```output\n{p.code_execution_result.output}\n```")

        if code_parts:
            code_text = "\n\n".join(code_parts)
            full_text = f"{full_text}\n\n{code_text}".strip() if full_text else code_text

        # If requested, include readable descriptions of function calls
        if include_function_calls:
            fc_parts = [p.function_call for p in parts if getattr(p, "function_call", None)]
            if fc_parts:
                fc_lines = []
                for fc in fc_parts:
                    args_dict = dict(fc.args) if getattr(fc, "args", None) else {}
                    fc_lines.append(f"- `{fc.name}({args_dict})`")
                fc_text = "\n".join(fc_lines)
                full_text = f"{full_text}\n\n{fc_text}".strip() if full_text else fc_text

        if full_text:
            return full_text

    # Fallback for mock objects or objects without parts where text was set as an attribute directly
    cls = type(response)
    text_attr = getattr(cls, "text", None)
    if not isinstance(text_attr, property):
        val = getattr(response, "text", None)
        if isinstance(val, str):
            return val

    return ""

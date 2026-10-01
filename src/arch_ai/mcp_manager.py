from contextlib import AsyncExitStack
import logging

try:
    from mcp import StdioServerParameters
    from mcp.client.stdio import stdio_client
    import mcp.types

    # Monkey patch for google.genai SDK bug with mcp>=2.0
    if not hasattr(mcp.types.Tool, "inputSchema"):
        mcp.types.Tool.inputSchema = property(lambda self: self.input_schema)

    # Monkey patch for google.genai SDK array items requirement
    import google.genai._mcp_utils as genai_mcp

    _orig_filter = genai_mcp._filter_to_supported_schema

    def _patched_filter(schema):
        if isinstance(schema, dict):
            if "anyOf" in schema:
                schema["any_of"] = schema.pop("anyOf")
            if schema.get("type") == "array":
                if "items" not in schema or not schema["items"]:
                    schema["items"] = {"type": "string"}
        return _orig_filter(schema)

    genai_mcp._filter_to_supported_schema = _patched_filter

except ImportError:
    pass

log = logging.getLogger("agent_terminal.mcp")


class MCPManager:
    def __init__(self):
        self.sessions = []
        self.exit_stack = AsyncExitStack()
        self.server_params_list = []
        self.tool_map = {}
        self.gemini_tools = []

    def add_server(self, command: str, args: list[str]):
        import os

        env = os.environ.copy()
        self.server_params_list.append(StdioServerParameters(command=command, args=args, env=env))

    async def initialize(self):
        try:
            from mcp import ClientSession
        except ImportError:
            log.warning("mcp package not installed. Skipping MCP initialization.")
            return

        import google.genai._mcp_utils as genai_mcp

        for params in self.server_params_list:
            try:
                stdio_transport = await self.exit_stack.enter_async_context(stdio_client(params))
                read, write = stdio_transport
                session = await self.exit_stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                self.sessions.append(session)

                # Fetch tools to map them to this session
                tools_result = await session.list_tools()
                for t in tools_result.tools:
                    orig_name = t.name
                    mapped_name = orig_name
                    counter = 2
                    while mapped_name in self.tool_map:
                        mapped_name = f"{orig_name}_{counter}"
                        counter += 1

                    self.tool_map[mapped_name] = (session, orig_name)

                    try:
                        g_tool = genai_mcp.mcp_to_gemini_tool(t)
                        g_tool.function_declarations[0].name = mapped_name
                        self.gemini_tools.append(g_tool)
                    except Exception as e:
                        log.warning(
                            f"Could not convert MCP tool {orig_name} to Gemini tool format: {e}"
                        )
            except Exception as e:
                log.error(f"Failed to initialize MCP server {params.command}: {e}")

    async def call_tool(self, name: str, args: dict):
        if name not in self.tool_map:
            raise ValueError(f"Tool {name} not found in any MCP session.")

        session_info = self.tool_map[name]
        if isinstance(session_info, tuple):
            session, orig_name = session_info
        else:
            session = session_info
            orig_name = name

        result = await session.call_tool(orig_name, arguments=args)
        return result

    async def cleanup(self):
        await self.exit_stack.aclose()

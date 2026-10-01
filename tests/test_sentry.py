import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def run():
    params = StdioServerParameters(
        command=".venv/bin/uvx",
        args=[
            "--with",
            "httpx",
            "--with",
            "mcp<2",
            "mcp-server-sentry",
            "--auth-token",
            "fake_token",
        ],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            for t in tools.tools:
                print(f"Tool: {t.name}")


asyncio.run(run())

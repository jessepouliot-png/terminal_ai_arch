import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import os


async def run():
    params = StdioServerParameters(
        command="npx", args=["-y", "steam-games-mcp"], env=os.environ.copy()
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            for t in tools.tools:
                print(f"Tool: {t.name}")


asyncio.run(run())

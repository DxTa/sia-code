import json
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


@pytest.mark.anyio
async def test_stdio_server_lists_tools_and_calls_embed_status():
    repo_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{repo_root}:{env.get('PYTHONPATH', '')}"

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "sia_code.mcp"],
        env=env,
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            tool_names = {tool.name for tool in tools.tools}
            assert "status" in tool_names
            assert "memory_working_set" in tool_names
            assert "embed_status" in tool_names

            response = await session.call_tool("embed_status", {})
            payload = json.loads(response.content[0].text)
            assert payload["ok"] is True
            assert payload["scope"] == "machine"

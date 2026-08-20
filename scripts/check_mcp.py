"""Verify an already-running TRM MCP endpoint without performing writes."""

import asyncio
import os
import sys

from mcp.client.session_group import ClientSessionGroup, StreamableHttpParameters


async def main() -> int:
    url = os.getenv("TRM_MCP_URL", "http://127.0.0.1:8000/mcp/")
    token = os.getenv("TRM_MCP_API_TOKEN", "")
    if len(token) < 24:
        print("请先设置 TRM_MCP_API_TOKEN（至少24字符）", file=sys.stderr)
        return 2
    async with ClientSessionGroup() as group:
        session = await group.connect_to_server(
            StreamableHttpParameters(
                url=url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
        )
        tools = await session.list_tools()
        names = [tool.name for tool in tools.tools]
        budgets = await session.call_tool("trm_list_budgets", {})
        if budgets.is_error:
            print("预算只读工具调用失败", file=sys.stderr)
            return 1
        print(f"MCP连接成功：{url}")
        print(f"工具数：{len(names)}")
        print("工具：" + ", ".join(names))
        print(f"可用预算数：{budgets.structured_content['count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

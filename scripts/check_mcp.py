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
        print(f"MCP连接成功：{url}")
        print(f"工具数：{len(names)}")
        print("工具：" + ", ".join(names))
        print("工具发现验证通过。业务工具调用需要由TRM当前登录会话签发的 delegation_token。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

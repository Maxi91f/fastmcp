"""Test script for session lifecycle example."""

import asyncio

from session_lifecycle_example import mcp

from fastmcp import Client


async def main():
    print("=== Testing Session Lifecycle ===\n")

    print("1. Connecting first client...")
    async with Client(mcp) as client1:
        result = await client1.call_tool("get_active_sessions", {})
        print(f"   Active sessions: {result.content[0].text}\n")

        print("2. Calling greet tool...")
        result = await client1.call_tool("greet", {"name": "Maxi"})
        print(f"   Result: {result.content[0].text}\n")

        print("3. Connecting second client in parallel...")
        async with Client(mcp) as client2:
            result = await client2.call_tool("get_active_sessions", {})
            print(f"   Active sessions: {result.content[0].text}\n")

            await asyncio.sleep(1)

        print("4. Second client disconnected\n")

        result = await client1.call_tool("get_active_sessions", {})
        print(f"   Active sessions: {result.content[0].text}\n")

        await asyncio.sleep(1)

    print("5. First client disconnected\n")
    print("=== Test Complete ===")


if __name__ == "__main__":
    asyncio.run(main())

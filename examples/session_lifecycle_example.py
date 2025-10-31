"""Example demonstrating session lifecycle tracking with on_initialize and on_disconnect.

This example shows how to track when clients connect and disconnect,
and perform cleanup operations.
"""

from datetime import datetime

import mcp.types as mt

from fastmcp import FastMCP
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext


class SessionLifecycleMiddleware(Middleware):
    """Middleware that tracks client connections and disconnections."""

    def __init__(self):
        super().__init__()
        self.active_sessions = {}
        self.total_connections = 0

    async def on_initialize(
        self,
        context: MiddlewareContext[mt.InitializeRequest],
        call_next: CallNext[mt.InitializeRequest, None],
    ) -> None:
        """Called when a client connects."""
        self.total_connections += 1

        client_name = "Unknown"
        if context.message.params.clientInfo:
            client_name = context.message.params.clientInfo.name

        # Store session data
        self.active_sessions[self.total_connections] = {
            "client": client_name,
            "connected_at": datetime.now(),
        }

        print(f"✅ Client connected: {client_name}")
        print(f"   Total connections: {self.total_connections}")
        print(f"   Active sessions: {len(self.active_sessions)}")
        print()

        return await call_next(context)

    async def on_disconnect(
        self,
        context: MiddlewareContext[None],
        call_next: CallNext[None, None],
    ) -> None:
        """Called when a client disconnects."""
        # Find and remove the most recent session (simplified for example)
        if self.active_sessions:
            session_id = max(self.active_sessions.keys())
            session_data = self.active_sessions.pop(session_id)

            duration = datetime.now() - session_data["connected_at"]

            print(f"❌ Client disconnected: {session_data['client']}")
            print(f"   Session duration: {duration.total_seconds():.1f} seconds")
            print(f"   Remaining active sessions: {len(self.active_sessions)}")
            print()

            # Perform cleanup operations here:
            # - Close database connections
            # - Save session data
            # - Release resources

        return await call_next(context)


# Create server
mcp = FastMCP("Session Lifecycle Demo")

# Add middleware
lifecycle_middleware = SessionLifecycleMiddleware()
mcp.add_middleware(lifecycle_middleware)


@mcp.tool
def get_active_sessions() -> dict:
    """Get information about active sessions."""
    return {
        "total_connections": lifecycle_middleware.total_connections,
        "active_sessions": len(lifecycle_middleware.active_sessions),
        "sessions": [
            {
                "client": data["client"],
                "connected_at": data["connected_at"].isoformat(),
            }
            for data in lifecycle_middleware.active_sessions.values()
        ],
    }

if __name__ == "__main__":
    # We need to run the server with HTTP transport to test this.
    mcp.run(transport="http", port=8000)

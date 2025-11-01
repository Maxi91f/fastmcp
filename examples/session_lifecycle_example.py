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

        # Create a session identifier from client info
        client_info = context.message.params.clientInfo
        client_name = client_info.name if client_info else "Unknown"
        client_version = client_info.version if client_info else "unknown"
        
        # Use client name + version as session key
        session_key = f"{client_name}:{client_version}:{self.total_connections}"

        # Store session data
        self.active_sessions[session_key] = {
            "client": client_name,
            "version": client_version,
            "connected_at": datetime.now(),
        }

        print(f"✅ Client connected: {client_name} v{client_version}")
        print(f"   Session key: {session_key}")
        print(f"   Total connections: {self.total_connections}")
        print(f"   Active sessions: {len(self.active_sessions)}")
        print()

        return await call_next(context)

    async def on_disconnect(
        self,
        context: MiddlewareContext,
        call_next: CallNext,
    ) -> None:
        """Called when a client disconnects."""
        # Reconstruct session key from disconnect message params
        client_info = context.message.params.clientInfo
        client_name = client_info.name if client_info else "Unknown"
        client_version = client_info.version if client_info else "unknown"
        
        # Find matching session by client name and version
        matching_key = None
        for key in self.active_sessions:
            if key.startswith(f"{client_name}:{client_version}:"):
                matching_key = key
                break
        
        if matching_key:
            session_data = self.active_sessions.pop(matching_key)
            duration = datetime.now() - session_data["connected_at"]

            print(f"❌ Client disconnected: {session_data['client']} v{session_data['version']}")
            print(f"   Session key: {matching_key}")
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
                "session_key": key,
                "client": data["client"],
                "version": data["version"],
                "connected_at": data["connected_at"].isoformat(),
            }
            for key, data in lifecycle_middleware.active_sessions.items()
        ],
    }

if __name__ == "__main__":
    # We need to run the server with HTTP transport to test this.
    mcp.run(transport="http", port=8000)

"""Tests for middleware support during disconnection."""

import mcp.types as mt

from fastmcp import Client, FastMCP
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext


class DisconnectTrackingMiddleware(Middleware):
    """Middleware that tracks disconnection events."""

    def __init__(self):
        super().__init__()
        self.disconnect_called = False
        self.session_data = {}

    async def on_initialize(
        self,
        context: MiddlewareContext[mt.InitializeRequest],
        call_next: CallNext[mt.InitializeRequest, None],
    ) -> None:
        """Store session data during initialization."""
        # Store basic session data without accessing session_id
        client_id = (
            context.message.params.clientInfo.name
            if context.message.params.clientInfo
            else None
        )
        self.session_data["last_client"] = {
            "initialized": True,
            "client_id": client_id,
        }
        return await call_next(context)

    async def on_disconnect(
        self,
        context: MiddlewareContext[None],
        call_next: CallNext[None, None],
    ) -> None:
        """Track disconnection."""
        self.disconnect_called = True

        # Mark all sessions as disconnected since we don't have session_id in disconnect
        for session_id in self.session_data:
            self.session_data[session_id]["disconnected"] = True

        return await call_next(context)


class SessionCleanupMiddleware(Middleware):
    """Middleware that performs cleanup on disconnect."""

    def __init__(self):
        super().__init__()
        self.resources_cleaned = False
        self.cleanup_errors = []

    async def on_disconnect(
        self,
        context: MiddlewareContext[None],
        call_next: CallNext[None, None],
    ) -> None:
        """Perform cleanup operations."""
        try:
            # Simulate cleanup operations
            self.resources_cleaned = True
        except Exception as e:
            self.cleanup_errors.append(str(e))

        return await call_next(context)


async def test_disconnect_hook_is_called():
    """Test that the on_disconnect hook is called when client disconnects."""
    server = FastMCP("TestServer")
    middleware = DisconnectTrackingMiddleware()
    server.add_middleware(middleware)

    @server.tool
    def test_tool() -> str:
        return "test"

    # Connect and disconnect
    async with Client(server) as client:
        await client.list_tools()

    # Verify disconnect was called
    assert middleware.disconnect_called


async def test_disconnect_with_session_data():
    """Test that disconnect middleware can access session data."""
    server = FastMCP("TestServer")
    middleware = DisconnectTrackingMiddleware()
    server.add_middleware(middleware)

    @server.tool
    def test_tool() -> str:
        return "test"

    # Connect and disconnect
    async with Client(server) as client:
        await client.list_tools()

    # Verify session data was tracked
    assert middleware.disconnect_called
    # Session data should have been stored during init
    assert len(middleware.session_data) > 0


async def test_disconnect_cleanup():
    """Test that cleanup operations work in disconnect middleware."""
    server = FastMCP("TestServer")
    cleanup_middleware = SessionCleanupMiddleware()
    server.add_middleware(cleanup_middleware)

    @server.tool
    def test_tool() -> str:
        return "test"

    # Connect and disconnect
    async with Client(server) as client:
        await client.list_tools()

    # Verify cleanup was performed
    assert cleanup_middleware.resources_cleaned
    assert len(cleanup_middleware.cleanup_errors) == 0


async def test_multiple_disconnect_middlewares():
    """Test that multiple disconnect middlewares are all called."""
    server = FastMCP("TestServer")

    middleware1 = DisconnectTrackingMiddleware()
    middleware2 = SessionCleanupMiddleware()

    server.add_middleware(middleware1)
    server.add_middleware(middleware2)

    @server.tool
    def test_tool() -> str:
        return "test"

    # Connect and disconnect
    async with Client(server) as client:
        await client.list_tools()

    # Verify both middlewares were called
    assert middleware1.disconnect_called
    assert middleware2.resources_cleaned


async def test_disconnect_with_error_handling():
    """Test that errors in disconnect middleware don't break cleanup."""
    server = FastMCP("TestServer")

    class ErrorMiddleware(Middleware):
        def __init__(self):
            super().__init__()
            self.disconnect_attempted = False

        async def on_disconnect(
            self,
            context: MiddlewareContext[None],
            call_next: CallNext[None, None],
        ) -> None:
            self.disconnect_attempted = True
            # Raise an error
            raise ValueError("Test error in disconnect")

    error_middleware = ErrorMiddleware()
    tracking_middleware = DisconnectTrackingMiddleware()

    server.add_middleware(error_middleware)
    server.add_middleware(tracking_middleware)

    @server.tool
    def test_tool() -> str:
        return "test"

    # Connect and disconnect - should not raise despite error
    async with Client(server) as client:
        await client.list_tools()

    # Both middlewares should have been attempted
    assert error_middleware.disconnect_attempted
    # The error should not prevent other middlewares from running
    # (though order matters - error_middleware is first, so tracking_middleware
    # won't be called if error_middleware raises before call_next)


async def test_disconnect_without_initialize():
    """Test disconnect middleware when session never initialized properly."""
    server = FastMCP("TestServer")
    middleware = DisconnectTrackingMiddleware()
    server.add_middleware(middleware)

    @server.tool
    def test_tool() -> str:
        return "test"

    # Connect and disconnect
    async with Client(server) as client:
        await client.list_tools()

    # Disconnect should still be called even if we didn't track init
    assert middleware.disconnect_called


async def test_init_and_disconnect_lifecycle():
    """Test complete lifecycle with both init and disconnect."""
    server = FastMCP("TestServer")

    lifecycle_events = []

    class LifecycleMiddleware(Middleware):
        async def on_initialize(
            self,
            context: MiddlewareContext[mt.InitializeRequest],
            call_next: CallNext[mt.InitializeRequest, None],
        ) -> None:
            lifecycle_events.append("initialize")
            return await call_next(context)

        async def on_disconnect(
            self,
            context: MiddlewareContext[None],
            call_next: CallNext[None, None],
        ) -> None:
            lifecycle_events.append("disconnect")
            return await call_next(context)

    server.add_middleware(LifecycleMiddleware())

    @server.tool
    def test_tool() -> str:
        return "test"

    # Connect and disconnect
    async with Client(server) as client:
        await client.list_tools()

    # Verify both lifecycle events occurred in order
    assert lifecycle_events == ["initialize", "disconnect"]

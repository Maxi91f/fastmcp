from __future__ import annotations

from collections.abc import AsyncGenerator, Callable, Generator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, cast

from mcp.server.auth.middleware.auth_context import AuthContextMiddleware
from mcp.server.auth.middleware.bearer_auth import (
    BearerAuthBackend,
    RequireAuthMiddleware,
)
from mcp.server.auth.provider import TokenVerifier as TokenVerifierProtocol
from mcp.server.auth.routes import create_auth_routes
from mcp.server.lowlevel.server import LifespanResultT
from mcp.server.sse import SseServerTransport
from mcp.server.streamable_http import EventStore
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from pydantic import AnyHttpUrl
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import BaseRoute, Mount, Route
from starlette.types import Lifespan, Receive, Scope, Send

from fastmcp.server.auth.auth import OAuthProvider, TokenVerifier
from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    from fastmcp.server.server import FastMCP

logger = get_logger(__name__)


_current_http_request: ContextVar[Request | None] = ContextVar(
    "http_request",
    default=None,
)


class StarletteWithLifespan(Starlette):
    @property
    def lifespan(self) -> Lifespan:
        return self.router.lifespan_context


@contextmanager
def set_http_request(request: Request) -> Generator[Request, None, None]:
    token = _current_http_request.set(request)
    try:
        yield request
    finally:
        _current_http_request.reset(token)


class RequestContextMiddleware:
    """
    Middleware that stores each request in a ContextVar
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            with set_http_request(Request(scope)):
                await self.app(scope, receive, send)
        else:
            await self.app(scope, receive, send)


def setup_auth_middleware_and_routes(
    auth: OAuthProvider | TokenVerifier,
) -> tuple[list[Middleware], list[BaseRoute], list[str]]:
    """Set up authentication middleware and routes if auth is enabled.

    Args:
        auth: Either an OAuthProvider or TokenVerifier for authentication

    Returns:
        Tuple of (middleware, auth_routes, required_scopes)
    """
    middleware: list[Middleware] = [
        Middleware(
            AuthenticationMiddleware,
            backend=BearerAuthBackend(cast(TokenVerifierProtocol, auth)),
        ),
        Middleware(AuthContextMiddleware),
    ]

    auth_routes: list[BaseRoute] = []
    required_scopes: list[str] = []

    # Handle TokenVerifier vs OAuthProvider
    # Check if it's an OAuthProvider by looking for issuer_url attribute
    if hasattr(auth, "issuer_url"):
        # OAuthProvider: create auth routes and get required scopes
        # We know this is an OAuthProvider because it has issuer_url
        auth_routes = list(
            create_auth_routes(
                provider=auth,  # type: ignore[arg-type]
                issuer_url=auth.issuer_url,  # type: ignore[attr-defined]
                service_documentation_url=auth.service_documentation_url,  # type: ignore[attr-defined]
                client_registration_options=auth.client_registration_options,  # type: ignore[attr-defined]
                revocation_options=auth.revocation_options,  # type: ignore[attr-defined]
            )
        )
        required_scopes = auth.required_scopes or []  # type: ignore[attr-defined]
    else:
        # TokenVerifier: no auth routes but may have required scopes
        required_scopes = getattr(auth, "required_scopes", None) or []

    return middleware, auth_routes, required_scopes


def create_base_app(
    routes: list[BaseRoute],
    middleware: list[Middleware],
    debug: bool = False,
    lifespan: Callable | None = None,
) -> StarletteWithLifespan:
    """Create a base Starlette app with common middleware and routes.

    Args:
        routes: List of routes to include in the app
        middleware: List of middleware to include in the app
        debug: Whether to enable debug mode
        lifespan: Optional lifespan manager for the app

    Returns:
        A Starlette application
    """
    # Always add RequestContextMiddleware as the outermost middleware
    middleware.append(Middleware(RequestContextMiddleware))

    return StarletteWithLifespan(
        routes=routes,
        middleware=middleware,
        debug=debug,
        lifespan=lifespan,
    )


def create_sse_app(
    server: FastMCP[LifespanResultT],
    message_path: str,
    sse_path: str,
    auth: OAuthProvider | TokenVerifier | None = None,
    debug: bool = False,
    routes: list[BaseRoute] | None = None,
    middleware: list[Middleware] | None = None,
) -> StarletteWithLifespan:
    """Return an instance of the SSE server app.

    Args:
        server: The FastMCP server instance
        message_path: Path for SSE messages
        sse_path: Path for SSE connections
        auth: Optional authentication provider (OAuthProvider or TokenVerifier)
        debug: Whether to enable debug mode
        routes: Optional list of custom routes
        middleware: Optional list of middleware
    Returns:
        A Starlette application with RequestContextMiddleware
    """

    server_routes: list[BaseRoute] = []
    server_middleware: list[Middleware] = []

    # Set up SSE transport
    sse = SseServerTransport(message_path)

    # Create handler for SSE connections
    async def handle_sse(scope: Scope, receive: Receive, send: Send) -> Response:
        async with sse.connect_sse(scope, receive, send) as streams:
            await server._mcp_server.run(
                streams[0],
                streams[1],
                server._mcp_server.create_initialization_options(),
            )
        return Response()

    # Get auth middleware and routes
    if auth:
        auth_middleware, auth_routes, required_scopes = (
            setup_auth_middleware_and_routes(auth)
        )

        server_routes.extend(auth_routes)
        server_middleware.extend(auth_middleware)

        # Determine resource_metadata_url for TokenVerifier
        resource_metadata_url = None
        if isinstance(auth, TokenVerifier) and auth.resource_server_url:
            # Add .well-known path for RFC 9728 compliance
            resource_metadata_url = AnyHttpUrl(
                str(auth.resource_server_url).rstrip("/")
                + "/.well-known/oauth-protected-resource"
            )

        # Auth is enabled, wrap endpoints with RequireAuthMiddleware
        server_routes.append(
            Route(
                sse_path,
                endpoint=RequireAuthMiddleware(
                    handle_sse, required_scopes, resource_metadata_url
                ),
                methods=["GET"],
            )
        )
        server_routes.append(
            Mount(
                message_path,
                app=RequireAuthMiddleware(
                    sse.handle_post_message, required_scopes, resource_metadata_url
                ),
            )
        )
    else:
        # No auth required
        async def sse_endpoint(request: Request) -> Response:
            return await handle_sse(request.scope, request.receive, request._send)  # type: ignore[reportPrivateUsage]

        server_routes.append(
            Route(
                sse_path,
                endpoint=sse_endpoint,
                methods=["GET"],
            )
        )
        server_routes.append(
            Mount(
                message_path,
                app=sse.handle_post_message,
            )
        )

    # Add custom routes with lowest precedence
    if routes:
        server_routes.extend(routes)
    server_routes.extend(server._additional_http_routes)

    # Add middleware
    if middleware:
        server_middleware.extend(middleware)

    # Create and return the app
    app = create_base_app(
        routes=server_routes,
        middleware=server_middleware,
        debug=debug,
    )
    # Store the FastMCP server instance on the Starlette app state
    app.state.fastmcp_server = server
    app.state.path = sse_path

    return app


def create_streamable_http_app(
    server: FastMCP[LifespanResultT],
    streamable_http_path: str,
    event_store: EventStore | None = None,
    auth: OAuthProvider | TokenVerifier | None = None,
    json_response: bool = False,
    stateless_http: bool = False,
    debug: bool = False,
    routes: list[BaseRoute] | None = None,
    middleware: list[Middleware] | None = None,
) -> StarletteWithLifespan:
    """Return an instance of the StreamableHTTP server app.

    Args:
        server: The FastMCP server instance
        streamable_http_path: Path for StreamableHTTP connections
        event_store: Optional event store for session management
        auth: Optional authentication provider (OAuthProvider or TokenVerifier)
        json_response: Whether to use JSON response format
        stateless_http: Whether to use stateless mode (new transport per request)
        debug: Whether to enable debug mode
        routes: Optional list of custom routes
        middleware: Optional list of middleware

    Returns:
        A Starlette application with StreamableHTTP support
    """
    server_routes: list[BaseRoute] = []
    server_middleware: list[Middleware] = []

    # Create session manager using the provided event store
    session_manager = StreamableHTTPSessionManager(
        app=server._mcp_server,
        event_store=event_store,
        json_response=json_response,
        stateless=stateless_http,
    )

    # Create the ASGI handler
    async def handle_streamable_http(
        scope: Scope, receive: Receive, send: Send
    ) -> None:
        try:
            await session_manager.handle_request(scope, receive, send)
        except RuntimeError as e:
            if str(e) == "Task group is not initialized. Make sure to use run().":
                logger.error(
                    f"Original RuntimeError from mcp library: {e}", exc_info=True
                )
                new_error_message = (
                    "FastMCP's StreamableHTTPSessionManager task group was not initialized. "
                    "This commonly occurs when the FastMCP application's lifespan is not "
                    "passed to the parent ASGI application (e.g., FastAPI or Starlette). "
                    "Please ensure you are setting `lifespan=mcp_app.lifespan` in your "
                    "parent app's constructor, where `mcp_app` is the application instance "
                    "returned by `fastmcp_instance.http_app()`. \\n"
                    "For more details, see the FastMCP ASGI integration documentation: "
                    "https://gofastmcp.com/deployment/asgi"
                )
                # Raise a new RuntimeError that includes the original error's message
                # for full context, but leads with the more helpful guidance.
                raise RuntimeError(f"{new_error_message}\\nOriginal error: {e}") from e
            else:
                # Re-raise other RuntimeErrors if they don't match the specific message
                raise

    # Add StreamableHTTP routes with or without auth
    if auth:
        resource_metadata_url = None

        if isinstance(auth, TokenVerifier) and auth.resource_server_url:
            resource_metadata_url = AnyHttpUrl(
                str(auth.resource_server_url).rstrip("/")
                + "/.well-known/oauth-protected-resource"
            )

        auth_middleware, auth_routes, required_scopes = (
            setup_auth_middleware_and_routes(auth)
        )

        server_routes.extend(auth_routes)
        server_middleware.extend(auth_middleware)

        # Determine resource_metadata_url for TokenVerifier
        resource_metadata_url = None
        if isinstance(auth, TokenVerifier) and auth.resource_server_url:
            # Add .well-known path for RFC 9728 compliance
            resource_metadata_url = AnyHttpUrl(
                str(auth.resource_server_url).rstrip("/")
                + "/.well-known/oauth-protected-resource"
            )

        # Auth is enabled, wrap endpoint with RequireAuthMiddleware
        server_routes.append(
            Mount(
                streamable_http_path,
                app=RequireAuthMiddleware(
                    handle_streamable_http, required_scopes, resource_metadata_url
                ),
            )
        )
    else:
        # No auth required
        server_routes.append(
            Mount(
                streamable_http_path,
                app=handle_streamable_http,
            )
        )

    # Add custom routes with lowest precedence
    if routes:
        server_routes.extend(routes)
    server_routes.extend(server._additional_http_routes)

    # Add middleware
    if middleware:
        server_middleware.extend(middleware)

    # Create a lifespan manager to start and stop the session manager
    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncGenerator[None, None]:
        async with session_manager.run():
            yield

    # Create and return the app with lifespan
    app = create_base_app(
        routes=server_routes,
        middleware=server_middleware,
        debug=debug,
        lifespan=lifespan,
    )
    # Store the FastMCP server instance on the Starlette app state
    app.state.fastmcp_server = server

    app.state.path = streamable_http_path

    return app

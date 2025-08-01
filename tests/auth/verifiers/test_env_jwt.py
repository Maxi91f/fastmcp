import pytest
from pydantic import ValidationError

from fastmcp import FastMCP
from fastmcp.server.auth.verifiers import EnvJWTVerifier, JWTVerifier
from fastmcp.settings import Settings
from fastmcp.utilities.tests import temporary_settings


def test_load_bearer_env_from_env_var(monkeypatch):
    mcp = FastMCP()
    assert mcp.auth is None

    monkeypatch.setenv("FASTMCP_DEFAULT_AUTH_PROVIDER", "jwt-env")
    monkeypatch.setenv("FASTMCP_AUTH_JWT_PUBLIC_KEY", "test-public-key")

    with temporary_settings(**Settings().model_dump()):
        mcp_with_auth = FastMCP()
    assert isinstance(mcp_with_auth.auth, EnvJWTVerifier)


def test_load_bearer_env_from_env_var_requires_public_key_or_jwks_uri(monkeypatch):
    mcp = FastMCP()
    assert mcp.auth is None

    monkeypatch.setenv("FASTMCP_DEFAULT_AUTH_PROVIDER", "jwt-env")

    with temporary_settings(**Settings().model_dump()):
        with pytest.raises(
            ValueError, match="Either public_key or jwks_uri must be provided"
        ):
            FastMCP()


def test_configure_bearer_env_from_env_var(monkeypatch):
    monkeypatch.setenv("FASTMCP_DEFAULT_AUTH_PROVIDER", "jwt-env")
    monkeypatch.setenv("FASTMCP_AUTH_JWT_PUBLIC_KEY", "test-public-key")
    monkeypatch.setenv("FASTMCP_AUTH_JWT_ISSUER", "http://test-issuer")
    monkeypatch.setenv("FASTMCP_AUTH_JWT_AUDIENCE", "test-audience")
    monkeypatch.setenv(
        "FASTMCP_AUTH_JWT_REQUIRED_SCOPES", '["test-scope1", "test-scope2"]'
    )

    with temporary_settings(**Settings().model_dump()):
        mcp = FastMCP()
    assert isinstance(mcp.auth, EnvJWTVerifier)
    assert mcp.auth.public_key == "test-public-key"
    assert mcp.auth.audience == "test-audience"
    assert mcp.auth.required_scopes == ["test-scope1", "test-scope2"]


def test_list_of_scopes_must_be_a_list(monkeypatch):
    monkeypatch.setenv("FASTMCP_DEFAULT_AUTH_PROVIDER", "jwt-env")
    monkeypatch.setenv("FASTMCP_AUTH_JWT_REQUIRED_SCOPES", "test-scope1")

    with temporary_settings(**Settings().model_dump()):
        with pytest.raises(ValidationError, match="Input should be a valid list"):
            FastMCP()


def test_configure_bearer_env_jwks_uri_from_env_var(monkeypatch):
    monkeypatch.setenv("FASTMCP_DEFAULT_AUTH_PROVIDER", "jwt-env")
    monkeypatch.setenv("FASTMCP_AUTH_JWT_JWKS_URI", "test-jwks-uri")

    with temporary_settings(**Settings().model_dump()):
        mcp = FastMCP()
        assert isinstance(mcp.auth, EnvJWTVerifier)
        assert mcp.auth.jwks_uri == "test-jwks-uri"


def test_configure_bearer_env_public_key_and_jwks_uri_error(monkeypatch):
    monkeypatch.setenv("FASTMCP_DEFAULT_AUTH_PROVIDER", "jwt-env")
    monkeypatch.setenv("FASTMCP_AUTH_JWT_PUBLIC_KEY", "test-public-key")
    monkeypatch.setenv("FASTMCP_AUTH_JWT_JWKS_URI", "test-jwks-uri")

    with temporary_settings(**Settings().model_dump()):
        with pytest.raises(ValueError, match="Provide either public_key or jwks_uri"):
            FastMCP()


def test_provided_auth_takes_precedence_over_env_vars(monkeypatch):
    monkeypatch.setenv("FASTMCP_DEFAULT_AUTH_PROVIDER", "jwt-env")
    monkeypatch.setenv("FASTMCP_AUTH_JWT_PUBLIC_KEY", "test-public-key")

    with temporary_settings(**Settings().model_dump()):
        mcp = FastMCP(auth=JWTVerifier(public_key="test-public-key-2"))
        assert isinstance(mcp.auth, JWTVerifier)
        assert not isinstance(mcp.auth, EnvJWTVerifier)
        assert mcp.auth.public_key == "test-public-key-2"

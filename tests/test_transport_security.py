"""Tests for the streamable-HTTP transport security helper."""
from wikimcp import __version__
from wikimcp.server.mcp_server import (
    http_transport_security,
    create_local_server,
)


def test_default_allows_localhost_only() -> None:
    settings = http_transport_security()
    assert settings.enable_dns_rebinding_protection is True
    assert "localhost" in settings.allowed_hosts
    assert "localhost:*" in settings.allowed_hosts
    assert "127.0.0.1" in settings.allowed_hosts
    assert "127.0.0.1:*" in settings.allowed_hosts
    # Not allowed by default
    assert "host.docker.internal" not in settings.allowed_hosts


def test_extra_hosts_registered_bare_and_wildcard() -> None:
    settings = http_transport_security(["host.docker.internal"])
    assert "host.docker.internal" in settings.allowed_hosts
    assert "host.docker.internal:*" in settings.allowed_hosts
    # Defaults still present
    assert "localhost" in settings.allowed_hosts
    # Matching http/https origins are allowed too
    assert "http://host.docker.internal" in settings.allowed_origins
    assert "https://host.docker.internal:*" in settings.allowed_origins


def test_disable_protection() -> None:
    settings = http_transport_security(disable_protection=True)
    assert settings.enable_dns_rebinding_protection is False


def test_local_server_reports_wikimcp_version() -> None:
    mcp = create_local_server("/tmp/does-not-need-to-exist")
    assert mcp._mcp_server.version == __version__

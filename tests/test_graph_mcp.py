"""MCP integration tests (5e): the graph tools are registered with valid schemas,
invoked through the real FastMCP server interface, and return JSON-serialisable,
token-lean payloads."""

from __future__ import annotations

import asyncio
import json

import pytest

from wikimcp.server.mcp_server import create_local_server

GRAPH_TOOLS = {
    "get_related", "get_subgraph", "path", "surprising_links",
    "hubs", "orphans", "wiki_report",
}


def _text(result) -> str:
    content = result[0] if isinstance(result, tuple) else result
    return content[0].text


def call(mcp, name, args):
    return _text(asyncio.run(mcp.call_tool(name, args)))


@pytest.fixture
def mcp(realistic_wiki):
    return create_local_server(realistic_wiki)


def test_all_graph_tools_registered(mcp):
    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    assert GRAPH_TOOLS <= names


def test_tool_schemas_valid(mcp):
    tools = {t.name: t for t in asyncio.run(mcp.list_tools())}
    # Required arg present.
    assert "page" in tools["get_related"].inputSchema.get("required", [])
    # Defaulted args are not required.
    sub = tools["get_subgraph"].inputSchema
    assert "page" in sub["required"]
    assert "depth" not in sub.get("required", [])
    # orphans takes no arguments.
    assert tools["orphans"].inputSchema.get("required", []) == []


def test_hubs_returns_json(mcp):
    payload = json.loads(call(mcp, "hubs", {"limit": 3}))
    assert payload["hubs"][0]["path"] == "topics/machine-learning.md"


def test_get_subgraph_returns_render_ready_json(mcp):
    payload = json.loads(
        call(mcp, "get_subgraph",
             {"page": "topics/machine-learning.md", "depth": 2, "max_nodes": 10})
    )
    assert set(payload) >= {"center", "nodes", "edges", "truncated"}
    node_paths = {n["path"] for n in payload["nodes"]}
    for e in payload["edges"]:
        assert e["source"] in node_paths and e["target"] in node_paths


def test_get_related_via_mcp(mcp):
    payload = json.loads(
        call(mcp, "get_related",
             {"page": "topics/machine-learning.md", "direction": "in"})
    )
    assert any(r["path"] == "projects/project-falcon.md" for r in payload["related"])


def test_path_via_mcp(mcp):
    payload = json.loads(
        call(mcp, "path",
             {"page_a": "topics/machine-learning.md", "page_b": "topics/time-blocking.md"})
    )
    assert payload["found"] is True


def test_orphans_via_mcp(mcp):
    payload = json.loads(call(mcp, "orphans", {}))
    assert len(payload["orphans"]) == 2


def test_wiki_report_via_mcp_is_text(mcp):
    text = call(mcp, "wiki_report", {})
    assert text.startswith("# Wiki Report")


def test_missing_page_returns_json_error(mcp):
    payload = json.loads(call(mcp, "get_related", {"page": "topics/nope.md"}))
    assert "error" in payload


def test_payloads_are_token_lean(mcp):
    # Compact JSON: no pretty-print whitespace after separators.
    raw = call(mcp, "hubs", {"limit": 10})
    assert ", " not in raw and ": " not in raw

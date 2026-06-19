"""Export tests (5h): graph.html is self-contained (no network references),
inlines the graph JSON, and parses; the Obsidian export produces a valid,
openable vault without mutating source pages."""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path

from wikimcp.wiki import graph_export as ge


class _ScriptCollector(HTMLParser):
    """Confirms the document parses and captures the inline graph-data script."""

    def __init__(self):
        super().__init__()
        self._in_data = False
        self.data_text = ""
        self.script_count = 0

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.script_count += 1
            self._in_data = ("id", "graph-data") in attrs

    def handle_endtag(self, tag):
        if tag == "script":
            self._in_data = False

    def handle_data(self, data):
        if self._in_data:
            self.data_text += data


# --- HTML export ------------------------------------------------------------

def test_html_is_self_contained(realistic_wiki, tmp_path):
    out = tmp_path / "graph.html"
    ge.export_html(realistic_wiki, out)
    html = out.read_text(encoding="utf-8")
    # No external resource references whatsoever (no CDN / network at view time).
    assert "http://" not in html
    assert "https://" not in html
    assert "//cdn" not in html
    assert "src=" not in html  # no external scripts/images


def test_html_inlines_parseable_graph_json(realistic_wiki, tmp_path):
    out = tmp_path / "graph.html"
    result = ge.export_html(realistic_wiki, out)
    html = out.read_text(encoding="utf-8")

    parser = _ScriptCollector()
    parser.feed(html)  # must not raise -> document parses
    assert parser.script_count >= 2  # data script + renderer script

    payload = json.loads(parser.data_text)
    assert len(payload["nodes"]) == result["nodes"] == 24
    assert len(payload["edges"]) == result["edges"]
    assert all("id" in n and "title" in n for n in payload["nodes"])


def test_html_escapes_script_in_data(tmp_path):
    # A title containing </script> must not break out of the data block.
    wiki = tmp_path
    sub = wiki / "wiki" / "topics"
    sub.mkdir(parents=True)
    (sub / "x.md").write_text("---\ntitle: Tricky </script> Title\n---\n# x\n",
                              encoding="utf-8")
    from wikimcp.wiki import graph
    graph.invalidate_cache()
    out = wiki / "graph.html"
    ge.export_html(wiki, out)
    html = out.read_text(encoding="utf-8")
    parser = _ScriptCollector()
    parser.feed(html)
    # Exactly the two real script tags (data + renderer), none injected.
    assert parser.script_count == 2
    payload = json.loads(parser.data_text)
    assert payload["nodes"][0]["title"] == "Tricky </script> Title"


def test_html_default_path(realistic_wiki_copy):
    result = ge.export_html(realistic_wiki_copy)
    assert Path(result["path"]) == realistic_wiki_copy / "graph.html"
    assert (realistic_wiki_copy / "graph.html").exists()


# --- Obsidian export --------------------------------------------------------

def test_obsidian_in_place_config(realistic_wiki_copy):
    result = ge.export_obsidian(realistic_wiki_copy)
    assert result["copied"] is False
    config = Path(result["config_dir"])
    assert (config / "app.json").exists()
    assert (config / "core-plugins.json").exists()
    assert (config / "graph.json").exists()
    # Graph + backlink plugins are enabled so the vault is useful out of the box.
    plugins = json.loads((config / "core-plugins.json").read_text())
    assert "graph" in plugins and "backlink" in plugins
    # app.json is valid JSON.
    json.loads((config / "app.json").read_text())


def test_obsidian_does_not_mutate_source_pages(realistic_wiki_copy):
    before = (realistic_wiki_copy / "wiki" / "topics" / "machine-learning.md").read_text()
    ge.export_obsidian(realistic_wiki_copy)
    after = (realistic_wiki_copy / "wiki" / "topics" / "machine-learning.md").read_text()
    assert before == after


def test_obsidian_copy_export(realistic_wiki, tmp_path):
    out = tmp_path / "vault"
    result = ge.export_obsidian(realistic_wiki, out)
    assert result["copied"] is True
    assert (out / ".obsidian" / "app.json").exists()
    # Pages copied into the vault.
    assert (out / "wiki" / "topics" / "machine-learning.md").exists()
    # Source wiki untouched (no .obsidian written into the read-only fixture).
    assert not (realistic_wiki / ".obsidian").exists()

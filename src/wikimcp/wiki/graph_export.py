"""
graph_export.py — Deterministic, offline exporters for the page graph.

Two exporters, both offline and side-effect-light:

* :func:`export_obsidian` — make the wiki openable as an Obsidian vault (graph
  view + backlinks come for free) by emitting a minimal ``.obsidian/`` config.
  Source pages are never mutated. An optional ``out_dir`` produces an exported
  copy instead of writing config into the live wiki.

* :func:`export_html` — write a SELF-CONTAINED ``graph.html``: the graph is
  serialised inline as JSON next to a tiny vendored canvas force-directed
  renderer. No network access at view time (no CDN), no build step.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from .graph import EXTRACTED, get_graph

# ---------------------------------------------------------------------------
# Obsidian vault export
# ---------------------------------------------------------------------------

#: Core plugins enabled so the vault has graph view + backlinks out of the box.
_OBSIDIAN_CORE_PLUGINS = [
    "file-explorer",
    "global-search",
    "graph",
    "backlink",
    "outgoing-link",
    "page-preview",
    "tag-pane",
]

#: App settings. ``useMarkdownLinks`` matches wikimcp's relative-markdown-link
#: convention while Obsidian still renders ``[[wikilinks]]`` too.
_OBSIDIAN_APP_JSON = {
    "alwaysUpdateLinks": True,
    "newLinkFormat": "relative",
    "useMarkdownLinks": True,
    "attachmentFolderPath": "raw",
}

_OBSIDIAN_GRAPH_JSON = {
    "collapse-filter": True,
    "search": "",
    "showTags": True,
    "showAttachments": False,
    "showOrphans": True,
    "collapse-display": True,
    "showArrow": True,
    "collapse-forces": False,
    "centerStrength": 0.5,
    "repelStrength": 10,
    "linkStrength": 1,
    "linkDistance": 250,
}


def _write_obsidian_config(vault_root: Path) -> Path:
    config_dir = vault_root / ".obsidian"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "app.json").write_text(
        json.dumps(_OBSIDIAN_APP_JSON, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (config_dir / "core-plugins.json").write_text(
        json.dumps(sorted(_OBSIDIAN_CORE_PLUGINS), indent=2) + "\n",
        encoding="utf-8",
    )
    (config_dir / "graph.json").write_text(
        json.dumps(_OBSIDIAN_GRAPH_JSON, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return config_dir


def export_obsidian(
    wiki_dir: Path,
    out_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Make ``wiki_dir`` openable as an Obsidian vault.

    Default (``out_dir`` is None): write a minimal ``.obsidian/`` config into the
    live wiki — source pages are untouched. With ``out_dir``: copy the wiki pages
    (and ``CLAUDE.md``) into a fresh vault under ``out_dir`` and configure that
    copy, leaving the source wiki completely unmodified.
    """
    wiki_dir = Path(wiki_dir)
    if out_dir is None:
        config_dir = _write_obsidian_config(wiki_dir)
        return {
            "vault_root": str(wiki_dir),
            "config_dir": str(config_dir),
            "copied": False,
        }

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Copy the pages directory and CLAUDE.md; never touch the source.
    src_pages = wiki_dir / "wiki"
    if src_pages.exists():
        shutil.copytree(
            src_pages,
            out_dir / "wiki",
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(".git", ".*"),
        )
    claude_md = wiki_dir / "CLAUDE.md"
    if claude_md.exists():
        shutil.copy2(claude_md, out_dir / "CLAUDE.md")

    config_dir = _write_obsidian_config(out_dir)
    return {
        "vault_root": str(out_dir),
        "config_dir": str(config_dir),
        "copied": True,
    }


# ---------------------------------------------------------------------------
# Self-contained HTML graph export
# ---------------------------------------------------------------------------

def build_graph_payload(wiki_dir: Path) -> Dict[str, Any]:
    """Deterministic node/edge JSON payload for rendering."""
    graph = get_graph(wiki_dir)
    nodes = [
        {
            "id": path,
            "title": graph.title_of(path),
            "dir": graph.pages[path].top_dir or "/",
        }
        for path in sorted(graph.pages)
    ]
    edges = [
        {
            "source": e.source,
            "target": e.target,
            "type": e.type,
            "subtype": e.subtype,
            "direction": e.direction,
            "weight": e.weight,
        }
        for e in graph.edges
    ]
    return {"nodes": nodes, "edges": edges}


def render_html(payload: Dict[str, Any]) -> str:
    """Render the self-contained HTML document for a graph payload.

    The graph JSON is embedded inline and consumed by a small vendored canvas
    force-directed renderer. The output references no external resources.
    """
    data_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    # The JS is intentionally dependency-free and inlined. {DATA} is replaced
    # after escaping to keep the HTML valid even if a title contains "</script>".
    safe = data_json.replace("</", "<\\/")
    return _HTML_TEMPLATE.replace("__GRAPH_DATA__", safe)


def export_html(
    wiki_dir: Path,
    out_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Write a self-contained ``graph.html`` for the wiki graph.

    Defaults to ``<wiki_dir>/graph.html``. Returns the output path and counts.
    """
    wiki_dir = Path(wiki_dir)
    payload = build_graph_payload(wiki_dir)
    html = render_html(payload)
    if out_path is None:
        out_path = wiki_dir / "graph.html"
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return {
        "path": str(out_path),
        "nodes": len(payload["nodes"]),
        "edges": len(payload["edges"]),
    }


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>wikimcp graph</title>
<style>
  html,body{margin:0;height:100%;background:#11131a;color:#e6e6e6;
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;}
  #wrap{display:flex;height:100%;}
  #canvas{flex:1;display:block;cursor:grab;}
  #side{width:300px;box-sizing:border-box;padding:14px;overflow:auto;
    background:#181b24;border-left:1px solid #2a2f3a;font-size:13px;}
  #side h1{font-size:15px;margin:0 0 8px;}
  #side h2{font-size:13px;margin:14px 0 6px;color:#9aa4b2;text-transform:uppercase;
    letter-spacing:.05em;}
  .filter{display:block;margin:3px 0;cursor:pointer;}
  .pill{display:inline-block;padding:1px 6px;border-radius:8px;font-size:11px;}
  a{color:#7fb2ff;text-decoration:none;word-break:break-all;}
  ul{margin:4px 0;padding-left:16px;}
  li{margin:2px 0;}
  .muted{color:#6b7280;}
  .legend i{display:inline-block;width:10px;height:10px;border-radius:50%;
    margin-right:6px;vertical-align:middle;}
</style>
</head>
<body>
<div id="wrap">
  <canvas id="canvas"></canvas>
  <div id="side">
    <h1>wikimcp graph</h1>
    <div id="counts" class="muted"></div>
    <h2>Edge filters</h2>
    <div id="filters"></div>
    <h2>Legend</h2>
    <div id="legend" class="legend"></div>
    <h2>Selected</h2>
    <div id="detail" class="muted">Click a node.</div>
  </div>
</div>
<script id="graph-data" type="application/json">__GRAPH_DATA__</script>
<script>
"use strict";
var GRAPH = JSON.parse(document.getElementById("graph-data").textContent);
var SUBTYPES = ["wikilink","related","title-mention","shared-tag"];
var enabled = {};
SUBTYPES.forEach(function(s){enabled[s]=true;});

var canvas = document.getElementById("canvas");
var ctx = canvas.getContext("2d");
var W=0,H=0,DPR=window.devicePixelRatio||1;
function resize(){
  W=canvas.clientWidth;H=canvas.clientHeight;
  canvas.width=W*DPR;canvas.height=H*DPR;ctx.setTransform(DPR,0,0,DPR,0,0);
}
window.addEventListener("resize",function(){resize();});

// Deterministic node colours by directory.
var dirs=[];
GRAPH.nodes.forEach(function(n){if(dirs.indexOf(n.dir)<0)dirs.push(n.dir);});
dirs.sort();
function colour(dir){
  var i=dirs.indexOf(dir);var h=(i*67)%360;return "hsl("+h+",60%,60%)";
}

// Build node objects with deterministic seeded layout.
var nodes={},nodeList=[];
GRAPH.nodes.forEach(function(n,i){
  var a=(i*2.399963229);var r=40+8*i;
  var o={id:n.id,title:n.title,dir:n.dir,
    x:Math.cos(a)*r,y:Math.sin(a)*r,vx:0,vy:0,deg:0};
  nodes[n.id]=o;nodeList.push(o);
});
GRAPH.edges.forEach(function(e){
  if(nodes[e.source])nodes[e.source].deg++;
  if(nodes[e.target])nodes[e.target].deg++;
});

var view={x:0,y:0,scale:1};
function activeEdges(){return GRAPH.edges.filter(function(e){return enabled[e.subtype];});}

// Force simulation (Fruchterman-Reingold style), runs in-browser only.
function step(){
  var k=90;
  for(var i=0;i<nodeList.length;i++){
    var a=nodeList[i];
    for(var j=i+1;j<nodeList.length;j++){
      var b=nodeList[j];
      var dx=a.x-b.x,dy=a.y-b.y;var d2=dx*dx+dy*dy+0.01;var d=Math.sqrt(d2);
      var f=(k*k)/d2;var ux=dx/d,uy=dy/d;
      a.vx+=ux*f;a.vy+=uy*f;b.vx-=ux*f;b.vy-=uy*f;
    }
    a.vx+=(-a.x)*0.002;a.vy+=(-a.y)*0.002;
  }
  activeEdges().forEach(function(e){
    var a=nodes[e.source],b=nodes[e.target];if(!a||!b)return;
    var dx=b.x-a.x,dy=b.y-a.y;var d=Math.sqrt(dx*dx+dy*dy)+0.01;
    var f=(d-k)*0.02;var ux=dx/d,uy=dy/d;
    a.vx+=ux*f;a.vy+=uy*f;b.vx-=ux*f;b.vy-=uy*f;
  });
  nodeList.forEach(function(n){
    if(n===dragNode)return;
    n.vx*=0.85;n.vy*=0.85;n.x+=n.vx*0.1;n.y+=n.vy*0.1;
  });
}

function toScreen(n){return {x:W/2+(n.x+view.x)*view.scale,y:H/2+(n.y+view.y)*view.scale};}

function draw(){
  ctx.clearRect(0,0,W,H);
  activeEdges().forEach(function(e){
    var a=nodes[e.source],b=nodes[e.target];if(!a||!b)return;
    var pa=toScreen(a),pb=toScreen(b);
    ctx.beginPath();ctx.moveTo(pa.x,pa.y);ctx.lineTo(pb.x,pb.y);
    ctx.strokeStyle=e.type==="EXTRACTED"?"rgba(160,200,255,0.5)":"rgba(150,150,150,0.3)";
    ctx.lineWidth=Math.min(1+e.weight*0.3,3);
    if(e.type!=="EXTRACTED"){ctx.setLineDash([4,3]);}else{ctx.setLineDash([]);}
    ctx.stroke();ctx.setLineDash([]);
  });
  nodeList.forEach(function(n){
    var p=toScreen(n);var r=(4+Math.sqrt(n.deg)*2)*view.scale;
    ctx.beginPath();ctx.arc(p.x,p.y,r,0,Math.PI*2);
    ctx.fillStyle=(n===selected)?"#ffd166":colour(n.dir);ctx.fill();
    if(view.scale>0.7){
      ctx.fillStyle="#cdd3dc";ctx.font=(11*Math.min(view.scale,1.4))+"px sans-serif";
      ctx.fillText(n.title,p.x+r+2,p.y+3);
    }
  });
}

var selected=null,dragNode=null,panning=false,last={x:0,y:0};
function nodeAt(mx,my){
  for(var i=nodeList.length-1;i>=0;i--){
    var n=nodeList[i];var p=toScreen(n);var r=(4+Math.sqrt(n.deg)*2)*view.scale+4;
    if((mx-p.x)*(mx-p.x)+(my-p.y)*(my-p.y)<=r*r)return n;
  }
  return null;
}
canvas.addEventListener("mousedown",function(ev){
  var n=nodeAt(ev.offsetX,ev.offsetY);
  if(n){dragNode=n;selectNode(n);}else{panning=true;}
  last={x:ev.offsetX,y:ev.offsetY};
});
window.addEventListener("mousemove",function(ev){
  var mx=ev.offsetX,my=ev.offsetY;
  if(dragNode){dragNode.x=(mx-W/2)/view.scale-view.x;dragNode.y=(my-H/2)/view.scale-view.y;}
  else if(panning){view.x+=(mx-last.x)/view.scale;view.y+=(my-last.y)/view.scale;}
  last={x:mx,y:my};
});
window.addEventListener("mouseup",function(){dragNode=null;panning=false;});
canvas.addEventListener("wheel",function(ev){
  ev.preventDefault();var f=ev.deltaY<0?1.1:0.9;view.scale=Math.max(0.2,Math.min(4,view.scale*f));
},{passive:false});

function selectNode(n){
  selected=n;
  var outs=GRAPH.edges.filter(function(e){return e.source===n.id&&enabled[e.subtype];});
  var ins=GRAPH.edges.filter(function(e){return e.target===n.id&&enabled[e.subtype];});
  function li(e,other){return "<li><span class='muted'>"+e.subtype+"</span> "+
    (nodes[other]?nodes[other].title:other)+"</li>";}
  var html="<h1>"+n.title+"</h1><div class='muted'>"+n.id+"</div>";
  html+="<h2>Outgoing ("+outs.length+")</h2><ul>"+
    outs.map(function(e){return li(e,e.target);}).join("")+"</ul>";
  html+="<h2>Backlinks ("+ins.length+")</h2><ul>"+
    ins.map(function(e){return li(e,e.source);}).join("")+"</ul>";
  document.getElementById("detail").innerHTML=html;
}

// Build filter checkboxes + legend.
var fbox=document.getElementById("filters");
SUBTYPES.forEach(function(s){
  var id="f_"+s;
  var label=document.createElement("label");label.className="filter";
  label.innerHTML="<input type='checkbox' id='"+id+"' checked> "+s;
  fbox.appendChild(label);
  label.querySelector("input").addEventListener("change",function(e){
    enabled[s]=e.target.checked;
  });
});
var legend=document.getElementById("legend");
dirs.forEach(function(d){
  var row=document.createElement("div");
  row.innerHTML="<i style='background:"+colour(d)+"'></i>"+d;
  legend.appendChild(row);
});
document.getElementById("counts").textContent=
  GRAPH.nodes.length+" pages · "+GRAPH.edges.length+" edges";

resize();
(function loop(){step();draw();requestAnimationFrame(loop);})();
</script>
</body>
</html>
"""

"""Stdio MCP provider for the status line introduced in Codex 0.172."""

import json
import sys

from . import __version__
from .rollout import rate_limit_fields


URI = "ui://cxs/footer"
STATE_KEY = "com.openai/codex-statusline-state"
TOOL = {
    "name": "statusline",
    "title": "cxs · Codex Statebar",
    "description": "Show model, context usage and account limits in the Codex footer.",
    "inputSchema": {"type": "object", "properties": {}},
    "_meta": {"openai/ui": {"entrypoints": [{"type": "statusline", "resourceUri": URI}]}},
}


def _text(text, color):
    return {"type": "text", "props": {"color": color}, "children": [text]}


def _render(state):
    thread = state.get("thread", {})
    model = thread.get("model", {}).get("display_name")
    label = model.strip()[:256] if isinstance(model, str) else ""
    children = [_text(label or "Codex", "status-model")]
    used = thread.get("usage", {}).get("context", {}).get("used_percentage")
    if type(used) is int and used >= 0:
        children.append(_text(f"ctx {min(used, 100)}% used", "status-usage"))

    for limit in state.get("account", {}).get("rate_limits", []):
        if limit.get("limit_id") != "codex":
            continue
        fields = rate_limit_fields({
            "primary": limit.get("primary_limit"),
            "secondary": limit.get("secondary_limit"),
        })
        for label, key in (("5h", "rate_limit_pct"), ("7d", "rate_limit_7d_pct")):
            percent = fields.get(key)
            if percent is not None:
                children.append(_text(f"{label} {percent:g}% used", "status-limit"))
        break

    return {"type": "row", "props": {"gap": 2, "wrap": "wrap"}, "children": children}


def _respond(method, params):
    if method == "initialize":
        return {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {}, "resources": {}},
            "serverInfo": {"name": "cxs", "version": __version__},
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [TOOL]}
    if method == "tools/call":
        if params.get("name") != TOOL["name"]:
            raise ValueError("Unknown tool")
        return {"content": [{"type": "text", "text": f"Status line resource: {URI}"}]}
    if method == "resources/list":
        return {"resources": [{"name": "footer", "uri": URI, "mimeType": "application/json"}]}
    if method == "resources/templates/list":
        return {"resourceTemplates": []}
    if method == "resources/read":
        if params.get("uri") != URI:
            raise ValueError("Unknown resource")
        state = params.get("_meta", {}).get(STATE_KEY, {})
        return {"contents": [{
            "uri": URI,
            "mimeType": "application/json",
            "text": json.dumps(_render(state), ensure_ascii=False),
        }]}
    raise KeyError(method)


def main():
    for line in sys.stdin.buffer:
        reply = {"jsonrpc": "2.0", "id": None}
        try:
            request = json.loads(line)
            if (not isinstance(request, dict) or request.get("jsonrpc") != "2.0"
                    or not isinstance(request.get("method"), str)):
                reply["error"] = {"code": -32600, "message": "Invalid request"}
            elif "id" not in request:
                continue
            else:
                reply["id"] = request["id"]
                reply["result"] = _respond(request["method"], request.get("params", {}))
        except (UnicodeDecodeError, json.JSONDecodeError):
            reply["error"] = {"code": -32700, "message": "Parse error"}
        except KeyError:
            reply["error"] = {"code": -32601, "message": "Method not found"}
        except (TypeError, ValueError, AttributeError):
            reply["error"] = {"code": -32602, "message": "Invalid parameters"}
        print(json.dumps(reply), flush=True)
    return 0

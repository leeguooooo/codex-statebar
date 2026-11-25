import json
import subprocess
import sys


def test_official_codex_statusline_stdio():
    snapshot_key = "com.openai/codex-statusline-state"
    uri = "ui://cxs/footer"

    def request(method, params=None, *, id=None):
        message = {"jsonrpc": "2.0", "method": method}
        if id is not None:
            message["id"] = id
        if params is not None:
            message["params"] = params
        return json.dumps(message)

    model = {"display_name": "GPT‑6"}
    five_hours = {"window_minutes": 300, "used_percent": 0, "resets_at": None}
    weekly = {"window_minutes": 10080, "used_percent": 42.5, "resets_at": None}
    account_limits = {
        "limit_id": "codex", "primary_limit": five_hours, "secondary_limit": weekly,
    }
    first = {
        "thread": {"id": "first", "model": model, "usage": {"context": {"used_percentage": 26}}},
        "account": {"rate_limits": [account_limits]},
    }
    second = {
        "thread": {"id": "second", "model": {"display_name": "GPT‑5"}},
        "account": {"rate_limits": [
            {"limit_id": "other", "primary_limit": five_hours},
            {"limit_id": "codex", "primary_limit": weekly, "secondary_limit": None},
        ]},
    }
    expired = {
        "account": {"rate_limits": [
            dict(account_limits, primary_limit=dict(five_hours, resets_at=1)),
        ]},
    }

    def read(id, snapshot):
        return request("resources/read", {"uri": uri, "_meta": {snapshot_key: snapshot}}, id=id)

    messages = [
        request("initialize", {"protocolVersion": "2025-06-18"}, id=1),
        request("notifications/initialized"),
        request("tools/list", id=2),
        read(3, first),
        read(4, second),
        read(5, expired),
        request("resources/read", {"uri": "ui://other/footer"}, id=6),
    ]
    result = subprocess.run(
        [sys.executable, "-m", "codex_statebar", "mcp-statusline"],
        input="\n".join(messages) + "\n",
        capture_output=True, text=True, check=True, timeout=10,
    )
    assert result.stderr == ""
    replies = [json.loads(line) for line in result.stdout.splitlines()]
    assert [reply["id"] for reply in replies] == [1, 2, 3, 4, 5, 6]
    assert replies[0]["result"]["protocolVersion"] == "2025-06-18"
    tool, = replies[1]["result"]["tools"]
    assert tool["_meta"]["openai/ui"]["entrypoints"] == [
        {"type": "statusline", "resourceUri": uri},
    ]

    def footer(reply):
        resource, = reply["result"]["contents"]
        assert resource["uri"] == uri and resource["mimeType"] == "application/json"
        tree = json.loads(resource["text"])
        assert tree["type"] == "row" and tree["props"]["wrap"] == "wrap"
        return [element["children"][0] for element in tree["children"]]

    assert footer(replies[2]) == ["GPT‑6", "ctx 26% used", "5h 0% used", "7d 42.5% used"]
    assert footer(replies[3]) == ["GPT‑5", "7d 42.5% used"]
    assert footer(replies[4]) == ["Codex", "7d 42.5% used"]
    assert replies[5]["error"]["code"] == -32602

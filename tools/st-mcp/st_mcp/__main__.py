"""Command line: every MCP tool is also a CLI command.

    python -m st_mcp <tool> key=value ...      (JSON on stdout; previews: out=file.png)
    python -m st_mcp serve                     (MCP stdio server)
    python -m st_mcp list                      (tools and their parameters)

Values are parsed as JSON when possible (numbers, true/false, ["a","b"]), else strings.
run_stages waits for completion when used from the command line.
"""
from __future__ import annotations

import inspect
import json
import sys

from . import ops


def _parse(v: str):
    try:
        return json.loads(v)
    except json.JSONDecodeError:
        return v


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd = argv[0]
    if cmd == "serve":
        from .server import main as serve
        serve()
        return 0
    tools = {f.__name__: f for f in ops.TOOLS}
    if cmd == "list":
        for name, f in tools.items():
            print(f"{name}{inspect.signature(f)}\n    {(inspect.getdoc(f) or '').splitlines()[0]}")
        return 0
    if cmd not in tools:
        print(f"unknown tool {cmd}; try: list", file=sys.stderr)
        return 2
    kwargs = {}
    for a in argv[1:]:
        if "=" not in a:
            print(f"arguments must be key=value: {a}", file=sys.stderr)
            return 2
        k, v = a.split("=", 1)
        kwargs[k.lstrip("-").replace("-", "_")] = _parse(v)
    out = kwargs.pop("out", None)
    if cmd == "run_stages":
        kwargs.setdefault("wait_s", 10**9)
    try:
        res = tools[cmd](**kwargs)
    except Exception as e:  # noqa: BLE001 - CLI surface
        print(json.dumps({"error": type(e).__name__, "message": str(e)}, ensure_ascii=False))
        return 1
    if cmd in ops.IMAGE_TOOLS:
        path = out or f"{cmd}.png"
        res.save(path)
        res = {"image": path, "size": list(res.size)}
    print(json.dumps(res, ensure_ascii=False, indent=2))
    if isinstance(res, dict) and res.get("state") == "failed":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

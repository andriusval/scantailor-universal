"""MCP server (stdio): every function in ops.TOOLS becomes one MCP tool."""
from __future__ import annotations

import functools
import inspect

from mcp.server.mcpserver import Image as McpImage
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import ops
from .preview import to_png

INSTRUCTIONS = """\
ScanTailor Universal for agents: turns a folder of book scans into clean page TIFFs.
Start with workflow_guide and follow it step by step. Look at previews before
deciding anything; ask the user at the approval points the guide lists.
Long stages run in the background: run_stages returns a job, poll job_status(wait_s=60).
Pages can be referred to by id ("9") or label ("0100_2R").
"""


def _wrap_errors(fn):
    """Turn any exception into a ToolError so the agent sees the reason, not a generic failure."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ToolError:
            raise
        except Exception as e:  # noqa: BLE001 - reported to the client
            raise ToolError(f"{type(e).__name__}: {e}") from e
    return wrapper


def _wrap_image_tool(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return McpImage(data=to_png(fn(*args, **kwargs)), format="png")

    sig = inspect.signature(fn)
    wrapper.__signature__ = sig.replace(return_annotation=McpImage)  # type: ignore[attr-defined]
    wrapper.__annotations__ = {**fn.__annotations__, "return": McpImage}
    return wrapper


def build() -> MCPServer:
    server = MCPServer(name="scantailor", instructions=INSTRUCTIONS)
    for fn in ops.TOOLS:
        f = _wrap_image_tool(fn) if fn.__name__ in ops.IMAGE_TOOLS else fn
        server.tool(name=fn.__name__, description=inspect.getdoc(fn))(_wrap_errors(f))
    return server


def main() -> None:
    build().run("stdio")


if __name__ == "__main__":
    main()

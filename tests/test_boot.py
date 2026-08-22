"""Boot/integration tests for the mcp-medusa server.

These run WITHOUT Docker: they install the package + fastmcp in a venv and
actually start the server process, asserting it boots on both the stdio and
SSE transports and that the expected tools (including `postprocess`) are
registered. This is the gate that catches the class of crash we hit when
migrating to fastmcp 3.x (e.g. AttributeError on mcp.settings, or
run_stdio_async rejecting host/port kwargs).

Run locally:
    uv venv && uv pip install -e ".[test]"
    pytest tests/test_boot.py
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Expected tool count after the fastmcp 3.x migration:
# 20 original tools + 1 (postprocess).
EXPECTED_TOOL_COUNT = 21
EXPECTED_TOOLS = {"postprocess"}


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_for_sse(port: int, timeout: float = 20.0) -> bool:
    """Poll the SSE endpoint until it returns 200 (server bound) or timeout."""
    url = f"http://127.0.0.1:{port}/sse"
    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception as e:  # noqa: BLE001 - urlopen raises many types
            last_err = e
            time.sleep(0.5)
    if last_err:
        print(f"[test] last SSE poll error: {last_err!r}")
    return False


def _start_server(transport: str, port: int) -> subprocess.Popen:
    """Start the server as a subprocess on the given transport/port."""
    env = dict(os.environ)
    env.setdefault("MEDUSA_URL", "http://localhost:8081")
    env.setdefault("MEDUSA_API_KEY", "dummy")
    cmd = [
        sys.executable,
        "-m",
        "mcp_medusa.server",
        "--transport",
        transport,
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    return subprocess.Popen(
        cmd,
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


@pytest.fixture
def port():
    return _free_port()


def test_module_imports_and_registers_postprocess():
    """Importing the module must construct FastMCP and register tools.

    This is the exact failure mode we hit: a bad import or constructor call
    (e.g. mcp.server.fastmcp removal, or mcp.settings AttributeError) would
    raise here at import time.
    """
    import asyncio
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "mcp_medusa_server_test",
        os.path.join(REPO_ROOT, "src", "mcp_medusa", "server.py"),
    )
    assert spec is not None and spec.loader is not None, "spec_from_file_location failed"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # FastMCP instance must exist
    assert hasattr(mod, "mcp"), "FastMCP instance 'mcp' not found"

    # Public API: list_tools() returns the registered tools
    tools = asyncio.run(mod.mcp.list_tools())
    registered = {t.name for t in tools}
    assert EXPECTED_TOOLS.issubset(registered), (
        f"Missing expected tools {EXPECTED_TOOLS - registered}; "
        f"registered={sorted(registered)}"
    )
    # Sanity: total tool count matches expectation
    assert len(registered) == EXPECTED_TOOL_COUNT, (
        f"Expected {EXPECTED_TOOL_COUNT} tools, got {len(registered)}: "
        f"{sorted(registered)}"
    )


def test_server_boots_sse(port):
    """The server must actually start and bind on the SSE transport."""
    proc = _start_server("sse", port)
    try:
        assert _wait_for_sse(port, timeout=25.0), (
            "Server did not bind SSE endpoint within timeout"
        )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_server_boots_stdio():
    """The server must start on the default stdio transport without crashing.

    stdio reads from stdin, so we feed EOF (empty) and assert the process
    exits cleanly (code 0) rather than erroring on boot. A boot crash would
    produce a non-zero exit / traceback.
    """
    env = dict(os.environ)
    env.setdefault("MEDUSA_URL", "http://localhost:8081")
    env.setdefault("MEDUSA_API_KEY", "dummy")
    proc = subprocess.Popen(
        [sys.executable, "-m", "mcp_medusa.server", "--transport", "stdio"],
        cwd=REPO_ROOT,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    # Feed EOF (empty stdin) so the stdio transport exits cleanly, then wait.
    try:
        out, _ = proc.communicate(input="", timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
        pytest.fail("stdio server hung instead of exiting on EOF")

    # A boot crash would leave a traceback in stdout/stderr
    assert "Traceback" not in (out or ""), f"stdio boot crashed:\n{out}"
    assert "AttributeError" not in (out or ""), f"stdio boot AttributeError:\n{out}"
    assert "TypeError" not in (out or ""), f"stdio boot TypeError:\n{out}"
    # Exit 0 = clean shutdown after EOF (success); non-zero = boot failure
    assert proc.returncode in (0, None) or proc.returncode == 0, (
        f"stdio server exited {proc.returncode}:\n{out}"
    )

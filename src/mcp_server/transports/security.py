"""Checks shared by the HTTP transports, so other websites can't drive this local server."""

import re

from fastapi import Request
from fastapi.responses import Response

# Browser pages served from this machine, on any port (e.g. the MCP Inspector on :6274)
LOCAL_ORIGIN_REGEX = r"https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?"
LOCAL_HOSTNAMES = {"localhost", "127.0.0.1", "::1"}


def check_host_and_origin(request: Request) -> Response | None:
    """Return an error response if the request didn't come from this machine, else None.

    Host: in a DNS-rebinding attack, evil.example is made to resolve to 127.0.0.1,
    so the browser reaches us but still sends `Host: evil.example:8000`.
    Origin: browsers send it on cross-site requests. Non-browser clients (the
    Inspector's proxy, Claude Desktop, curl) usually send none, so a missing
    Origin is allowed."""
    if request.url.hostname not in LOCAL_HOSTNAMES:
        return Response("Invalid Host header", status_code=421)
    origin = request.headers.get("origin")
    if origin is not None and not re.fullmatch(LOCAL_ORIGIN_REGEX, origin):
        return Response("Invalid Origin header", status_code=403)
    return None

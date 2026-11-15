import logging

from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger(__name__)

SERVER_INFO = {"name": "my-mcp-server", "version": "1.0.0"}

# --- Protocol versions ---
# Handshake era: the client must call `initialize` first, and the version agreed
# there holds for the whole session.
HANDSHAKE_VERSIONS = ["2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"]
# Stateless era (2026-07-28): no handshake. Every request carries its own protocol
# version and client capabilities in `params._meta`, and `server/discover`
# replaces `initialize` for asking what the server supports.
STATELESS_VERSIONS = ["2026-07-28"]

# Reserved `_meta` keys used by the 2026-07-28 per-request envelope
PROTOCOL_VERSION_KEY = "io.modelcontextprotocol/protocolVersion"
CLIENT_CAPABILITIES_KEY = "io.modelcontextprotocol/clientCapabilities"
SERVER_INFO_KEY = "io.modelcontextprotocol/serverInfo"

# Which methods exist in which era
HANDSHAKE_METHODS = {"initialize", "ping", "tools/list", "tools/call"}
STATELESS_METHODS = {"server/discover", "tools/list", "tools/call"}

HANDSHAKE = "handshake"
STATELESS = "stateless"

# --- JSON-RPC error codes ---
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
UNSUPPORTED_PROTOCOL_VERSION = -32022  # MCP-specific, added in 2026-07-28


# --- Tools ---
class AddArguments(BaseModel):
    # strict: "5" is not an integer. Without it Pydantic would quietly convert it.
    model_config = ConfigDict(strict=True)

    a: int = Field(description="First number")
    b: int = Field(description="Second number")


def tool_add(args: AddArguments) -> str:
    return str(args.a + args.b)


# --- Helpers ---
class RequestError(Exception):
    """Raise this anywhere inside the dispatcher to answer with a JSON-RPC error."""

    def __init__(self, code: int, message: str, data=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def error_response(request_id, code: int, message: str, data=None) -> dict:
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def tool_result(text: str, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def is_stateless_request(message) -> bool:
    """True if this is a 2026-07-28 request: its params._meta names a protocol version.

    `initialize` never counts, since that method only exists in the handshake era."""
    if not isinstance(message, dict) or message.get("method") == "initialize":
        return False
    params = message.get("params")
    meta = params.get("_meta") if isinstance(params, dict) else None
    return isinstance(meta, dict) and PROTOCOL_VERSION_KEY in meta


def check_envelope(params: dict) -> None:
    """Validate the 2026-07-28 per-request envelope in params._meta."""
    meta = params.get("_meta")
    if not isinstance(meta, dict) or PROTOCOL_VERSION_KEY not in meta or CLIENT_CAPABILITIES_KEY not in meta:
        raise RequestError(
            INVALID_PARAMS, f"params._meta must carry {PROTOCOL_VERSION_KEY!r} and {CLIENT_CAPABILITIES_KEY!r}"
        )
    version = meta[PROTOCOL_VERSION_KEY]
    if not isinstance(version, str):
        raise RequestError(INVALID_PARAMS, "The protocol version in params._meta must be a string")
    if version not in STATELESS_VERSIONS:
        raise RequestError(
            UNSUPPORTED_PROTOCOL_VERSION,
            "Unsupported protocol version",
            {"supported": STATELESS_VERSIONS, "requested": version},
        )


class Session:
    """State for one client connection.

    stdio has one per process, SSE one per /sse stream, and Streamable HTTP one per
    Mcp-Session-Id. The first request picks the era: `initialize` starts a handshake
    session, a request with the 2026-07-28 `_meta` envelope starts a stateless one.
    The era can't change after that."""

    def __init__(self, era: str | None = None):
        self.era = era
        self.protocol_version = None  # agreed during `initialize`
        # Set by notifications/initialized. The spec only lets the *server* start
        # sending its own requests (sampling, elicitation...) after this, so
        # client requests are allowed as soon as `initialize` has been answered.
        self.initialized = False


class Dispatcher:
    """The transport-agnostic core. It doesn't know about stdio or HTTP.
    It takes a JSON message plus that connection's Session, and returns the
    JSON response (or None when no response should be sent)."""

    def __init__(self):
        self.registered_tools = {
            "add": {
                "description": "Add two integers together.",
                "input_model": AddArguments,
                "handler": tool_add,
            }
        }

    def handle_request(self, message, session: Session) -> dict | None:
        if not isinstance(message, dict):
            # JSON-RPC batches (arrays) were removed from MCP in 2025-06-18
            return error_response(None, INVALID_REQUEST, "Invalid Request: expected a single JSON object")

        if "method" not in message and ("result" in message or "error" in message):
            # A response from the client. We never send requests, so there's nothing to match it to.
            return None

        # Notifications have no id and never get a reply
        if "id" not in message:
            self.handle_notification(message, session)
            return None

        request_id = message["id"]
        if isinstance(request_id, bool) or not isinstance(request_id, (str, int)):
            return error_response(None, INVALID_REQUEST, "Invalid Request: id must be a string or an integer")

        try:
            result = self.route(message, session)
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except RequestError as e:
            return error_response(request_id, e.code, e.message, e.data)
        except Exception:
            # Safety net: a bug in the server costs one error reply, never the whole connection
            logger.exception("Unhandled error while processing %r", message.get("method"))
            return error_response(request_id, INTERNAL_ERROR, "Internal error")

    def handle_notification(self, message: dict, session: Session) -> None:
        method = message.get("method")
        if method == "notifications/initialized":
            if session.protocol_version is None:
                logger.warning("Ignoring notifications/initialized: the client hasn't sent initialize yet")
            else:
                session.initialized = True
                logger.info("Handshake complete (protocol %s)", session.protocol_version)
        else:
            logger.debug("Ignoring notification %r", method)

    def route(self, message: dict, session: Session) -> dict:
        method = message.get("method")
        params = message.get("params")
        if message.get("jsonrpc") != "2.0" or not isinstance(method, str):
            raise RequestError(INVALID_REQUEST, 'Invalid Request: needs "jsonrpc": "2.0" and a string method')
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise RequestError(INVALID_PARAMS, "params must be an object")

        # The first request decides which era this session speaks
        if session.era is None:
            session.era = STATELESS if is_stateless_request(message) else HANDSHAKE

        if session.era == STATELESS:
            return self.handle_stateless(method, params)
        return self.handle_handshake(message, method, params, session)

    # 1. Handshake era (2024-11-05 ... 2025-11-25)
    def handle_handshake(self, message: dict, method: str, params: dict, session: Session) -> dict:
        if is_stateless_request(message):
            raise RequestError(
                INVALID_REQUEST,
                "This session uses the initialize handshake; "
                "requests with the 2026-07-28 _meta envelope aren't accepted",
            )
        if method not in HANDSHAKE_METHODS:
            raise RequestError(METHOD_NOT_FOUND, "Method not found", method)

        if method == "initialize":
            return self.initialize(params, session)
        if method == "ping":
            return {}

        # Everything else needs a finished handshake first
        if session.protocol_version is None:
            raise RequestError(INVALID_REQUEST, "Server not initialized: send 'initialize' first")
        if method == "tools/list":
            return self.list_tools()
        return self.call_tool(params)

    def initialize(self, params: dict, session: Session) -> dict:
        if session.protocol_version is not None:
            raise RequestError(INVALID_REQUEST, "Session is already initialized")

        requested = params.get("protocolVersion")
        if (
            not isinstance(requested, str)
            or not isinstance(params.get("capabilities"), dict)
            or not isinstance(params.get("clientInfo"), dict)
        ):
            raise RequestError(INVALID_PARAMS, "initialize needs protocolVersion, capabilities and clientInfo")

        # Version negotiation: use the client's version if we support it,
        # otherwise offer our newest and let the client decide whether to disconnect.
        negotiated = requested if requested in HANDSHAKE_VERSIONS else HANDSHAKE_VERSIONS[-1]
        session.protocol_version = negotiated
        client_name = params["clientInfo"].get("name")
        logger.info("initialize from %r: client asked for %s, using %s", client_name, requested, negotiated)

        return {
            "protocolVersion": negotiated,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        }

    # 2. Stateless era (2026-07-28)
    def handle_stateless(self, method: str, params: dict) -> dict:
        if method == "initialize":
            raise RequestError(
                UNSUPPORTED_PROTOCOL_VERSION,
                "This session uses the 2026-07-28 protocol, which has no initialize handshake",
                {"supported": STATELESS_VERSIONS},
            )
        check_envelope(params)
        if method not in STATELESS_METHODS:
            raise RequestError(METHOD_NOT_FOUND, "Method not found", method)

        if method == "server/discover":
            result = {"supportedVersions": STATELESS_VERSIONS, "capabilities": {"tools": {}}}
        elif method == "tools/list":
            result = self.list_tools()
        else:
            result = self.call_tool(params)

        if method in ("server/discover", "tools/list"):
            # These results are cacheable in 2026-07-28. 0 ms + private = "don't cache".
            result["ttlMs"] = 0
            result["cacheScope"] = "private"
        # There's no initialize response to carry serverInfo any more, so every
        # result names the server and says it is final.
        result["resultType"] = "complete"
        result["_meta"] = {SERVER_INFO_KEY: SERVER_INFO}
        return result

    # 3. Tools (same in both eras)
    def list_tools(self) -> dict:
        return {
            "tools": [
                {
                    "name": name,
                    "description": tool["description"],
                    "inputSchema": tool["input_model"].model_json_schema(),
                }
                for name, tool in self.registered_tools.items()
            ]
        }

    def call_tool(self, params: dict) -> dict:
        name = params.get("name")
        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(name, str):
            raise RequestError(INVALID_PARAMS, "tools/call needs params.name (the tool name)")
        if name not in self.registered_tools:
            raise RequestError(INVALID_PARAMS, f"Unknown tool: {name}")
        if not isinstance(arguments, dict):
            raise RequestError(INVALID_PARAMS, "Tool arguments must be an object")
        tool = self.registered_tools[name]

        # From here on, problems are *tool* errors, not protocol errors: they go back
        # as a normal result with isError: true, so the model can read them and retry.
        try:
            args = tool["input_model"].model_validate(arguments)
        except ValidationError as e:
            problems = "; ".join(
                f"{'.'.join(str(part) for part in err['loc']) or 'arguments'}: {err['msg']}" for err in e.errors()
            )
            return tool_result(f"Invalid arguments for '{name}': {problems}", is_error=True)

        try:
            answer = tool["handler"](args)
        except Exception as e:
            logger.exception("Tool %r failed", name)
            return tool_result(f"Tool '{name}' failed: {e}", is_error=True)
        return tool_result(answer)

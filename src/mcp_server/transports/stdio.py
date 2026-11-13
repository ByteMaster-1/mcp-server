import sys
import json
import logging
from ..core import PARSE_ERROR, Dispatcher, Session, error_response


def send(response: dict) -> None:
    sys.stdout.write(json.dumps(response) + "\n")
    sys.stdout.flush()


def main():
    # stdout is the protocol channel, so logs must go to stderr
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    dispatch = Dispatcher()
    # stdio serves exactly one client (the process that launched us), so one session
    session = Session()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            # Reply and keep reading: one bad line must not take the server down
            send(error_response(None, PARSE_ERROR, "Parse error"))
            continue

        response_dict = dispatch.handle_request(message, session)
        # Notifications (and responses from the client) get no reply
        if response_dict is not None:
            send(response_dict)


if __name__ == "__main__":
    main()

import sys
import json
from ..core import Dispatcher


def main():
    dispatch = Dispatcher()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
            
        message = json.loads(line)
        
        # Check if it's a request (has an 'id' and 'method')
        if "id" in message and "method" in message:
            request_id = message["id"]
            method = message["method"]
            
            response_dict=dispatch.handle_request(message)
            
            sys.stdout.write(json.dumps(response_dict)+"\n")
            sys.stdout.flush()



if __name__ == "__main__":
    main()

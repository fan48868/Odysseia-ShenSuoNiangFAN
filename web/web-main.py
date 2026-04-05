import os
import sys

import uvicorn

current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from web_main import web_app


if __name__ == "__main__":
    port = int(os.getenv("CONFIG_WEB_PORT", "8080"))
    uvicorn.run(web_app, host="0.0.0.0", port=port)

import uvicorn
import os
import sys

if __name__ == "__main__":
    current_dir = os.path.dirname(os.path.abspath(__file__))
    if current_dir not in sys.path:
        sys.path.insert(0, current_dir)
    print("==================================================")
    print("  SOLANA AI VOLUME SUITE: COMMAND CENTER STARTING ")
    print("  URL: http://127.0.0.1:8000                     ")
    print("==================================================")
    uvicorn.run("dashboard.app:app", host="0.0.0.0", port=8000, reload=False)

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
    sys.path.insert(0, os.path.dirname(current_dir))
    uvicorn.run("solana_volume_suite.dashboard.safety_app:app", host="127.0.0.1", port=8000, reload=False)

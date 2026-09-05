"""Launch the virtual-only dashboard on loopback with a fresh local access token."""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from solana_volume_suite.core.security import audit, generate_password, require_virtual_mode, validate_password


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8501)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--mainnet", action="store_true", help="Blocked in this build")
    parser.add_argument("--setup", action="store_true", help="Legacy mainnet setup; blocked")
    args = parser.parse_args()
    if args.mainnet or args.setup:
        audit("SECURITY_VIOLATION", reason="MAINNET_LAUNCH_BLOCKED")
        parser.exit(2, "VIRTUAL_ONLY: mainnet and mainnet setup are disabled.\n")
    # Read only explicitly allowed local settings. Never import provider credentials.
    allowed = {"DASHBOARD_API_TOKEN", "LIVE_EXECUTION_ENABLED", "PAPER_TRADING",
               "GEMINI_REAL_MONEY_READY", "MAX_ALLOWED_LOSS_USD", "SOLANA_RPC_URL",
               "SOLANA_WSS_URL", "JITO_BLOCK_ENGINE_URL"}
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            name, sep, value = line.partition("=")
            if sep and name.strip() in allowed:
                os.environ.setdefault(name.strip(), value.split(" #", 1)[0].strip().strip("\"'"))
    require_virtual_mode()
    if not os.getenv("DASHBOARD_API_TOKEN"):
        os.environ["DASHBOARD_API_TOKEN"] = generate_password()
        print("Temporary local dashboard token (paste into the dashboard):")
        print(os.environ["DASHBOARD_API_TOKEN"])
    validate_password(os.environ["DASHBOARD_API_TOKEN"])
    url = f"http://127.0.0.1:{args.port}"
    print(f"Virtual-only dashboard: {url}\nNo keys, signing or live execution. Ctrl+C saves state and stops.")
    if not args.no_browser:
        import threading
        import webbrowser
        timer = threading.Timer(1, lambda: webbrowser.open(url))
        timer.daemon = True
        timer.start()
    import uvicorn
    uvicorn.run("solana_volume_suite.dashboard.app:app", host="127.0.0.1", port=args.port,
                workers=1, proxy_headers=False, ws_max_size=8192, ws_max_queue=4,
                timeout_graceful_shutdown=5)


if __name__ == "__main__":
    main()

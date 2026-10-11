"""Launch the local, paper-only Solana Volume Suite dashboard."""
import argparse
import os
import sys
import threading
import webbrowser


REQUIRED_DEPENDENCIES = ["solders", "fastapi", "uvicorn", "cryptography", "pydantic"]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Solana Volume Suite (paper-only)")
    parser.add_argument("--mainnet", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--setup", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, default=8501, help="Port for the local dashboard")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser automatically")
    args = parser.parse_args(argv)

    # Reject unsafe modes before importing the dashboard or any wallet/network code.
    if args.mainnet or args.setup:
        parser.error("live trading and mainnet setup are disabled; this launcher is paper-only")

    missing = []
    for dep in REQUIRED_DEPENDENCIES:
        try:
            __import__(dep)
        except ImportError:
            missing.append(dep)
    if missing:
        print(f"[-] FATAL: Missing required dependencies: {', '.join(missing)}")
        print("[-] Please install via: pip install -r requirements.txt")
        return 1

    suite_root = os.path.dirname(os.path.abspath(__file__))
    workspace_root = os.path.dirname(suite_root)
    for path in (suite_root, workspace_root):
        if path not in sys.path:
            sys.path.insert(0, path)

    import uvicorn
    from dashboard.safety_app import app, orchestrator

    print("=" * 68)
    print("   SOLANA VOLUME SUITE // PAPER-ONLY LOCAL DASHBOARD   ")
    print("=" * 68)
    print(" [i] Execution Mode:     PAPER_TRADING_ONLY")
    print(" [i] Real wallets:       DISABLED")
    print(" [i] On-chain sending:   DISABLED")
    print("-" * 68)

    orchestrator.initialize_vault_pool(count=10)
    print(f"[+] Ready with {len(orchestrator.sub_wallet_addresses)} virtual wallets.")

    url = f"http://127.0.0.1:{args.port}"
    print(f"[+] Starting local dashboard on {url}")
    print("[+] Press Ctrl+C to shut down.")
    if not args.no_browser:
        def open_browser():
            try:
                webbrowser.open(url)
            except Exception:
                pass
        threading.Timer(1.2, open_browser).start()

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

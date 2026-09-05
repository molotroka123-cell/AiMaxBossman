"""Interactive Prototype Launcher for Solana AI Volume Suite.

Starts the Bloomberg/Cyberpunk Dashboard on http://127.0.0.1:8501 with real-time
WebSocket telemetry, Zero-Knowledge KeyVault initialization, and fail-closed safety.
"""
import os
import sys
import threading
import webbrowser

# 1. Dependency Verification Gate
REQUIRED_DEPENDENCIES = ["solders", "fastapi", "uvicorn", "cryptography", "pydantic"]
missing = []
for dep in REQUIRED_DEPENDENCIES:
    try:
        __import__(dep)
    except ImportError:
        missing.append(dep)

if missing:
    print(f"[-] FATAL: Missing required dependencies: {', '.join(missing)}")
    print("[-] Please install via: pip install -r requirements.txt")
    sys.exit(1)

# Ensure directory paths are in sys.path
SUITE_ROOT = os.path.dirname(os.path.abspath(__file__))
if SUITE_ROOT not in sys.path:
    sys.path.insert(0, SUITE_ROOT)
WORKSPACE_ROOT = os.path.dirname(SUITE_ROOT)
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

import uvicorn
from orchestrator_loop import VolumeOrchestratorLoop
from dashboard.safety_app import app, orchestrator


def main():
    print("=" * 68)
    print("   SOLANA AI VOLUME SUITE // INTERACTIVE QUANTITATIVE DESK   ")
    print("=" * 68)
    print(" [i] Architecture:       Zero-Knowledge + Fail-Closed Safety")
    print(" [i] Execution Mode:     PAPER_TRADING_ONLY")
    print(" [i] Liquidity Guard:    Raydium/DexScreener Price Impact <= 1.2%")
    print(" [i] Circuit Breaker:    Max Allowed Loss: $40.00 USD")
    print(" [i] KeyVault Security:  PBKDF2-SHA256 (100k) + AES-256-GCM")
    print("-" * 68)

    # 2. Automatically initialize default 10-wallet pool if needed
    print("[*] Initializing Zero-Knowledge Encrypted Sub-Wallet Pool...")
    try:
        orchestrator.initialize_vault_pool(count=10)
        wallet_count = len(orchestrator.cached_keypairs)
        print(f"[+] Vault ready with {wallet_count} encrypted sub-wallets.")
    except Exception as e:
        print(f"[!] Warning during vault pool initialization: {e}")

    # 3. Schedule automated browser launch
    url = "http://127.0.0.1:8501"
    print(f"[+] Starting UI Command Center on {url}")
    print("[+] Press Ctrl+C at any time to shutdown.")
    print("=" * 68)

    def open_browser():
        try:
            webbrowser.open(url)
        except Exception:
            pass

    threading.Timer(1.2, open_browser).start()

    # 4. Start Uvicorn ASGI Server
    uvicorn.run(app, host="127.0.0.1", port=8501, log_level="info")


if __name__ == "__main__":
    main()

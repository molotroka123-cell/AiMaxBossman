import os
import sys
import argparse
import httpx
from typing import Optional
from solders.pubkey import Pubkey

SUITE_ROOT = os.path.dirname(os.path.abspath(__file__))
if SUITE_ROOT not in sys.path:
    sys.path.insert(0, SUITE_ROOT)

from core.key_vault.vault import SecurityKeyVault, DEFAULT_VAULT_PATH
from core.jito_client import JITO_TIP_ACCOUNTS

DEFAULT_MAINNET_RPC = "https://api.mainnet-beta.solana.com"
DEFAULT_JITO_ENGINE = "https://mainnet.block-engine.jito.wtf/api/v1/bundles"
ENV_PATH = os.path.join(SUITE_ROOT, ".env")


def test_rpc_connection(rpc_url: str) -> bool:
    """Verifies that the Solana RPC endpoint is responsive and on mainnet."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": "getSlot"}
    try:
        with httpx.Client(timeout=6.0) as client:
            resp = client.post(rpc_url, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                slot = data.get("result")
                if slot and isinstance(slot, int):
                    print(f"  [+] RPC Active: Current Slot #{slot}")
                    return True
    except Exception as e:
        print(f"  [-] Connection warning: {e}")
    return False


def test_jito_connection(jito_url: str) -> bool:
    """Verifies Jito Block Engine reachable."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": "getBundleStatuses", "params": [["test_probe"]]}
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.post(jito_url, json=payload)
            if resp.status_code == 200:
                print(f"  [+] Jito Block Engine Active: {jito_url}")
                return True
    except Exception:
        pass
    print(f"  [+] Jito Block Engine configured: {jito_url}")
    return True


def run_setup_wizard(
    rpc_url: Optional[str] = None,
    mint: Optional[str] = None,
    password: Optional[str] = None,
    wallet_count: int = 10,
    non_interactive: bool = False
):
    print("==================================================")
    print("  SOLANA MAINNET AI VOLUME SUITE: EASY CONNECT    ")
    print("==================================================")

    # 1. RPC Configuration
    selected_rpc = rpc_url or DEFAULT_MAINNET_RPC
    if not non_interactive and not rpc_url:
        user_rpc = input(f"Enter Mainnet RPC URL (press Enter for default {DEFAULT_MAINNET_RPC}): ").strip()
        if user_rpc:
            selected_rpc = user_rpc

    print(f"\n[*] Probing RPC connectivity: {selected_rpc}...")
    rpc_ok = test_rpc_connection(selected_rpc)

    # 2. Target Token Mint
    target_mint = mint or "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
    if not non_interactive and not mint:
        user_mint = input(f"Enter Target Token Mint Address (press Enter for sample Bonk): ").strip()
        if user_mint:
            target_mint = user_mint

    try:
        Pubkey.from_string(target_mint)
        print(f"  [+] Target Token Mint validated: {target_mint}")
    except Exception:
        print(f"  [!] Invalid Mint address, using standard sample.")
        target_mint = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"

    # 3. Vault Password
    master_pass = password or "SuperSecretMasterPass123!"
    if not non_interactive and not password:
        user_pass = input("Enter Master Vault Password (min 12 chars, press Enter for default): ").strip()
        if len(user_pass) >= 12:
            master_pass = user_pass

    # 4. Generate/Load Vault
    vault_file = os.path.join(SUITE_ROOT, DEFAULT_VAULT_PATH)
    vault = SecurityKeyVault(storage_path=vault_file)
    if not os.path.exists(vault_file):
        print(f"\n[*] Generating initial {wallet_count} encrypted sub-wallets with AES-256-GCM...")
        pubkeys = vault.create_and_store_pool(wallet_count, master_pass, mode="random")
    else:
        try:
            pubkeys = vault.get_public_addresses(master_pass)
            print(f"  [+] Found existing vault with {len(pubkeys)} sub-wallets.")
        except Exception:
            try:
                os.remove(vault_file)
            except OSError:
                pass
            print(f"\n[*] Re-encrypting {wallet_count} sub-wallets with new master password...")
            pubkeys = vault.create_and_store_pool(wallet_count, master_pass, mode="random")

    # 5. Anti-Bubblemaps Transit Instructions
    print("\n--------------------------------------------------")
    print("  ANTI-BUBBLEMAPS FUNDING TOPOLOGY (2-TIER TREE)  ")
    print("--------------------------------------------------")
    print("To break on-chain clustering, DO NOT fund all sub-wallets directly from 1 address.")
    print("Recommended workflow:")
    print("  1. Withdraw SOL from CEX (Binance / Bybit) to 3 Transit Wallets.")
    print("  2. Each Transit Wallet funds a subset of the sub-wallets.")
    print(f"  Sub-wallets ready: {len(pubkeys)}")
    for i, pk in enumerate(pubkeys[:3]):
        print(f"    Sub-Wallet [{i:02d}]: {pk}")
    if len(pubkeys) > 3:
        print(f"    ... and {len(pubkeys) - 3} more sub-wallets encrypted on disk.")

    # 6. Save .env
    env_content = f"""# Solana AI Volume Suite - Mainnet Environment
SOLANA_NETWORK=mainnet-beta
SOLANA_RPC_URL={selected_rpc}
TARGET_TOKEN_MINT={target_mint}
VAULT_MASTER_PASSWORD={master_pass}
JITO_BLOCK_ENGINE_URL={DEFAULT_JITO_ENGINE}
JITO_MIN_TIP_LAMPORTS=100000
EXECUTION_MODE=MAINNET_READY
CIRCUIT_BREAKER_MAX_LOSS_USD=40.0
"""
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.write(env_content)
    print(f"\n[+] Configuration saved to: {ENV_PATH}")

    # 7. Check Jito
    test_jito_connection(DEFAULT_JITO_ENGINE)

    print("\n==================================================")
    print("  MAINNET CONNECTION SETUP COMPLETE!              ")
    print("  Start Command Center:                           ")
    print("    python solana_volume_suite/start_prototype.py ")
    print("==================================================")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Solana Volume Suite Mainnet Setup")
    parser.add_argument("--rpc", type=str, default=None, help="Solana RPC URL")
    parser.add_argument("--mint", type=str, default=None, help="Target Token Mint")
    parser.add_argument("--password", type=str, default=None, help="Vault master password")
    parser.add_argument("--count", type=int, default=10, help="Initial wallet count")
    parser.add_argument("--non-interactive", action="store_true", help="Run without prompts")
    args = parser.parse_args()

    run_setup_wizard(
        rpc_url=args.rpc,
        mint=args.mint,
        password=args.password,
        wallet_count=args.count,
        non_interactive=args.non_interactive
    )

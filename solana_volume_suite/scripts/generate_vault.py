import argparse
import getpass
import os
import sys

# Ensure parent directory is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.key_vault.vault import SecurityKeyVault, DEFAULT_VAULT_PATH


def main():
    parser = argparse.ArgumentParser(description="Solana AI Volume Suite - Zero-Knowledge KeyVault Generator")
    parser.add_argument("--count", "-c", type=int, default=20, help="Number of sub-wallets to generate (10-50 recommended)")
    parser.add_argument("--password", "-p", type=str, default=None, help="Master encryption password (PBKDF2-SHA256 100k + AES-256-GCM)")
    parser.add_argument("--mode", "-m", type=str, choices=["random", "hd_bip44"], default="random", help="Generation mode: random Keypairs or HD BIP-44")
    parser.add_argument("--path", type=str, default=DEFAULT_VAULT_PATH, help="Path for output encrypted JSON file")
    args = parser.parse_args()

    password = args.password
    if not password:
        password = getpass.getpass("Enter master password for vault encryption: ")
        if len(password) < 6:
            print("[-] Error: Master password must be at least 6 characters.")
            sys.exit(1)

    print("==================================================")
    print("   SOLANA ZERO-KNOWLEDGE KEYVAULT GENERATOR       ")
    print("==================================================")
    print(f"[*] Sub-wallet count: {args.count}")
    print(f"[*] Derivation mode:  {args.mode}")
    print(f"[*] Output path:      {args.path}")
    print(f"[*] Encryption:       AES-256-GCM (PBKDF2 100,000 iterations)")

    vault = SecurityKeyVault(storage_path=args.path)
    pubkeys = vault.create_and_store_pool(count=args.count, password=password, mode=args.mode)

    print(f"\n[OK] SUCCESS: Generated and encrypted {len(pubkeys)} sub-wallets.")
    print("Public Addresses:")
    for idx, pk in enumerate(pubkeys):
        print(f"  [{idx:02d}] {pk}")

    print("\n[SECURITY NOTICE]: Raw private keys are sealed with AES-256-GCM on disk.")
    print("AI Orchestrator operates under Zero-Knowledge constraints with virtual indices.")


if __name__ == "__main__":
    main()

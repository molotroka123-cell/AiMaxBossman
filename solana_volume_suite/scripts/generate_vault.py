import argparse
import getpass
import os
import shutil
import sys
from datetime import datetime, timezone

# Ensure parent directory is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.key_vault.vault import SecurityKeyVault, DEFAULT_VAULT_PATH, MIN_PASSWORD_LENGTH


def main():
    parser = argparse.ArgumentParser(description="Solana AI Volume Suite - Zero-Knowledge KeyVault Generator")
    parser.add_argument("--count", "-c", type=int, default=20, help="Number of sub-wallets to generate (10-50 recommended)")
    # There is deliberately NO --password flag: a password on argv is visible in
    # the process list and shell history. It is read with getpass only.
    parser.add_argument("--mode", "-m", type=str, choices=["random", "hd_bip44"], default="random", help="Generation mode: random Keypairs or HD BIP-44")
    parser.add_argument("--path", type=str, default=DEFAULT_VAULT_PATH, help="Path for output encrypted JSON file")
    args = parser.parse_args()

    # Existing vault: never overwrite or delete. Keep a timestamped copy and stop.
    if os.path.exists(args.path):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = f"{args.path}.bak-{stamp}"
        shutil.copy2(args.path, backup)
        print(f"[-] Refusing: vault already exists at {args.path}. Backup copy: {backup}. "
              "Choose another --path or move the file yourself.")
        sys.exit(3)

    password = getpass.getpass("Enter master password for vault encryption: ")
    if len(password) < MIN_PASSWORD_LENGTH:
        print(f"[-] Error: Master password must be at least {MIN_PASSWORD_LENGTH} characters.")
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
    print("The password is not stored anywhere; losing it means losing the keys.")


if __name__ == "__main__":
    main()

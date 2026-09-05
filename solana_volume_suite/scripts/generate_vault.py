"""Generate an encrypted fixture of publicly known mock wallets. Never fund them."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from solana_volume_suite.core.security import generate_password, require_virtual_mode
from solana_volume_suite.core.key_vault.vault import SecurityKeyVault


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--path", default="mock_wallets_encrypted.json")
    args = parser.parse_args()
    require_virtual_mode()
    password = generate_password()
    try:
        public = SecurityKeyVault(args.path).create_and_store_pool(args.count, password)
    except (ValueError, FileExistsError) as exc:
        parser.exit(1, f"Mock fixture not created: {exc}\n")
    print(f"Created {len(public)} PUBLICLY KNOWN mock wallets. NEVER FUND THESE ADDRESSES.")
    print("Generated mock fixture password (displayed once):")
    print(password)


if __name__ == "__main__":
    main()

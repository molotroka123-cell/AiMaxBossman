"""Fail-closed compatibility stub for the retired Mainnet setup wizard."""
import argparse
import sys


DISABLED_REASON = (
    "Mainnet setup is disabled in this repository: no RPC probing, key generation, "
    ".env writing, or live trading is performed. Use the paper-only dashboard."
)


def run_setup_wizard(*_args, **_kwargs):
    """Reject old callers without touching the network, wallet vault, or filesystem."""
    raise RuntimeError(DISABLED_REASON)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Retired Solana Mainnet setup")
    parser.parse_args(argv)
    print(DISABLED_REASON, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

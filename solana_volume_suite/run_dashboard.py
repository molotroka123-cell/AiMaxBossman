"""Compatibility launcher using the same virtual-only policy."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from solana_volume_suite.start_prototype import main

if __name__ == "__main__":
    main()

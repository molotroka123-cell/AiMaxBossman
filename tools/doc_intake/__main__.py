"""Entry point: ``python tools/doc_intake ...`` or ``python -m doc_intake ...`` (from tools/)."""
import sys
from pathlib import Path

if __package__ in (None, ""):
    here = Path(__file__).resolve().parent
    # Run as a directory: sys.path[0] is the package folder itself; replace it with tools/
    # so sibling module names (memory, config, notes) never shadow anything top-level.
    if sys.path and Path(sys.path[0] or ".").resolve() == here:
        sys.path[0] = str(here.parent)
    else:
        sys.path.insert(0, str(here.parent))
    from doc_intake.cli import main
else:
    from .cli import main

sys.exit(main())

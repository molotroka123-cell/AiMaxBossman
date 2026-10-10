"""Code mode: many tools behind two functions (opt-in, default OFF).

    catalog.py        BM25 tool search + typed Python stubs (`bossman.search`)
    sandbox.py        parent side of the subprocess sandbox (`bossman.run`)
    _child.py         allowlist validator + in-sandbox interpreter (stdlib only)
    facade.py         the two ToolSpecs, the opt-in flag, visibility rules
    engine_bridge.py  how the engine drives a snippet through its ordinary tool pipeline
    measure.py        token accounting used by the tests and the report script

Design, security argument and measured savings: docs/architecture/CODE_MODE_FACADE_RU.md
"""

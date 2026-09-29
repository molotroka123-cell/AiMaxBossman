normalize_rel() does not honour its docstring contract for the paths our Windows owner types
(spaces, Cyrillic, mixed separators, traversal, drives). Make it satisfy the full contract and add
tests covering each contract bullet. Standard library only.

from bcc.v2.memory.chunking_v22 import parse_blocks, split_long_markdown


def test_fenced_code_is_atomic_under_normal_limit():
    text = "Intro.\n\n```python\n" + "\n".join(f"print({i})" for i in range(90)) + "\n```\n\nAfter."
    chunks = split_long_markdown(text, max_chars=220, max_atomic_chars=5000)
    fence = [c for c in chunks if "```python" in c]
    assert len(fence) == 1
    assert fence[0].rstrip().endswith("```")


def test_markdown_table_is_atomic():
    rows = ["| Name | Value |", "| --- | --- |"] + [f"| row{i} | value{i} |" for i in range(40)]
    chunks = split_long_markdown("Before\n\n" + "\n".join(rows) + "\n\nAfter",
                                 max_chars=180, max_atomic_chars=5000)
    table = [c for c in chunks if "| Name | Value |" in c]
    assert len(table) == 1
    assert "| row39 | value39 |" in table[0]


def test_large_plain_paragraph_splits():
    chunks = split_long_markdown("word " * 2000, max_chars=400, overlap=40)
    assert len(chunks) > 1
    assert all(chunks)


def test_parse_blocks_recognizes_atoms():
    text = """hello

| a | b |
| --- | --- |
| 1 | 2 |

```js
const x = {a: 1};
```
"""
    kinds = [x.kind for x in parse_blocks(text)]
    assert "table" in kinds and "fence" in kinds

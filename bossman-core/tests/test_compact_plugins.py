from pathlib import Path
from bossman.context_engine import CompactSkill, MarkdownMemoryPlugin, Message


def test_markdown_memory_plugin_hydrates_compact(tmp_path: Path):
    root=tmp_path/'memory'; root.mkdir()
    (root/'decisions.md').write_text('Use provenance for every durable memory record.',encoding='utf-8')
    plugin=MarkdownMemoryPlugin(root)
    out=CompactSkill([plugin]).compact(
        [Message('user','We are improving durable memory provenance.'), Message('assistant','Working on it.')],
        project='bossman',query='durable memory provenance',keep_recent=1,target_tokens=2000,
    )
    assert 'Use provenance for every durable memory record.' in out.text
    assert 'mdmem_' in out.text
    assert out.quality_checks['memory_provenance_preserved']

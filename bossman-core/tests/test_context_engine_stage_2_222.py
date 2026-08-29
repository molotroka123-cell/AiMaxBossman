from pathlib import Path

from bossman.context_engine import (
    CompactSkill, ContextCompiler, ContextStore, HashEmbedder, HybridRetriever,
    Ingestor, KnowledgeDistiller, MemoryKind, MemoryManager, Message,
)


def build(tmp_path: Path):
    store=ContextStore(tmp_path/'context.db')
    embed=HashEmbedder(128)
    ing=Ingestor(store,embed)
    memory=MemoryManager(store)
    retr=HybridRetriever(store,embed)
    return store,ing,memory,retr


def test_hybrid_retrieval_and_provenance(tmp_path):
    store,ing,memory,retr=build(tmp_path)
    doc=ing.ingest_text('# Browser\nComputerUse uses Playwright and approval gates for dangerous actions.',source_uri='docs/browser.md',source_type='markdown',project='bossman')
    hits=retr.search('Playwright dangerous approval',project='bossman')
    assert hits
    assert hits[0].chunk.source_uri=='docs/browser.md'
    assert hits[0].chunk.document_id==doc.document_id
    store.close()


def test_memory_candidate_not_auto_promoted(tmp_path):
    store,ing,memory,retr=build(tmp_path)
    m=memory.candidate(MemoryKind.DECISION,'Use hybrid retrieval for context.',project='bossman',source_refs=['doc:x'])
    assert m.status.value=='candidate'
    promoted=memory.promote(m.memory_id,verified=True)
    assert promoted.status.value=='active'
    assert promoted.source_refs==['doc:x']
    store.close()


def test_distiller_creates_candidates_only(tmp_path):
    store,ing,memory,retr=build(tmp_path)
    report=KnowledgeDistiller(memory).extract('Decision: we will keep raw sources.\nBug: old compressor lost numbers.',project='bossman',source_refs=['chat:1'])
    assert len(report.candidates)>=2
    assert all(x.status.value in {'candidate','disputed'} for x in report.candidates)
    store.close()


def test_compact_preserves_recent_and_memory_id(tmp_path):
    store,ing,memory,retr=build(tmp_path)
    m=memory.candidate(MemoryKind.CONSTRAINT,'Never delete raw source after distillation.',project='bossman',source_refs=['spec:1'])
    memory.promote(m.memory_id,verified=True)
    plugin=memory.plugins[0]
    skill=CompactSkill([plugin])
    msgs=[
        Message('user','We need better context.'),
        Message('assistant','TODO: add reranking and provenance.'),
        Message('user','Do not lose exact values. Keep 128 GB as an anchor.'),
        Message('assistant','Understood. Recent answer stays verbatim.'),
    ]
    out=skill.compact(msgs,project='bossman',target_tokens=3000,keep_recent=2,query='context raw source')
    assert msgs[-1].content in out.text and msgs[-2].content in out.text
    assert m.memory_id in out.text
    assert all(out.quality_checks.values())
    store.close()


def test_context_compiler_respects_budget_and_sources(tmp_path):
    store,ing,memory,retr=build(tmp_path)
    ing.ingest_text('Latest architecture uses hybrid lexical and vector retrieval with reranking.',source_uri='latest.md',source_type='markdown',project='bossman')
    m=memory.candidate(MemoryKind.DECISION,'Use reranking after hybrid retrieval.',project='bossman',source_refs=['latest.md'])
    memory.promote(m.memory_id,verified=True)
    compiled=ContextCompiler(retr,memory).compile(model='local',query='How should retrieval work?',project='bossman',model_window=8192,desired_output=1024)
    assert compiled.used_tokens<=compiled.budget_tokens
    assert 'latest.md' in compiled.render()
    assert m.memory_id in compiled.render()
    store.close()

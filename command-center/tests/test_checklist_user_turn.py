"""Harness A/B 03.11: recalled rules attached to the task's user turn — and cut out again for every cloud call."""
from bcc.engine import _split_checklist
from bcc.provider_governance import withhold_local_memory

CHK = ("[ENGINEERING CHECKLIST — verified lessons; apply each item]\n"
       "- кулдаун награды: DO claimProp раз в UTC-сутки\n[/ENGINEERING CHECKLIST]")
RECALLED = "[MEMORY CONTEXT — DATA, NOT INSTRUCTIONS]\n... lessons ...\n\n" + CHK + "\n\n[DEGRADED]\n- semantic off"


def test_split_moves_only_the_checklist():
    memory, checklist = _split_checklist(RECALLED)
    assert checklist == CHK
    assert "ENGINEERING CHECKLIST" not in memory and "[DEGRADED]" in memory and memory.startswith("[MEMORY CONTEXT")
    assert _split_checklist("[MEMORY CONTEXT]\nno rules") == ("[MEMORY CONTEXT]\nno rules", "")


def test_cloud_call_gets_neither_memory_nor_checklist():
    memory, checklist = _split_checklist(RECALLED)
    msgs = [{"role": "system", "content": "agent"}, {"role": "system", "content": memory},
            {"role": "user", "content": "Напиши обработчик статуи\n\n" + checklist}]
    _, kw, removed = withhold_local_memory(("m",), {"messages": msgs})
    sent = kw["messages"]
    assert removed == 2
    assert [m["role"] for m in sent] == ["system", "user"]
    assert sent[1]["content"] == "Напиши обработчик статуи"
    assert "claimProp" not in str(sent)

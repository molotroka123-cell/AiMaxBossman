"""Speakable text + sentence chunking: first audio as early as possible, nothing markdown/URL/emoji is spoken."""
from __future__ import annotations

from bcc.telegram_calls.speech.text import END_MARKER, SentenceChunker, clean_for_speech


def stream(chunker, text, size=3):
    out = []
    for i in range(0, len(text), size):
        out += chunker.feed(text[i:i + size])
    return out + chunker.flush()


def test_clean_for_speech_removes_markdown_links_code_emoji_and_think_blocks():
    raw = "<think>скрытые рассуждения</think>**Привет!** Вот [ссылка](https://x.io/a) и `код` 😀\n\n- пункт один\n- пункт два\n```py\nprint(1)\n```"
    said = clean_for_speech(raw)
    for bad in ("**", "http", "`", "😀", "думаю", "print", "- ", "скрытые"):
        assert bad not in said
    assert "Привет" in said and "ссылка" in said and "пункт один" in said


def test_first_chunk_is_early_but_later_chunks_are_whole_sentences():
    c = SentenceChunker()
    out = stream(c, "Конечно, сейчас расскажу подробнее. Сегодня в Праге облачно, около двенадцати градусов. Завтра будет дождь.")
    assert out[0].endswith(("подробнее.", "Конечно,")) or out[0].startswith("Конечно")
    assert len(out) >= 2 and all(o.strip() for o in out)
    assert " ".join(out).replace("  ", " ").count("дождь") == 1          # nothing lost, nothing duplicated


def test_short_answers_are_not_held_back_until_flush_forever():
    assert stream(SentenceChunker(), "Да.") == ["Да."]


def test_end_marker_is_removed_and_flags_the_call_end_even_when_split_across_deltas():
    c = SentenceChunker()
    out = []
    for piece in ("Хорошо, до связи. ", "Пока! [ко", "нец]"):
        out += c.feed(piece)
    out += c.flush()
    assert c.end_call is True and END_MARKER not in " ".join(out) and "конец" not in " ".join(out).lower().replace("до связи", "")
    assert "Пока" in " ".join(out)


def test_no_end_marker_no_hangup_negative_control():
    c = SentenceChunker()
    stream(c, "Понял, продолжаем разговор.")
    assert c.end_call is False


def test_runaway_sentence_is_cut_at_a_space():
    c = SentenceChunker(max_chars=60)
    out = stream(c, "слово " * 40, size=7)
    assert len(out) > 2 and all(len(o) <= 70 for o in out)


def test_empty_and_punctuation_only_chunks_are_never_spoken():
    assert stream(SentenceChunker(), "  ... !!! ") == []

"""Sealed local grader for the text-only coding tasks; do not send to models."""
from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import tempfile
import unittest


def load(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class CandidateCodingTests(unittest.TestCase):
    root: Path

    def test_merge_intervals(self):
        m = load(self.root / "merge_intervals.py", "candidate_merge")
        src = [(8, 10), (1, 3), (3, 5), (20, 21)]
        self.assertEqual(m.merge_intervals(iter(src)), [(1, 5), (8, 10), (20, 21)])
        self.assertEqual(src, [(8, 10), (1, 3), (3, 5), (20, 21)])
        self.assertEqual(m.merge_intervals([]), [])
        self.assertEqual(m.merge_intervals([(4, 4), (4, 4)]), [(4, 4)])
        with self.assertRaises(ValueError):
            m.merge_intervals([(5, 4)])

    def test_top_k_frequent(self):
        m = load(self.root / "top_k_frequent.py", "candidate_topk")
        words = ["pear", "apple", "pear", "plum", "apple", "kiwi", "plum"]
        self.assertEqual(m.top_k_frequent(words, 2), ["apple", "pear"])
        self.assertEqual(m.top_k_frequent(words, 20), ["apple", "pear", "plum", "kiwi"])
        self.assertEqual(m.top_k_frequent(words, 0), [])
        self.assertEqual(m.top_k_frequent(words, -1), [])
        self.assertEqual(words, ["pear", "apple", "pear", "plum", "apple", "kiwi", "plum"])

    def test_retry_after(self):
        m = load(self.root / "retry_after.py", "candidate_retry")
        now = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(m.parse_retry_after("12", now), 12.0)
        self.assertEqual(m.parse_retry_after("Sun, 04 Oct 2026 12:00:09 GMT", now), 9.0)
        self.assertEqual(m.parse_retry_after("Sun, 04 Oct 2026 11:59:00 GMT", now), 0.0)
        self.assertIsNone(m.parse_retry_after("tomorrow", now))
        self.assertIsNone(m.parse_retry_after("-3", now))
        naive = datetime(2026, 10, 4, 12, 0)
        self.assertEqual(m.parse_retry_after("Sun, 04 Oct 2026 12:00:09 GMT", naive), 9.0)

    def test_redact(self):
        m = load(self.root / "redact.py", "candidate_redact")
        source = {"Name": "Ada", "api_key_id": 8,
                  "nested": [{"Authorization": "Bearer hidden", "ok": True},
                             ("tuple", {"clientSecret": "s"})]}
        result = m.redact(source)
        self.assertEqual(result, {"Name": "Ada", "api_key_id": "[REDACTED]",
                                  "nested": [{"Authorization": "[REDACTED]", "ok": True},
                                             ("tuple", {"clientSecret": "[REDACTED]"})]})
        self.assertIsNot(result, source)
        self.assertEqual(source["api_key_id"], 8)

    def test_parse_byte_range(self):
        m = load(self.root / "byte_range.py", "candidate_byte_range")
        self.assertEqual(m.parse_byte_range("bytes=0-9", 100), (0, 9))
        self.assertEqual(m.parse_byte_range("bytes=90-", 100), (90, 99))
        self.assertEqual(m.parse_byte_range("bytes=-10", 100), (90, 99))
        self.assertEqual(m.parse_byte_range("bytes=-200", 100), (0, 99))
        self.assertEqual(m.parse_byte_range("bytes=4-500", 8), (4, 7))
        for value, size in (("items=0-1", 8), ("bytes=8-9", 8), ("bytes=4-3", 8),
                            ("bytes=0-1,3-4", 8), ("bytes=--1", 8), ("bytes=-0", 8),
                            ("bytes=0-1", 0), ("bytes=0-1", -1)):
            self.assertIsNone(m.parse_byte_range(value, size), (value, size))

    def test_dedupe_paths(self):
        m = load(self.root / "dedupe_paths.py", "candidate_dedupe_paths")
        source = ["A\\b\\..\\C.txt", "a/c.TXT", "./root//x", "root/x", "/a/../b", "/b", "../../z", "../../Z"]
        self.assertEqual(m.dedupe_paths(source), ["A\\b\\..\\C.txt", "./root//x", "/a/../b", "../../z"])
        self.assertEqual(source[0], "A\\b\\..\\C.txt")
        self.assertEqual(m.dedupe_paths([]), [])


def run_candidate(root: Path) -> dict:
    CandidateCodingTests.root = root
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(CandidateCodingTests)
    result = unittest.TestResult()
    suite.run(result)
    return {"passed": result.testsRun - len(result.failures) - len(result.errors),
            "total": result.testsRun,
            "failures": [f"{test.id()}: {message[-1200:]}" for test, message in result.failures],
            "errors": [f"{test.id()}: {message[-1200:]}" for test, message in result.errors]}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate_dir", type=Path)
    args = parser.parse_args()
    print(run_candidate(args.candidate_dir))

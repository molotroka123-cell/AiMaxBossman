"""Reproducible, runtime-bound Bossman benchmark.

The runner deliberately launches each fixture in a child process.  Benchmark
orchestration therefore never imports or calls product implementation details
directly; adapters are exercised through their public process/CLI boundary.
"""

from .engine import BASELINE_SHA, BenchmarkRunner, compare_reports

__all__ = ("BASELINE_SHA", "BenchmarkRunner", "compare_reports")

"""Independent oracle. Run only after inspecting the model's candidate code.

Usage: python aster6_circular_verify.py candidate.py
No model answer is provided by this file. Input code is local audit evidence.
"""
import importlib.util
import itertools
import json
import random
import subprocess
import sys
import time


def oracle(a, k):
    best = None
    for start in range(len(a)):
        total = 0
        for length in range(1, len(a) + 1):
            total += a[(start + length - 1) % len(a)]
            if total >= k:
                pair = (length, start)
                if best is None or pair < best:
                    best = pair
    return None if best is None else (best[1], best[0])


def main(path):
    spec = importlib.util.spec_from_file_location("candidate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    f = module.shortest_circular
    count = 0

    def check(a, k, expected):
        nonlocal count
        original = a.copy()
        actual = f(a, k)
        count += 1
        if actual != expected or a != original:
            print(json.dumps({"verdict": "FAIL", "case": count, "a": original,
                              "k": k, "expected": expected, "actual": actual,
                              "mutated": a != original}))
            raise SystemExit(1)

    for n in range(7):
        for values in itertools.product((-2, 0, 3), repeat=n):
            for k in (-6, -2, 0, 1, 3, 5, 9):
                a = list(values)
                check(a, k, oracle(a, k))
    rng = random.Random(0xA5760922)
    for _ in range(4000):
        a = [rng.randint(-15, 15) for _ in range(rng.randrange(1, 31))]
        k = rng.randint(-30, 100)
        check(a, k, oracle(a, k))
    for a, k in [([6 * 10**11, -10**12, 6 * 10**11], 10**12),
                 ([-10**12] * 3, -10**12), ([0] * 20, 0)]:
        check(a, k, oracle(a, k))
    start = time.perf_counter()
    check([0] * 200000, 1, None)
    check([1] * 200000, 200000, (0, 200000))
    check([-1] * 200000, -1, (0, 1))
    check([10] + [-100] * 199998 + [10], 20, (199999, 2))
    print(json.dumps({"verdict": "PASS", "cases": count,
                      "four_large_cases_seconds": round(time.perf_counter() - start, 4),
                      "scope": "one coding puzzle; not general intelligence certification"}))


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        main(sys.argv[2])
    else:
        try:
            completed = subprocess.run([sys.executable, __file__, "--worker", sys.argv[1]],
                                       timeout=30, check=False)
            raise SystemExit(completed.returncode)
        except subprocess.TimeoutExpired:
            print(json.dumps({"verdict": "FAIL", "reason": "verification exceeded 30 s"}))
            raise SystemExit(1)

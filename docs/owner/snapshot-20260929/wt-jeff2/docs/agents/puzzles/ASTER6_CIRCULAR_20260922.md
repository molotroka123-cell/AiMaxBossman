# Five-minute coding challenge (unseen holdout)

Implement Python 3.12 function `shortest_circular(a: list[int], k: int) -> tuple[int, int] | None`.

`a` is a circular array. Choose a nonempty contiguous circular segment, using each array position at most once. Its sum must be at least `k`. Return `(start, length)` with the smallest length; among equal lengths choose the smallest original start index. Return `None` if no segment qualifies, including an empty input. Do not mutate `a`.

Constraints: 0 <= len(a) <= 200000; elements and k are signed integers with absolute value <= 10**12. Negative values, k <= 0, wrapping, repeated prefix sums and ties all matter. Required worst-case O(n) time and O(n) additional memory. No external packages, filesystem or network access.

Examples:
- `shortest_circular([2, -1, 2], 3) == (2, 2)`
- `shortest_circular([1, 1, 1], 2) == (0, 2)`
- `shortest_circular([-5, -2], -3) == (1, 1)`
- `shortest_circular([], 0) is None`
- `shortest_circular([1, -1], 2) is None`

You have at most 300 seconds from submission. Return one Python code block containing the complete function (and any standard-library imports), then a concise correctness and complexity argument. The evaluator uses a separate brute-force oracle and hidden adversarial/random cases. Do not claim to have executed tests unless you actually did. No follow-up hints or repairs are given during this attempt.

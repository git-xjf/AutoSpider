from __future__ import annotations


def fingerprint_similarity(left: str | None, right: str | None) -> float:
    """Compare SHA-256 structure fingerprints conservatively.

    V0.1 hashes are exact structure signatures. A future profile can store
    MinHash features for graded similarity without changing repository callers.
    """

    if not left or not right:
        return 0.0
    return 1.0 if left == right else 0.0


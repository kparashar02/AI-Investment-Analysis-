"""Evaluation harness — Phase 6 (PRD 19).

Six tracks of evidence that the system works, not just a demo that ran once:
computational accuracy against golden values, directional agreement with
consensus, report quality by rubric, determinism & variance, a hallucination /
groundedness audit, and adversarial injection resistance. The deterministic
tracks (1, 4, 5, 6) run offline with no key; the tracks that need live data or a
model judge (2, 3) provide their scoring logic and run when given their inputs.
``docs/EVALUATION.md`` is the written deliverable (PRD 19.8).
"""

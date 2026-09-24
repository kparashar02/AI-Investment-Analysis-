"""Application layer — Layer 5 (PRD 7, Phase 5).

A FastAPI backend that exposes the analysis pipeline over HTTP with live
progress streaming, persists a history of analyses to SQLite, and serves a
minimal browser UI plus the HTML/JSON/Markdown report. The API is a thin shell:
all the work is the deterministic engine and the agent pipeline underneath, and
the service layer is injectable so the whole surface is testable with a fake
pipeline and no network.
"""

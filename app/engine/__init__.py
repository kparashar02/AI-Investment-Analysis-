"""Deterministic computation layer.

Nothing in this package may import from ``app.agents``, call a language
model, or reach the network. Every function here is a pure transformation of
numbers into numbers, and the recommendation the system eventually issues is
built out of these results rather than out of a model's opinion.
"""

"""Shared provider adapters (Gemini LLM & Embedding).

Invariant 4: ONLY this package may import the Gemini SDK. Routers, services,
repositories, and worker tasks call these adapters exclusively.
"""

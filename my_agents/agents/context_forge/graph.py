"""Optional ContextForge graph seam.

The initial production integration uses ContextForgeService directly so the
conversation path stays simple and testable. This module reserves a package-local
place for a future LangGraph role-node implementation without moving hard
authorization out of RetrievalService.
"""

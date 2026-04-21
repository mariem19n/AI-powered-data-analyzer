"""
app/llm/__init__.py
Client LLM centralisé — réutilisable par tous les composants
(extractor, orchestrator, agents).
"""

from app.llm.client import LLMClient, get_llm_client

__all__ = ["LLMClient", "get_llm_client"]
"""
app/agents/analysis/enrichment

Modules d'enrichissement contextuel pour les analyses (GDELT, news externes, etc.).
"""

from app.agents.analysis.enrichment.gdelt import (
    extract_crypto_id_from_context,
    fetch_gdelt_context,
)

__all__ = [
    "extract_crypto_id_from_context",
    "fetch_gdelt_context",
]

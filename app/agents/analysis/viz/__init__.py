"""
app/agents/analysis/viz
Visualisations de l'Analysis Agent.

L'import des modules de viz concrets ci-dessous déclenche leur enregistrement
auprès du registry via @register_viz et éventuellement
@register_default_for_shape. Aucune liste hardcodée de viz ailleurs.

Pour ajouter une nouvelle viz :
  1. créer un fichier dans ce dossier (ex: bar_chart.py)
  2. décorer la fonction avec @register_viz("name")
     et éventuellement @register_default_for_shape(SHAPE_GROUPBY)
  3. l'importer ici pour déclencher l'enregistrement
"""

# Imports de side-effect : chacun enregistre sa viz via @register_viz.
# noqa: F401 — imports volontairement non-utilisés directement.
from app.agents.analysis.viz import line_chart  # noqa: F401
from app.agents.analysis.viz import scatter_plot  # noqa: F401

# API publique du sous-package.
from app.agents.analysis.viz.templates import (
    default_viz_for_shape,
    get_default_viz_name_for_shape,
    get_viz,
    list_registered_viz,
    list_shape_defaults,
    register_default_for_shape,
    register_viz,
)
from app.agents.analysis.viz.theme import (
    apply_theme_to_layout,
    get_categorical_palette,
    get_series_color,
    get_theme,
)

__all__ = [
    "apply_theme_to_layout",
    "default_viz_for_shape",
    "get_categorical_palette",
    "get_default_viz_name_for_shape",
    "get_series_color",
    "get_theme",
    "get_viz",
    "list_registered_viz",
    "list_shape_defaults",
    "register_default_for_shape",
    "register_viz",
]
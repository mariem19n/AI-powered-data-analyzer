"""
app/agents/analysis/viz/templates.py
Registre et dispatcher des fonctions de visualisation.

Conception :
- Chaque fonction de viz (line_chart, bar_chart, heatmap, ...) s'auto-enregistre
  via le décorateur @register_viz("name"). Le dispatcher get_viz(name) est un
  pur lookup : aucun mapping nom→fonction n'est hardcodé ailleurs.
- default_viz_for_shape(shape) retourne la viz par défaut pour une forme de
  DataFrame donnée (telle que produite par stats/descriptive.detect_dataframe_shape).
  Le mapping shape→viz est lui aussi peuplé via un décorateur dédié
  @register_default_for_shape, pas via une table hardcodée. Conséquence :
  ajouter une nouvelle forme + sa viz par défaut = créer un fichier + décorer.

Aucune fonction de viz n'est importée ici. Les modules de viz s'enregistrent
au moment de leur import (déclenché depuis viz/__init__.py).

Le contrat d'une fonction de viz :
    (df: pd.DataFrame, config: dict) -> dict
Le retour est un dict Plotly JSON-serializable (équivalent de fig.to_dict()).
La forme exacte de `config` est définie par chaque viz via une TypedDict
dédiée dans son propre fichier (LineChartConfig, BarChartConfig, ...).
"""

from __future__ import annotations

import logging
from typing import Any, Callable

import pandas as pd

logger = logging.getLogger(__name__)


# Type alias pour clarifier les signatures.
VizFunction = Callable[[pd.DataFrame, dict[str, Any]], dict[str, Any]]


# ─── Registres ────────────────────────────────────────────────────────────


# Registre principal : viz_name -> fonction de viz.
# Peuplé exclusivement via @register_viz.
_VIZ_REGISTRY: dict[str, VizFunction] = {}

# Registre des viz par défaut pour chaque forme de DataFrame.
# Peuplé exclusivement via @register_default_for_shape.
# Clés : tags retournés par detect_dataframe_shape (SHAPE_TIMESERIES, SHAPE_GROUPBY...).
# Valeurs : viz_name (doit aussi être enregistré dans _VIZ_REGISTRY au final).
_SHAPE_DEFAULTS: dict[str, str] = {}


# ─── Décorateurs d'enregistrement ─────────────────────────────────────────


def register_viz(name: str) -> Callable[[VizFunction], VizFunction]:
    """
    Décorateur de fonction : enregistre une fonction de viz sous `name`.

    Usage :
        @register_viz("line")
        def line_chart(df, config):
            ...

    Validation :
    - `name` doit être une str non vide.
    - Pas de collision avec un viz_name déjà enregistré.
    """
    if not isinstance(name, str) or not name:
        raise ValueError("register_viz: 'name' doit être une str non vide")

    def decorator(fn: VizFunction) -> VizFunction:
        if not callable(fn):
            raise TypeError(f"register_viz: {fn!r} n'est pas appelable")
        if name in _VIZ_REGISTRY:
            existing = _VIZ_REGISTRY[name].__name__
            raise ValueError(
                f"register_viz: collision sur viz_name='{name}'. "
                f"Déjà enregistré par {existing}, tentative depuis {fn.__name__}."
            )
        _VIZ_REGISTRY[name] = fn
        # Pose l'attribut __viz_name__ sur la fonction pour que les autres
        # décorateurs (register_default_for_shape) puissent l'utiliser au lieu
        # de fn.__name__ qui est le nom Python (souvent différent).
        fn.__viz_name__ = name  # type: ignore[attr-defined]
        logger.debug("Registered viz: %s -> %s", name, fn.__name__)
        return fn

    return decorator


def register_default_for_shape(
    shape: str,
) -> Callable[[VizFunction], VizFunction]:
    """
    Décorateur de fonction : déclare que la fonction est la viz par défaut
    pour une forme de DataFrame donnée.

    À combiner avec @register_viz. Exemple :
        @register_default_for_shape(SHAPE_TIMESERIES)
        @register_viz("line")
        def line_chart(df, config):
            ...

    L'ordre des décorateurs n'a pas d'importance fonctionnelle, mais par
    convention on place register_default_for_shape AU-DESSUS de register_viz
    pour que l'attribution du défaut soit visible en premier à la lecture.

    Validation :
    - `shape` doit être une str non vide.
    - Pas de double-déclaration de défaut pour la même shape.
    - La fonction doit aussi être (ou être enregistrée plus tard) dans
      _VIZ_REGISTRY ; cette cohérence est vérifiée par get_default_viz_name_for_shape
      au moment de l'usage, pas ici, pour ne pas dépendre de l'ordre d'import.
    """
    if not isinstance(shape, str) or not shape:
        raise ValueError("register_default_for_shape: 'shape' doit être non vide")

    def decorator(fn: VizFunction) -> VizFunction:
        if shape in _SHAPE_DEFAULTS:
            existing = _SHAPE_DEFAULTS[shape]
            raise ValueError(
                f"register_default_for_shape: déjà un défaut pour shape='{shape}' "
                f"(viz='{existing}'), tentative depuis {fn.__name__}."
            )
        # On stocke le NOM via attribut __viz_name__ posé par register_viz, sinon
        # on tombe sur __name__ de la fonction. register_viz pose l'attribut
        # quand il s'exécute — donc l'ordre @register_default_for_shape AU-DESSUS
        # de @register_viz fonctionne correctement (register_viz s'applique en
        # premier, pose l'attribut, puis register_default_for_shape le lit).
        viz_name = getattr(fn, "__viz_name__", None) or fn.__name__
        _SHAPE_DEFAULTS[shape] = viz_name
        logger.debug("Registered default viz for shape '%s': %s", shape, viz_name)
        return fn

    return decorator


# ─── Lookup public ────────────────────────────────────────────────────────


def get_viz(name: str) -> VizFunction:
    """
    Retourne la fonction de viz enregistrée sous `name`.

    Lève KeyError avec un message listant les viz disponibles si non trouvée.
    """
    fn = _VIZ_REGISTRY.get(name)
    if fn is None:
        available = sorted(_VIZ_REGISTRY.keys())
        raise KeyError(
            f"Viz inconnue : '{name}'. Viz enregistrées : {available}"
        )
    return fn


def list_registered_viz() -> list[str]:
    """Retourne la liste triée des viz_name enregistrés."""
    return sorted(_VIZ_REGISTRY.keys())


def list_shape_defaults() -> dict[str, str]:
    """Retourne une copie du mapping shape -> viz_name par défaut."""
    return dict(_SHAPE_DEFAULTS)


def default_viz_for_shape(shape: str) -> VizFunction | None:
    """
    Retourne la fonction de viz par défaut associée à `shape`, ou None si
    aucun défaut n'a été enregistré pour cette forme.

    Utilisé par les tasks qui veulent déléguer le choix de viz au système
    plutôt que le coder en dur. Ex : descriptive task fait
        viz_fn = default_viz_for_shape(detected_shape)
        if viz_fn is None:
            # pas de viz pour cette shape -> on ne génère rien, warning
            ...
        else:
            plot_dict = viz_fn(df, config)

    Retourne None plutôt que de lever pour que l'absence de défaut soit un
    cas géré (warning -> TaskResult.warnings), pas une erreur.
    """
    name = _SHAPE_DEFAULTS.get(shape)
    if name is None:
        return None
    return _VIZ_REGISTRY.get(name)


def get_default_viz_name_for_shape(shape: str) -> str | None:
    """
    Variante de default_viz_for_shape qui retourne le NOM de la viz plutôt
    que la fonction. Utile pour le logging et les warnings où on ne veut
    pas déclencher de lookup.
    """
    return _SHAPE_DEFAULTS.get(shape)


# ─── Helpers de test ──────────────────────────────────────────────────────


def _clear_registries_for_tests() -> None:
    """
    Vide les deux registres. À n'utiliser que dans les tests.
    Ne pas appeler en production.
    """
    _VIZ_REGISTRY.clear()
    _SHAPE_DEFAULTS.clear()
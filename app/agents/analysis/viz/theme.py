"""
app/agents/analysis/viz/theme.py
Configuration visuelle centralisée pour toutes les visualisations.

Source unique de vérité pour les couleurs, polices, marges et layout par défaut.

API publique :
- get_theme(overrides=None) -> dict
    Retourne une COPIE PROFONDE du thème par défaut, avec un éventuel
    override deep-mergé. Modifier le retour ne modifie jamais le thème global.

- apply_theme_to_layout(layout, theme=None) -> dict
    Injecte les valeurs de thème (font, marges, fond, légende, axes) dans
    un dict de layout Plotly. Préserve les valeurs déjà fournies dans `layout`.

- get_categorical_palette(theme=None) -> list[str]
    Retourne la palette catégorielle (Plotly Set2, pastel) pour multi-séries.

- get_series_color(index, theme=None) -> str
    Retourne la couleur de la i-ème série (cycle modulo si i dépasse la palette).

Pas d'enum de thèmes nommés (dark/light/...). Si un mode alternatif est requis
plus tard, il peut être ajouté via un registre (même pattern que tasks/ et viz/).
"""

from __future__ import annotations

import copy
from typing import Any


# ─── Palette catégorielle : Plotly Set2 (pastel) ──────────────────────────


# Référence : palette Set2 de ColorBrewer (utilisée par plotly.express).
# Choisie pour être douce sur fond clair et permettre de distinguer jusqu'à 8
# séries sans saturer visuellement.
_SET2_PALETTE: tuple[str, ...] = (
    "#66C2A5",  # turquoise
    "#FC8D62",  # orange saumon
    "#8DA0CB",  # bleu pastel
    "#E78AC3",  # rose pastel
    "#A6D854",  # vert tilleul
    "#FFD92F",  # jaune
    "#E5C494",  # beige
    "#B3B3B3",  # gris
)


# ─── Thème par défaut : source unique de vérité ───────────────────────────


# Toutes les valeurs visuelles par défaut sont ici. Aucun fichier de viz ne
# doit en dupliquer. Pour modifier, soit on modifie ce dict (changement
# global), soit on passe un override à get_theme() (changement local).
DEFAULT_THEME: dict[str, Any] = {
    # ─ Couleurs catégorielles pour multi-séries ─
    "categorical_palette": list(_SET2_PALETTE),

    # ─ Couleurs sémantiques (utilisées pour des rôles précis) ─
    "colors": {
        # Série unique par défaut quand il n'y a qu'une courbe.
        "primary": "#66C2A5",
        # Marqueurs d'anomalies (utilisé par anomaly task plus tard).
        "anomaly": "#E74C3C",
        # Bandes de confiance / prévisions.
        "forecast": "#8DA0CB",
        # Lignes de référence (moyenne, seuils).
        "reference": "#999999",
        # Texte principal.
        "text": "#2C3E50",
        # Grille des axes.
        "grid": "#ECECEC",
        # Fond du plot.
        "plot_bg": "#FFFFFF",
        # Fond global (autour du plot).
        "paper_bg": "#FFFFFF",
    },

    # ─ Police ─
    "font": {
        # Familles système safe — pas de webfont à charger.
        "family": (
            "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, "
            "'Helvetica Neue', Arial, sans-serif"
        ),
        "size": 13,
        "color": "#2C3E50",
    },

    # ─ Tailles spécifiques (texte) ─
    "title_font_size": 16,
    "axis_title_font_size": 13,
    "tick_font_size": 11,
    "legend_font_size": 12,

    # ─ Layout par défaut ─
    "margins": {"l": 60, "r": 30, "t": 60, "b": 50},
    "height": 420,

    # ─ Légende ─
    "legend": {
        "orientation": "h",      # horizontale
        "yanchor": "bottom",
        "y": 1.02,
        "xanchor": "right",
        "x": 1.0,
    },

    # ─ Hover ─
    "hovermode": "x unified",    # tooltip aligné sur l'axe X
}


# ─── API publique ─────────────────────────────────────────────────────────


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """
    Fusion récursive de deux dicts. `override` gagne sur `base` à clé égale,
    sauf si les deux valeurs sont des dicts — auquel cas on descend récursivement.

    Listes et scalaires : remplacement complet (pas de merge de listes).
    Ne mute aucun des deux arguments.
    """
    out: dict[str, Any] = copy.deepcopy(base)
    for key, ov_value in override.items():
        if (
            key in out
            and isinstance(out[key], dict)
            and isinstance(ov_value, dict)
        ):
            out[key] = _deep_merge(out[key], ov_value)
        else:
            out[key] = copy.deepcopy(ov_value)
    return out


def get_theme(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Retourne une copie profonde du thème par défaut, avec un override optionnel.

    L'override est fusionné récursivement : on peut modifier juste une clé sans
    avoir à respécifier tout le sous-arbre. Ex :
        get_theme({"colors": {"primary": "#FF0000"}})
    laisse intactes toutes les autres couleurs.

    La valeur retournée est entièrement détachée du thème global — la modifier
    ne pollue pas les appels suivants.
    """
    if overrides is None:
        return copy.deepcopy(DEFAULT_THEME)
    return _deep_merge(DEFAULT_THEME, overrides)


def get_categorical_palette(theme: dict[str, Any] | None = None) -> list[str]:
    """Retourne la palette catégorielle du thème (copie, modifiable sans risque)."""
    t = theme if theme is not None else get_theme()
    return list(t["categorical_palette"])


def get_series_color(index: int, theme: dict[str, Any] | None = None) -> str:
    """
    Retourne la couleur de la i-ème série en cyclant sur la palette si besoin.

    Args:
        index: position de la série (0-indexed).
        theme: thème optionnel ; sinon thème par défaut.
    """
    palette = get_categorical_palette(theme)
    if not palette:
        # Fallback ultime — ne devrait jamais arriver vu DEFAULT_THEME.
        return "#000000"
    return palette[index % len(palette)]


def apply_theme_to_layout(
    layout: dict[str, Any] | None,
    theme: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Construit un dict de layout Plotly à partir d'un layout partiel + thème.

    Stratégie : les valeurs déjà présentes dans `layout` sont PRÉSERVÉES
    (l'appelant a la priorité). Les valeurs manquantes sont remplies par le
    thème. Pas de fusion profonde sur l'argument `layout` — il est utilisé
    en surcharge finale, donc l'appelant peut dire "non, je veux MES marges"
    sans subir le thème.

    Cette fonction est l'unique point d'injection du thème dans Plotly.
    Aucun fichier de viz ne doit créer un layout sans passer par elle.
    """
    t = theme if theme is not None else get_theme()
    user_layout = layout or {}

    base_layout: dict[str, Any] = {
        "font": dict(t["font"]),
        "title": {
            "font": {
                "family": t["font"]["family"],
                "size": t["title_font_size"],
                "color": t["colors"]["text"],
            },
        },
        "margin": dict(t["margins"]),
        "height": t["height"],
        "paper_bgcolor": t["colors"]["paper_bg"],
        "plot_bgcolor": t["colors"]["plot_bg"],
        "hovermode": t["hovermode"],
        "legend": {
            **t["legend"],
            "font": {"size": t["legend_font_size"]},
        },
        "xaxis": {
            "gridcolor": t["colors"]["grid"],
            "zeroline": False,
            "title": {"font": {"size": t["axis_title_font_size"]}},
            "tickfont": {"size": t["tick_font_size"]},
        },
        "yaxis": {
            "gridcolor": t["colors"]["grid"],
            "zeroline": False,
            "title": {"font": {"size": t["axis_title_font_size"]}},
            "tickfont": {"size": t["tick_font_size"]},
        },
    }

    # L'appelant gagne sur les conflits.
    return _deep_merge(base_layout, user_layout)

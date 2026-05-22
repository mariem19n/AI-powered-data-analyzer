"""
app/agents/analysis/viz/heatmap.py

Visualisation heatmap pour matrice de corrélation.

Suit exactement le pattern de `line_chart.py` :
  - TypedDict de config pour la signature publique
  - thème centralisé via `app/agents/analysis/viz/theme.py`
  - enregistrement automatique via @register_viz
  - aucun nom de colonne / domaine hardcodé : tout vient de la config

L'appelant (la task) construit le payload de config et appelle
`build_correlation_heatmap(config)`. Aucune dépendance à Pandas dans la
signature — on accepte des structures Python pures pour rester
testable et JSON-friendly.
"""

from __future__ import annotations

import logging
from typing import Any, TypedDict

from app.agents.analysis.viz.templates import register_viz
from app.agents.analysis.viz.theme import get_theme

logger = logging.getLogger(__name__)


# ─── Configuration ─────────────────────────────────────────────────────────


class HeatmapConfig(TypedDict, total=False):
    """
    Configuration d'une heatmap de corrélation.

    Champs obligatoires :
      - labels : list[str]
            Liste des noms de séries dans l'ordre voulu (sera utilisée
            comme axe X et axe Y).
      - matrix : list[list[float | None]]
            Matrice carrée des valeurs. None autorisé pour valeurs
            indéfinies (NaN/inf déjà sérialisés en None côté stats).

    Champs optionnels :
      - title : str         — titre du graphique
      - subtitle : str      — sous-titre (par ex. "n=120 points, returns")
      - method : str        — "pearson" ou "spearman" (affiché en sous-titre)
      - color_scale : str   — palette divergente (défaut : 'RdBu_r')
      - zmin, zmax : float  — bornes (défaut : -1, 1)
      - show_values : bool  — afficher les valeurs dans les cases (défaut True)
      - annotation_threshold : float
            |valeur| ≥ seuil → annotation en gras blanc.
            Défaut : 0.7 (= STRONG_THRESHOLD du module stats).
    """

    labels: list[str]
    matrix: list[list[float | None]]
    title: str
    subtitle: str
    method: str
    color_scale: str
    zmin: float
    zmax: float
    show_values: bool
    annotation_threshold: float


# ─── Constantes par défaut ─────────────────────────────────────────────────

_DEFAULT_COLOR_SCALE = "RdBu_r"  # divergente, centrée sur 0
_DEFAULT_ZMIN = -1.0
_DEFAULT_ZMAX = 1.0
_DEFAULT_SHOW_VALUES = True
_DEFAULT_ANNOTATION_BOLD_THRESHOLD = 0.7

# Fallbacks utilisés si le thème n'expose pas ces clés.
_FALLBACK_TEXT_DARK = "#333333"
_FALLBACK_TEXT_LIGHT = "#ffffff"
_FALLBACK_TEXT_MUTED = "#888888"
_FALLBACK_BACKGROUND = "white"
_FALLBACK_FONT_FAMILY = "Inter, sans-serif"


# ─── Helper de lecture du thème ─────────────────────────────────────────────


def _theme_get(theme: Any, key: str, default: str) -> str:
    """
    Lit une valeur du thème de manière défensive.

    Supporte trois formes de thème :
      1. dict plat :        {"text": "#333", "background": "white", ...}
      2. dict imbriqué :    {"colors": {"text": "#333", ...}, "font_family": ...}
      3. objet (dataclass) : theme.colors.text, theme.font_family

    Cherche `key` dans cet ordre :
      - theme[key] si dict plat
      - theme["colors"][key] si dict avec sous-bloc "colors"
      - theme.colors.<key> si objet avec attribut colors
      - theme.<key> si objet avec attribut direct (pour font_family)
    """
    if theme is None:
        return default

    if isinstance(theme, dict):
        # Cas 1 : dict plat
        if key in theme and theme[key]:
            return str(theme[key])
        # Cas 2 : dict imbriqué sous "colors"
        colors_block = theme.get("colors")
        if isinstance(colors_block, dict) and key in colors_block and colors_block[key]:
            return str(colors_block[key])
        return default

    # Cas 3 : objet
    colors_attr = getattr(theme, "colors", None)
    if colors_attr is not None:
        # Sous-objet : theme.colors.<key>
        val = getattr(colors_attr, key, None)
        if val:
            return str(val)
        # Sous-dict : theme.colors[key]
        if isinstance(colors_attr, dict):
            val = colors_attr.get(key)
            if val:
                return str(val)

    # Attribut direct sur l'objet
    val = getattr(theme, key, None)
    if val:
        return str(val)

    return default


# ─── Constructeur principal ────────────────────────────────────────────────


@register_viz("correlation_heatmap")
def build_correlation_heatmap(config: HeatmapConfig) -> dict[str, Any]:
    """
    Construit un objet Plotly figure-as-dict pour une matrice de corrélation.

    Retourne un dict JSON-safe directement consommable par le frontend
    (Plotly.react ou Streamlit). Aucun calcul statistique ici : la matrice
    DOIT déjà être calculée en amont par `stats/correlation.py`.

    Args:
        config: HeatmapConfig (cf. docstring de la classe).

    Returns:
        Dict de la forme {data: [...], layout: {...}, config: {...}} — le
        format figure JSON de Plotly.

    Raises:
        ValueError si la matrice n'est pas carrée ou ne matche pas labels.
    """
    labels = list(config.get("labels", []))
    matrix = config.get("matrix", [])

    _validate_inputs(labels, matrix)

    theme = get_theme()

    color_scale: str = config.get("color_scale", _DEFAULT_COLOR_SCALE)
    zmin: float = float(config.get("zmin", _DEFAULT_ZMIN))
    zmax: float = float(config.get("zmax", _DEFAULT_ZMAX))
    show_values: bool = bool(config.get("show_values", _DEFAULT_SHOW_VALUES))
    bold_threshold: float = float(
        config.get(
            "annotation_threshold", _DEFAULT_ANNOTATION_BOLD_THRESHOLD
        )
    )

    title: str = str(config.get("title", "Matrice de corrélation"))
    subtitle: str = str(config.get("subtitle", ""))
    method: str = str(config.get("method", ""))

    # Trace heatmap principale.
    heatmap_trace: dict[str, Any] = {
        "type": "heatmap",
        "x": labels,
        "y": labels,
        "z": matrix,
        "colorscale": color_scale,
        "zmin": zmin,
        "zmax": zmax,
        "zmid": 0.0,
        "colorbar": {
            "title": {"text": method.capitalize() if method else "corr"},
            "thickness": 12,
            "len": 0.85,
        },
        "hovertemplate": (
            "<b>%{y}</b> vs <b>%{x}</b><br>"
            "corr = %{z:.3f}<extra></extra>"
        ),
    }

    data: list[dict[str, Any]] = [heatmap_trace]

    # Annotations : valeurs dans les cases.
    annotations: list[dict[str, Any]] = []
    if show_values:
        annotations = _build_value_annotations(
            labels=labels,
            matrix=matrix,
            bold_threshold=bold_threshold,
            theme=theme,
        )

    # Layout : on hérite du thème, on surcharge ce qui est spécifique.
    layout = _build_layout(
        theme=theme,
        title=title,
        subtitle=subtitle,
        n_labels=len(labels),
        annotations=annotations,
    )

    figure: dict[str, Any] = {
        "data": data,
        "layout": layout,
        "config": {
            "displayModeBar": False,
            "responsive": True,
        },
    }
    return figure


# ─── Helpers ───────────────────────────────────────────────────────────────


def _validate_inputs(
    labels: list[str], matrix: list[list[float | None]]
) -> None:
    """Validation stricte : matrice carrée, dim cohérente avec labels."""
    n = len(labels)
    if n < 2:
        raise ValueError(
            f"Heatmap : il faut au moins 2 labels, reçu {n}."
        )
    if len(matrix) != n:
        raise ValueError(
            f"Heatmap : nombre de lignes ({len(matrix)}) "
            f"≠ nombre de labels ({n})."
        )
    for i, row in enumerate(matrix):
        if len(row) != n:
            raise ValueError(
                f"Heatmap : ligne {i} a {len(row)} colonnes, "
                f"attendu {n}."
            )


def _build_value_annotations(
    *,
    labels: list[str],
    matrix: list[list[float | None]],
    bold_threshold: float,
    theme: Any,
) -> list[dict[str, Any]]:
    """
    Construit les annotations texte au centre de chaque case.
    Couleur adaptative : blanc sur fond très saturé, sinon couleur du thème.
    """
    out: list[dict[str, Any]] = []
    text_color_dark = _theme_get(theme, "text", _FALLBACK_TEXT_DARK)
    text_color_light = _FALLBACK_TEXT_LIGHT

    for i, row_label in enumerate(labels):
        for j, col_label in enumerate(labels):
            val = matrix[i][j]
            if val is None:
                txt = "—"
                color = text_color_dark
                bold = False
            else:
                txt = f"{val:.2f}"
                strong = abs(val) >= bold_threshold
                # Fond très saturé → texte blanc pour lisibilité.
                color = text_color_light if strong else text_color_dark
                bold = strong

            out.append(
                {
                    "x": col_label,
                    "y": row_label,
                    "text": (
                        f"<b>{txt}</b>" if bold else txt
                    ),
                    "showarrow": False,
                    "font": {
                        "size": 11,
                        "color": color,
                    },
                }
            )
    return out


def _build_layout(
    *,
    theme: Any,
    title: str,
    subtitle: str,
    n_labels: int,
    annotations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Layout Plotly avec héritage du thème centralisé."""
    # On dimensionne le graphe proportionnellement au nombre de labels
    # pour que les cases restent lisibles. Bornes conservatives.
    side_px = max(360, min(720, 80 * n_labels + 200))

    text_color = _theme_get(theme, "text", _FALLBACK_TEXT_DARK)
    muted_color = _theme_get(theme, "text_muted", _FALLBACK_TEXT_MUTED)
    background = _theme_get(theme, "background", _FALLBACK_BACKGROUND)
    font_family = _theme_get(theme, "font_family", _FALLBACK_FONT_FAMILY)

    full_title = title
    if subtitle:
        full_title = (
            f"{title}<br><span style='font-size:0.8em; "
            f"color:{muted_color}'>"
            f"{subtitle}</span>"
        )

    layout: dict[str, Any] = {
        "title": {
            "text": full_title,
            "x": 0.5,
            "xanchor": "center",
            "font": {
                "size": 16,
                "color": text_color,
            },
        },
        "xaxis": {
            "side": "bottom",
            "tickangle": -30 if n_labels > 4 else 0,
            "automargin": True,
            "showgrid": False,
            "zeroline": False,
        },
        "yaxis": {
            "automargin": True,
            "autorange": "reversed",  # ligne 1 en haut, standard heatmap
            "showgrid": False,
            "zeroline": False,
        },
        "width": side_px,
        "height": side_px,
        "margin": {"l": 80, "r": 40, "t": 80, "b": 80},
        "paper_bgcolor": background,
        "plot_bgcolor": background,
        "font": {
            "family": font_family,
            "size": 12,
            "color": text_color,
        },
        "annotations": annotations,
    }
    return layout
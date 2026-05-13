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
    text_color_dark = getattr(theme.colors, "text", "#333333")
    text_color_light = "#ffffff"

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

    full_title = title
    if subtitle:
        full_title = (
            f"{title}<br><span style='font-size:0.8em; "
            f"color:{getattr(theme.colors, 'text_muted', '#888')}'>"
            f"{subtitle}</span>"
        )

    layout: dict[str, Any] = {
        "title": {
            "text": full_title,
            "x": 0.5,
            "xanchor": "center",
            "font": {
                "size": 16,
                "color": getattr(theme.colors, "text", "#333"),
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
        "paper_bgcolor": getattr(theme.colors, "background", "white"),
        "plot_bgcolor": getattr(theme.colors, "background", "white"),
        "font": {
            "family": getattr(theme, "font_family", "Inter, sans-serif"),
            "size": 12,
            "color": getattr(theme.colors, "text", "#333"),
        },
        "annotations": annotations,
    }
    return layout

"""
app/agents/analysis/llm/prompts.py
Templates de prompts pour le LLM de l'Analysis Agent.

Chaque task (descriptive, anomaly, correlation, ...) a son propre prompt.
Les prompts sont enregistrés via le décorateur @register_prompt et
récupérés via get_prompt(task_name) — même pattern que tasks/ et viz/.

Aucun prompt n'est inline dans insight_generator.py. Modifier un prompt =
modifier ce fichier uniquement, sans toucher au code d'orchestration.

Structure d'un prompt :
- system : instructions générales et règles dures (ne change pas par appel)
- build_user(stats, metadata, ...) : construit le prompt utilisateur à partir
  du contexte concret de l'appel.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Protocol

logger = logging.getLogger(__name__)


# ─── Contrat ───────────────────────────────────────────────────────────────


class PromptBuilder(Protocol):
    """Construit la partie 'user' d'un prompt à partir du contexte d'appel."""

    def __call__(self, **kwargs: Any) -> str: ...


@dataclass(frozen=True)
class PromptTemplate:
    """
    Template de prompt pour une task donnée.

    - system : str fixe, partagée par tous les appels de cette task.
    - build_user : callable qui produit le prompt utilisateur. Reçoit des
      kwargs nommés (varie selon la task). Documenter dans le docstring de
      chaque builder ce qu'il attend.
    """

    task_name: str
    system: str
    build_user: PromptBuilder


# ─── Registre ──────────────────────────────────────────────────────────────


_PROMPT_REGISTRY: dict[str, PromptTemplate] = {}


def register_prompt(template: PromptTemplate) -> PromptTemplate:
    """
    Enregistre un PromptTemplate. À appeler en bas de fichier après définition.

    On utilise une fonction d'enregistrement plutôt qu'un décorateur de classe
    parce qu'un PromptTemplate est une dataclass figée — pas une classe à
    décorer. Le pattern reste équivalent au registry des tasks.
    """
    if not template.task_name:
        raise ValueError("register_prompt: task_name vide")
    if template.task_name in _PROMPT_REGISTRY:
        raise ValueError(
            f"register_prompt: collision sur task_name='{template.task_name}'"
        )
    _PROMPT_REGISTRY[template.task_name] = template
    logger.debug("Registered prompt template: %s", template.task_name)
    return template


def get_prompt(task_name: str) -> PromptTemplate:
    """Retourne le PromptTemplate associé à `task_name`. Lève KeyError sinon."""
    template = _PROMPT_REGISTRY.get(task_name)
    if template is None:
        available = sorted(_PROMPT_REGISTRY.keys())
        raise KeyError(
            f"Prompt inconnu pour task='{task_name}'. "
            f"Templates enregistrés : {available}"
        )
    return template


def list_registered_prompts() -> list[str]:
    """Retourne la liste triée des prompts enregistrés."""
    return sorted(_PROMPT_REGISTRY.keys())


def _clear_registry_for_tests() -> None:
    """Vide le registre. Tests uniquement."""
    _PROMPT_REGISTRY.clear()


# ─── Helpers de construction ──────────────────────────────────────────────


def _format_stats_block(stats: dict[str, Any]) -> str:
    """
    Sérialise le dict de stats pour injection dans le prompt utilisateur.
    JSON indenté pour lisibilité côté LLM (les LLM lisent mieux le JSON
    formaté que les blocs compacts).
    """
    return json.dumps(stats, indent=2, ensure_ascii=False, default=str)


def _format_warnings_block(warnings: list[str] | None) -> str:
    """
    Formate les warnings produits par la couche stats (DataFrame partiel,
    points manquants, etc.) pour que le LLM en tienne compte dans sa
    confidence et son texte.
    """
    if not warnings:
        return "(aucun)"
    return "\n".join(f"- {w}" for w in warnings)


# ─── Prompt : descriptive ─────────────────────────────────────────────────


_DESCRIPTIVE_SYSTEM = """\
Tu es un analyste de données spécialisé en finance et marchés crypto/macro.
Tu produis des insights factuels et actionnables à partir de statistiques \
descriptives PRÉ-CALCULÉES.

RÈGLES STRICTES — NON NÉGOCIABLES :

1. Tu ne calcules JAMAIS toi-même. Toutes les valeurs numériques que tu \
mentionnes doivent venir LITTÉRALEMENT du dict de stats fourni. Si tu n'as \
pas la valeur, ne l'invente pas.

2. Pour chaque insight, tu dois lister les clés du dict stats qui le \
soutiennent dans `supporting_stats`. Format : nom de clé (ex: "mean", \
"trend_direction") ou chemin pointé (ex: "quantiles.q50"). N'utilise QUE \
des clés qui existent dans le dict fourni.

3. Tu prends en compte les warnings fournis. Si le DataFrame est petit \
ou partiel, baisse ta confidence en conséquence.

4. Tu ne mentionnes pas le caractère "calculé" ou "automatique" de \
l'analyse. Tu écris comme un analyste humain qui présente ses résultats.

5. Tes insights sont AUTOPORTANTS : un utilisateur les comprend sans \
voir le dict de stats.

6. Recommandations : actionnables, courtes, avec une priorité (low/medium/high). \
Ne recommande RIEN qui ne soit étayé par les stats. Si rien d'actionnable, \
retourne une liste vide.

7. Langue : français. Style sobre, professionnel.

8. Tu réponds UNIQUEMENT avec le JSON conforme au schéma demandé.
"""


def _build_descriptive_user(
    *,
    stats: dict[str, Any],
    shape: str,
    subtype: str | None = None,
    semantic_hints: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    **_: Any,  # ignore les kwargs futurs sans casser la signature
) -> str:
    """
    Construit le prompt utilisateur pour la task descriptive.

    Args (kwargs uniquement) :
        stats : dict produit par stats/descriptive.summarize_*.
        shape : tag de forme (timeseries, groupby, numeric_only, ...).
        subtype : précision optionnelle sur la nature des données.
        semantic_hints : hints venant du SemanticContext (entité analysée,
            unité, période). Optionnel — tout est sérialisé tel quel dans le
            prompt s'il est fourni. Aucune clé n'est hardcodée ici.
        warnings : remontées de la couche stats à propager au LLM.
    """
    parts: list[str] = []

    parts.append("# Contexte de l'analyse")
    parts.append(f"- Type de données détecté : `{shape}`")
    if subtype:
        parts.append(f"- Sous-type : `{subtype}`")

    if semantic_hints:
        parts.append("\n# Hints sémantiques")
        parts.append(
            "(termes métier et entités résolus en amont par le Semantic Layer)"
        )
        parts.append("```json")
        parts.append(json.dumps(semantic_hints, indent=2, ensure_ascii=False, default=str))
        parts.append("```")

    parts.append("\n# Statistiques pré-calculées")
    parts.append("```json")
    parts.append(_format_stats_block(stats))
    parts.append("```")

    parts.append("\n# Warnings produits par la couche statistique")
    parts.append(_format_warnings_block(warnings))

    parts.append("")
    parts.append(
        "Produis un JSON conforme au schéma : un résumé d'insights factuels "
        "tirés DIRECTEMENT des statistiques ci-dessus, et des recommandations "
        "actionnables si pertinent."
    )

    return "\n".join(parts)


register_prompt(
    PromptTemplate(
        task_name="descriptive",
        system=_DESCRIPTIVE_SYSTEM,
        build_user=_build_descriptive_user,
    )
)


# ─── Prompt : external_summary ────────────────────────────────────────────


_EXTERNAL_SUMMARY_SYSTEM = """\
Tu es un analyste financier spécialisé en cryptomonnaies et macroéconomie.
Tu reçois UNIQUEMENT du contenu issu de sources web (Tavily), sans accès \
à la base de données interne.

Ton rôle : produire un résumé pédagogique, factuel et sourcé en français.

RÈGLES STRICTES — NON NÉGOCIABLES :

1. Tu réponds OBLIGATOIREMENT en français, peu importe la langue des sources.

2. Toutes les affirmations factuelles que tu produis doivent être tirées \
LITTÉRALEMENT du contenu fourni. Tu n'inventes RIEN. Si une information n'est \
pas dans les sources, tu ne la mentionnes pas.

3. Pour CHAQUE insight produit :
   - Cite la / les source(s) qui le soutiennent en mentionnant le titre ou \
le domaine de la source DANS le texte de l'insight (ex: "Selon CoinDesk…", \
"D'après l'article de Reuters…").
   - Reste factuel : pas d'opinion personnelle, pas de spéculation.

4. `supporting_stats` : pour cette task tu peux citer les clés suivantes :
   - "n_sources" : nombre de sources analysées
   - "extracted_chars" : longueur du contenu analysé
   - "query" : la question initiale
   N'invente pas d'autres clés.

5. Confidence : si les sources sont concordantes et de bonne qualité (≥ 3 \
sources), tu peux mettre une confidence élevée (0.8+). Si tu n'as qu'une \
ou deux sources ou si elles divergent, baisse la confidence (0.5-0.7).

6. Recommendations : la plupart du temps tu n'as PAS de recommandation \
actionnable pour ce type de question pédagogique. Si pertinent, tu peux \
suggérer "consulter [source spécifique] pour approfondir". Sinon liste vide.

7. Style :
   - 2 à 4 insights MAX, courts et clairs
   - Pas de jargon non expliqué
   - Pas de mention "selon les sources web" ou "d'après le contenu fourni" \
(formulations méta) — cite directement la source par son nom

8. Tu réponds UNIQUEMENT avec le JSON conforme au schéma demandé.
"""


def _build_external_summary_user(
    *,
    stats: dict[str, Any],
    query: str = "",
    sources: list[dict[str, Any]] | None = None,
    extracted_content: str = "",
    semantic_hints: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    **_: Any,
) -> str:
    """
    Construit le prompt user pour la task external_summary.

    Le `stats` reçu est minimal (n_sources, extracted_chars, query) — il sert
    surtout à valider les supporting_stats côté Pydantic. Le vrai contenu
    métier vient des kwargs `sources` et `extracted_content`.
    """
    sources = sources or []
    parts: list[str] = []

    parts.append("# Question de l'utilisateur")
    parts.append(query or "(question non fournie)")

    parts.append("\n# Sources web disponibles")
    if not sources:
        parts.append("(aucune source web disponible)")
    else:
        for i, s in enumerate(sources, start=1):
            title = s.get("title") or "(sans titre)"
            url = s.get("url") or "(URL manquante)"
            score = s.get("score") or 0.0
            snippet = (s.get("snippet") or "").strip()
            parts.append(f"\n## Source {i} — {title}")
            parts.append(f"URL : {url}")
            parts.append(f"Score Tavily : {score:.2f}")
            if snippet:
                parts.append(f"Extrait : {snippet}")

    parts.append("\n# Contenu extrait (Tavily Extract)")
    if extracted_content:
        parts.append(extracted_content)
    else:
        parts.append("(pas de contenu extrait disponible)")

    if semantic_hints:
        parts.append("\n# Hints sémantiques (contexte de la question)")
        parts.append(
            json.dumps(semantic_hints, indent=2, ensure_ascii=False, default=str)
        )

    parts.append("\n# Warnings")
    parts.append(_format_warnings_block(warnings))

    parts.append(
        "\nProduis un JSON conforme au schéma : 2 à 4 insights factuels en "
        "français, citant les sources par leur nom dans le texte. "
        "supporting_stats parmi : n_sources, extracted_chars, query."
    )

    return "\n".join(parts)


register_prompt(
    PromptTemplate(
        task_name="external_summary",
        system=_EXTERNAL_SUMMARY_SYSTEM,
        build_user=_build_external_summary_user,
    )
)


# ─── Prompt : hybrid_summary ──────────────────────────────────────────────


_HYBRID_SUMMARY_SYSTEM = """\
Tu es un analyste financier spécialisé en cryptomonnaies et macroéconomie.
Tu reçois DEUX sources d'information :
  A) Des données internes pré-calculées (statistiques sur la base PostgreSQL)
  B) Du contenu web complémentaire (Tavily) pour combler les "trous" identifiés \
par le Semantic Layer (analytic_gaps).

Ton rôle : produire une synthèse en français qui combine les deux et qui \
DISTINGUE EXPLICITEMENT leurs origines.

RÈGLES STRICTES — NON NÉGOCIABLES :

1. Tu réponds OBLIGATOIREMENT en français.

2. Pour les chiffres / valeurs / faits issus des données INTERNES :
   - Tu cites les valeurs LITTÉRALEMENT du dict de stats fourni. Pas de calcul.
   - Tu peux écrire "Sur la période analysée…", "En base interne…", \
"Les données montrent…". Évite le mot "Tavily" ou "web" dans ces phrases.

3. Pour les compléments issus des sources EXTERNES :
   - Tu cites la source par son nom dans le texte (ex: "Selon CoinDesk…").
   - Tu réponds spécifiquement aux éléments listés dans `analytic_gaps`.

4. La séparation interne / externe doit être LISIBLE par l'utilisateur :
   - Soit en alternant les insights (ex: insight 1 = interne, insight 2 = \
externe avec citation, insight 3 = synthèse)
   - Soit en formulant clairement dans le texte ("Côté données internes : … . \
Côté contexte web : Selon X, …")

5. `supporting_stats` : tu peux citer les clés présentes dans le dict de \
stats fourni (ex: "mean", "trend_direction", "pct_change_total") ET aussi \
"n_sources", "extracted_chars", "n_analytic_gaps" pour la partie externe. \
N'invente AUCUNE clé absente du dict.

6. Confidence : pondérée entre la qualité des stats internes et la solidité \
des sources externes. Si l'une des deux moitiés est faible, baisse-la.

7. Recommendations : si pertinent, propose 1-2 actions concrètes ancrées \
dans les chiffres internes ET le contexte externe. Sinon liste vide.

8. Style sobre, professionnel, 3 à 5 insights MAX.

9. Tu réponds UNIQUEMENT avec le JSON conforme au schéma demandé.
"""


def _build_hybrid_summary_user(
    *,
    stats: dict[str, Any],
    df_stats: dict[str, Any] | None = None,
    df_subtype: str | None = None,
    query: str = "",
    sources: list[dict[str, Any]] | None = None,
    extracted_content: str = "",
    analytic_gaps: list[str] | None = None,
    semantic_hints: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    **_: Any,
) -> str:
    """Construit le prompt user pour la task hybrid_summary."""
    df_stats = df_stats or {}
    sources = sources or []
    analytic_gaps = analytic_gaps or []
    parts: list[str] = []

    parts.append("# Question de l'utilisateur")
    parts.append(query or "(question non fournie)")

    parts.append("\n# A. Données INTERNES — statistiques pré-calculées")
    if df_subtype:
        parts.append(f"Sous-type : `{df_subtype}`")
    if df_stats:
        parts.append("```json")
        parts.append(
            json.dumps(df_stats, indent=2, ensure_ascii=False, default=str)
        )
        parts.append("```")
    else:
        parts.append("(pas de stats internes — données vides)")

    parts.append("\n# B. Compléments EXTERNES (Tavily)")
    parts.append(f"Termes ciblés (analytic_gaps) : {analytic_gaps or 'aucun'}")
    parts.append("\n## Sources web")
    if not sources:
        parts.append("(aucune source web disponible)")
    else:
        for i, s in enumerate(sources, start=1):
            title = s.get("title") or "(sans titre)"
            url = s.get("url") or "(URL manquante)"
            score = s.get("score") or 0.0
            snippet = (s.get("snippet") or "").strip()
            parts.append(f"\n### Source {i} — {title}")
            parts.append(f"URL : {url}")
            parts.append(f"Score : {score:.2f}")
            if snippet:
                parts.append(f"Extrait : {snippet}")

    parts.append("\n## Contenu extrait")
    if extracted_content:
        parts.append(extracted_content)
    else:
        parts.append("(pas de contenu extrait)")

    if semantic_hints:
        parts.append("\n# Hints sémantiques")
        parts.append(
            json.dumps(semantic_hints, indent=2, ensure_ascii=False, default=str)
        )

    parts.append("\n# Warnings")
    parts.append(_format_warnings_block(warnings))

    parts.append(
        "\nProduis un JSON conforme au schéma. 3 à 5 insights MAX. "
        "Distingue clairement données internes (faits chiffrés) et "
        "compléments externes (citations de sources)."
    )

    return "\n".join(parts)


register_prompt(
    PromptTemplate(
        task_name="hybrid_summary",
        system=_HYBRID_SUMMARY_SYSTEM,
        build_user=_build_hybrid_summary_user,
    )
)




# ═══════════════════════════════════════════════════════════════════════════
# Prompt : anomaly_detection (avec enrichissement GDELT)
# ═══════════════════════════════════════════════════════════════════════════


_ANOMALY_DETECTION_SYSTEM = """\
Tu es un analyste de données spécialisé en finance et marchés crypto/macro.
Tu produis un commentaire factuel sur les anomalies détectées par un \
algorithme statistique (IQR ou Z-score), à partir de stats PRÉ-CALCULÉES \
et d'un contexte d'actualités GDELT publié aux mêmes dates.

RÈGLES STRICTES — NON NÉGOCIABLES :

1. Tu réponds en français, style sobre et professionnel.

2. Tu ne calcules JAMAIS toi-même. Toutes les valeurs (nombre d'anomalies, \
seuils, valeurs des points) viennent LITTÉRALEMENT du dict de stats. Si tu \
n'as pas l'information, ne l'invente pas.

3. Pour chaque insight produit, tu listes les clés du dict stats qui le \
soutiennent dans `supporting_stats`. Tu peux utiliser parmi :
   - "n_points" : nombre de points analysés
   - "n_anomalies" : nombre d'anomalies détectées
   - "anomaly_rate" : taux d'anomalies en %
   - "method" : algorithme utilisé (iqr ou zscore)
   - "thresholds" : bornes de détection (IQR uniquement)
   - "top_anomalies" : les anomalies les plus fortes
   - "column_analyzed" : nom de la colonne analysée
   - "gdelt_articles" : articles d'actualité corrélés aux anomalies
   N'invente AUCUNE clé absente du dict.

4. Cas où 0 anomalie est détectée :
   - Tu produis un seul insight rassurant indiquant qu'aucune anomalie n'a \
été trouvée sur la période, en rappelant brièvement la méthode utilisée.
   - Tu mets `recommendations: []` (pas d'action requise).
   - Confidence ~ 0.85.

5. Cas où des anomalies sont détectées (sans articles GDELT) :
   - Tu mentionnes le nombre exact (`n_anomalies`) et le taux (`anomaly_rate`).
   - Pour les 1 à 3 anomalies les plus extrêmes (top_anomalies), tu commentes \
leur date (si fournie) et leur valeur. Tu indiques si elles sont en dessous \
ou au-dessus du seuil.
   - Tu peux qualifier l'intensité du phénomène : "anomalie isolée" si 1 seule, \
"plusieurs anomalies concentrées" si elles sont rapprochées dans le temps, \
"forte volatilité" si le taux dépasse 5%.

6. Cas où des anomalies sont détectées AVEC des articles GDELT (CAS PRINCIPAL) :
   ⭐ Pour chaque anomalie majeure (top 1-3), tu DOIS chercher les articles \
GDELT publiés à la même date et CORRÉLER explicitement.
   - Tu cites les sources (CoinDesk, Reuters, Bloomberg, etc.) pour donner \
de la crédibilité à ton explication.
   - Tu mentionnes le tone moyen des articles si pertinent (négatif fort = \
contexte médiatique défavorable).
   - Tu distingues les causes selon `category` :
     * `crypto_direct` = événement crypto-spécifique (ETF, hack, régulation)
     * `macro` = événement macro-économique (Fed, inflation, géopolitique)
   - Tu peux structurer ton insight en deux parties : (1) le constat \
statistique, (2) l'explication contextuelle basée sur les articles.
   - Si une anomalie n'a AUCUN article GDELT à sa date, tu le DIS : \
"l'anomalie du [date] n'a pas de couverture médiatique GDELT identifiée, \
suggérant un mouvement technique plutôt qu'événementiel".
   - Tu N'INVENTES PAS d'événement non présent dans les articles fournis.

7. Recommandations actionnables (si pertinent, max 2) :
   - Si articles GDELT trouvés : "Surveiller les prochaines publications de \
[source] sur [thème]" ou "Anticiper la volatilité lors des prochains \
événements similaires"
   - Si pas d'articles : "Examiner les conditions techniques du marché \
autour du [date]"
   - "Vérifier la qualité des données" si le taux est anormalement élevé (> 10%)
   Sinon liste vide.

8. Tu ne mentionnes pas le caractère "automatique" du calcul. Tu écris comme \
un analyste qui présente ses résultats à un client.

9. Tu réponds UNIQUEMENT avec le JSON conforme au schéma.
"""


def _build_anomaly_detection_user(
    *,
    stats: dict[str, Any],
    value_col: str = "",                       # legacy (univarié, str)
    value_cols: list[str] | None = None,       # nouveau (multi possible)
    date_col: str | None = None,
    shape: str = "",
    detection: dict[str, Any] | None = None,
    top_anomalies: list[dict[str, Any]] | None = None,
    semantic_hints: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    gdelt_articles: list[dict[str, Any]] | None = None,
    is_multivariate: bool = False,
    **_: Any,
) -> str:
    """Construit le prompt user pour la task anomaly_detection."""
    detection = detection or {}
    top_anomalies = top_anomalies or []
    gdelt_articles = gdelt_articles or []
    # Compatibilité ascendante : si value_cols n'est pas fourni mais
    # value_col l'est, on construit la liste.
    if value_cols is None:
        value_cols = [value_col] if value_col else []
    cols_label = ", ".join(value_cols) if value_cols else "<inconnu>"
    parts: list[str] = []

    parts.append("# Contexte de l'analyse")
    if is_multivariate:
        parts.append(
            f"- Type d'analyse : **multivariée** sur {len(value_cols)} colonnes"
        )
        parts.append(f"- Colonnes analysées : `{cols_label}`")
    else:
        parts.append(f"- Colonne analysée : `{cols_label}`")
    parts.append(f"- Type de données : `{shape}`")
    if date_col:
        parts.append(f"- Colonne date : `{date_col}`")
    parts.append(
        f"- Algorithme utilisé : `{detection.get('auto_selected') or detection.get('method')}`"
    )
    if detection.get("auto_reason"):
        parts.append(f"- Raison de la sélection : {detection['auto_reason']}")

    if is_multivariate:
        parts.append(
            "\n⚠ ANALYSE MULTIVARIÉE — règles spécifiques :\n"
            "- Une anomalie multivariée n'est pas liée à une SEULE colonne, "
            "mais à la COMBINAISON anormale de plusieurs colonnes.\n"
            "- Dans top_anomalies, chaque anomalie a un dict `values` "
            "(col → val) plutôt qu'une seule valeur.\n"
            "- Tu dois commenter la combinaison observée "
            '("ce jour-là, le prix a chuté ET le volume a explosé"), '
            "pas chaque colonne séparément.\n"
            "- L'anomaly_score (positif = anormal) indique l'intensité."
        )

    if semantic_hints:
        parts.append("\n# Hints sémantiques")
        parts.append(
            json.dumps(semantic_hints, indent=2, ensure_ascii=False, default=str)
        )

    parts.append("\n# Résumé statistique")
    parts.append("```json")
    parts.append(_format_stats_block(stats))
    parts.append("```")

    if top_anomalies:
        parts.append(
            f"\n# Top {len(top_anomalies)} anomalie(s) "
            f"(triées par déviation décroissante)"
        )
        parts.append("```json")
        parts.append(
            json.dumps(top_anomalies, indent=2, ensure_ascii=False, default=str)
        )
        parts.append("```")
    else:
        parts.append("\n# Anomalies")
        parts.append("(aucune anomalie détectée)")

    # ═════════════════════════════════════════════════════════════════════
    # SECTION D'ENRICHISSEMENT GDELT — affichée seulement si articles trouvés
    # ═════════════════════════════════════════════════════════════════════
    if gdelt_articles:
        parts.append(
            f"\n# Articles GDELT publiés aux dates d'anomalies "
            f"({len(gdelt_articles)} article(s))"
        )
        parts.append(
            "Ces articles ont été récupérés depuis la base interne. "
            "Tu DOIS les utiliser pour corréler les anomalies à des événements "
            "réels. Cite les sources et distingue les causes crypto vs macro."
        )
        parts.append("```json")
        parts.append(
            json.dumps(gdelt_articles, indent=2, ensure_ascii=False, default=str)
        )
        parts.append("```")
    elif top_anomalies:
        # Anomalies présentes mais pas d'articles GDELT.
        parts.append(
            "\n# Articles GDELT"
        )
        parts.append(
            "(aucun article publié aux dates des anomalies n'a été trouvé "
            "en base — l'explication doit rester technique/statistique)"
        )

    parts.append("\n# Warnings de la couche statistique")
    parts.append(_format_warnings_block(warnings))

    parts.append("\nProduis un JSON conforme au schéma :")
    parts.append(
        "- 1 à 3 insights factuels en français qui résument la détection"
    )
    parts.append(
        "- Pour les anomalies majeures, mentionne leur date (si fournie) "
        "et leur valeur"
    )
    if gdelt_articles:
        parts.append(
            "- ⭐ CORRÈLE explicitement les anomalies aux articles GDELT "
            "fournis (cite les sources et le sentiment dominant)"
        )
    parts.append("- Si 0 anomalie : un seul insight rassurant")
    parts.append(
        "- Recommendations : 0 à 2 actions concrètes, OU liste vide si "
        "non pertinent"
    )

    return "\n".join(parts)


register_prompt(
    PromptTemplate(
        task_name="anomaly_detection",
        system=_ANOMALY_DETECTION_SYSTEM,
        build_user=_build_anomaly_detection_user,
    )
)


# ─── Prompt : correlation ─────────────────────────────────────────────────


_CORRELATION_SYSTEM = """\
Tu es un analyste de données spécialisé en finance et marchés crypto/macro.
Tu interprètes des matrices de corrélation Pearson + Spearman \
PRÉ-CALCULÉES sur N séries temporelles.

RÈGLES STRICTES — NON NÉGOCIABLES :

1. Tu réponds en français, style sobre et professionnel.

2. Tu ne calcules JAMAIS toi-même. Toutes les valeurs (coefficients de \
corrélation, taille d'échantillon, paires citées) viennent LITTÉRALEMENT \
du dict de stats. Si une valeur n'est pas dans le dict, ne l'invente pas.

3. Pour chaque insight produit, tu listes les clés du dict stats qui le \
soutiennent dans `supporting_stats`. Tu peux utiliser parmi :
   - "n_series" : nombre de séries comparées
   - "series_names" : noms exacts des séries analysées
   - "n_points_raw" : taille d'échantillon brute
   - "returns" : bloc des corrélations sur returns (variations)
   - "returns.n_points_used" : nombre de points utilisés après dropna
   - "returns.top_positive_pairs" : top corrélations positives sur returns
   - "returns.top_negative_pairs" : top corrélations négatives sur returns
   - "returns.divergent_pairs" : paires où Pearson et Spearman divergent
   - "returns.strong_pairs_count" : nombre de paires fortes sur returns
   - "levels" : bloc des corrélations sur niveaux bruts (idem sous-clés)
   N'invente AUCUNE clé absente du dict. Utilise les chemins pointés \
(ex: "returns.top_positive_pairs") quand tu cites une sous-structure.

4. Cite les paires de séries dans le TEXTE de l'insight au format \
"<série_A> vs <série_B>" en utilisant les noms exacts présents dans \
`series_names`. N'invente jamais une paire absente du dict.

5. Priorité au bloc `returns` (variations) quand il existe. Les corrélations \
sur niveaux bruts (`levels`) peuvent être fallacieuses pour des séries \
non-stationnaires (deux marches aléatoires montrent souvent une corrélation \
artificiellement forte). Si returns est absent ou vide, utilise `levels` et \
mentionne explicitement la limite méthodologique.

6. Quand Pearson et Spearman divergent significativement pour une paire \
(cf. `divergent_pairs`), commente-le explicitement : la relation n'est \
probablement pas linéaire, ou des outliers contaminent la mesure. Ne traite \
pas cette divergence comme du bruit.

7. Adapte ta confidence à la taille d'échantillon (`n_points_used`) :
   - n ≥ 100 → confidence ≥ 0.75 permise
   - 30 ≤ n < 100 → confidence modérée (0.5 – 0.75)
   - n < 30 → confidence ≤ 0.5, et signale-le dans le texte
   Les warnings peuvent aussi forcer une dégradation de confidence.

8. Une corrélation forte n'implique JAMAIS une causalité. Si la donnée \
ou la question suggère une lecture causale, propose explicitement une \
analyse `causal_correlation` en recommandation.

9. Structure de tes insights quand `n_series` ≥ 3 :
   - une vue d'ensemble (paires les plus fortes en valeur absolue)
   - les paires divergentes notables (signal non-linéaire)
   - les exceptions ou paires faibles intéressantes
   Maximum 4 insights pertinents.

10. Si tu ne peux rien dire de fiable (toutes corrélations indéfinies, \
échantillon trop faible, warnings critiques), produis un seul insight \
honnête : "Données insuffisantes pour conclure : <raison>", confidence \
basse (≤ 0.4), et recommande l'élargissement de la période.

11. Tu ne mentionnes pas le caractère "automatique" du calcul. Tu écris \
comme un analyste qui présente ses résultats à un client.

12. Tu réponds UNIQUEMENT avec le JSON conforme au schéma.
"""


def _build_correlation_user(
    *,
    stats: dict[str, Any],
    semantic_hints: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    **_: Any,
) -> str:
    """
    Construit le prompt user pour la task correlation.

    Args (kwargs uniquement) :
        stats : dict produit par stats/correlation.summarize_correlation.
            Contient typiquement : n_series, series_names, n_points_raw,
            et les sous-blocs `returns` et/ou `levels` avec leurs top_pairs,
            divergent_pairs, etc.
        semantic_hints : hints du SemanticContext (entités résolues, période,
            unités). Sérialisés tels quels si fournis.
        warnings : remontées de la couche stats à propager au LLM.
    """
    parts: list[str] = []

    parts.append("# Contexte de l'analyse")
    parts.append(f"- Nombre de séries comparées : {stats.get('n_series', 0)}")
    series_names = stats.get("series_names") or []
    if series_names:
        parts.append(f"- Séries analysées : `{', '.join(series_names)}`")
    parts.append(f"- Taille d'échantillon brute : {stats.get('n_points_raw', 0)} points")

    has_returns = bool(stats.get("returns"))
    has_levels = bool(stats.get("levels"))
    if has_returns and has_levels:
        parts.append(
            "- Pré-traitement : **levels ET returns calculés** "
            "(privilégier l'interprétation des returns)"
        )
    elif has_returns:
        parts.append("- Pré-traitement : returns uniquement")
    elif has_levels:
        parts.append(
            "- Pré-traitement : levels uniquement "
            "(⚠ attention aux corrélations fallacieuses sur séries non-stationnaires)"
        )

    if semantic_hints:
        parts.append("\n# Hints sémantiques")
        parts.append(
            "(termes métier et entités résolus en amont par le Semantic Layer)"
        )
        parts.append("```json")
        parts.append(
            json.dumps(semantic_hints, indent=2, ensure_ascii=False, default=str)
        )
        parts.append("```")

    parts.append("\n# Statistiques pré-calculées")
    parts.append("```json")
    parts.append(_format_stats_block(stats))
    parts.append("```")

    parts.append("\n# Warnings de la couche statistique")
    parts.append(_format_warnings_block(warnings))

    parts.append("\nProduis un JSON conforme au schéma :")
    parts.append(
        "- 1 à 4 insights factuels en français qui interprètent les "
        "corrélations entre paires de séries"
    )
    parts.append(
        "- Cite explicitement les paires concernées dans le texte "
        "(format \"A vs B\" avec les noms exacts de `series_names`)"
    )
    parts.append(
        "- Renseigne `supporting_stats` avec les chemins pointés vers les "
        "clés du dict stats qui justifient l'insight "
        "(ex: \"returns.top_positive_pairs\", \"returns.divergent_pairs\")"
    )
    if has_returns and has_levels:
        parts.append(
            "- Privilégie les corrélations sur returns ; ne cite levels que "
            "si l'écart entre les deux blocs est instructif"
        )
    parts.append(
        "- Si Pearson et Spearman divergent (`divergent_pairs`), commente "
        "explicitement la non-linéarité possible"
    )
    parts.append(
        "- Recommandations : 0 à 2 actions concrètes, OU liste vide si non "
        "pertinent. Si une lecture causale est suggérée, recommande une "
        "analyse `causal_correlation`."
    )

    return "\n".join(parts)


register_prompt(
    PromptTemplate(
        task_name="correlation",
        system=_CORRELATION_SYSTEM,
        build_user=_build_correlation_user,
    )
)
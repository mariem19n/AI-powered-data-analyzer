"""
streamlit_app.py
AI-Powered Data Analyzer — Interface Streamlit

Pipeline complet du Semantic Layer :
  1. Pré-traitement + Extraction LLM + Validation  
  2. KG Lookup / Résolution                         
  3. Règles métier implicites                       
  4. Construction du SemanticContext JSON         

Usage :
    streamlit run streamlit_app.py
"""

import sys
import time
import json
import logging
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env")

from app.semantic.extractor import BusinessTermsExtractor
from app.semantic.resolver import KGResolver
from app.semantic.rules import RulesEnricher
from app.semantic.context_builder import SemanticContextBuilder
from app.semantic.schemas import TermCategory
from app.db.neo4j import neo4j_driver

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ─── Page config ──────────────────────────────────────────────

st.set_page_config(
    page_title="AI Data Analyzer",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Custom CSS ───────────────────────────────────────────────

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&family=DM+Sans:wght@400;500;600;700&display=swap');

    .stApp { font-family: 'DM Sans', sans-serif; }

    .tag-chip {
        display: inline-block;
        padding: 4px 12px;
        margin: 3px 4px;
        border-radius: 20px;
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.8rem;
        font-weight: 500;
        border: 1px solid;
    }
    .tag-bt   { background: rgba(59,130,246,0.12); color: #60a5fa; border-color: rgba(59,130,246,0.3); }
    .tag-ent  { background: rgba(16,185,129,0.12); color: #34d399; border-color: rgba(16,185,129,0.3); }
    .tag-tp   { background: rgba(168,139,250,0.12); color: #c4b5fd; border-color: rgba(168,139,250,0.3); }
    .tag-met  { background: rgba(34,211,238,0.12); color: #67e8f9; border-color: rgba(34,211,238,0.3); }
    .tag-unr  { background: rgba(245,158,11,0.12); color: #fbbf24; border-color: rgba(245,158,11,0.3); }
    .tag-gap  { background: rgba(251,146,60,0.12); color: #fb923c; border-color: rgba(251,146,60,0.3); }
    .tag-unk  { background: rgba(239,68,68,0.12); color: #f87171; border-color: rgba(239,68,68,0.3); }
    .tag-pred { background: rgba(16,185,129,0.12); color: #34d399; border-color: rgba(16,185,129,0.3); }
    .tag-guide { background: rgba(168,139,250,0.12); color: #c4b5fd; border-color: rgba(168,139,250,0.3); }

    .status-ok     { display:inline-block; padding:4px 14px; border-radius:20px; font-size:0.82rem; font-weight:600; background:rgba(16,185,129,0.15); color:#34d399; border:1px solid rgba(16,185,129,0.35); }
    .status-warn   { display:inline-block; padding:4px 14px; border-radius:20px; font-size:0.82rem; font-weight:600; background:rgba(245,158,11,0.15); color:#fbbf24; border:1px solid rgba(245,158,11,0.35); }
    .status-empty  { display:inline-block; padding:4px 14px; border-radius:20px; font-size:0.82rem; font-weight:600; background:rgba(239,68,68,0.15); color:#f87171; border:1px solid rgba(239,68,68,0.35); }

    .correction-box {
        padding: 10px 14px; margin: 6px 0; border-radius: 8px;
        background: rgba(245,158,11,0.08); border-left: 3px solid #f59e0b;
        font-size: 0.88rem;
    }
    .validation-box {
        padding: 10px 14px; margin: 6px 0; border-radius: 8px;
        background: rgba(59,130,246,0.08); border-left: 3px solid #3b82f6;
        font-size: 0.88rem;
    }
    .resolver-box {
        padding: 10px 14px; margin: 6px 0; border-radius: 8px;
        background: rgba(16,185,129,0.08); border-left: 3px solid #10b981;
        font-size: 0.88rem;
    }
    .rule-box {
        padding: 10px 14px; margin: 6px 0; border-radius: 8px;
        background: rgba(168,139,250,0.08); border-left: 3px solid #a78bfa;
        font-size: 0.88rem;
    }
    .context-box {
        padding: 10px 14px; margin: 6px 0; border-radius: 8px;
        background: rgba(34,211,238,0.08); border-left: 3px solid #22d3ee;
        font-size: 0.88rem;
    }

    .confidence-bar {
        height: 8px; border-radius: 4px; background: #1e293b;
        overflow: hidden; margin-top: 4px;
    }
    .confidence-fill {
        height: 100%; border-radius: 4px; transition: width 0.3s ease;
    }

    .metric-card {
        text-align: center; padding: 16px 8px;
        border-radius: 10px; background: rgba(30,41,59,0.5);
        border: 1px solid rgba(51,65,85,0.5);
    }
    .metric-card .number { font-family: 'JetBrains Mono', monospace; font-size: 1.8rem; font-weight: 700; }
    .metric-card .label  { font-size: 0.78rem; color: #64748b; margin-top: 4px; }

    .stage-header {
        font-size: 1.1rem; font-weight: 600; margin-bottom: 8px;
        padding: 8px 0; border-bottom: 1px solid rgba(51,65,85,0.3);
    }

    .block-container { padding-top: 2rem !important; }
</style>
""", unsafe_allow_html=True)


# ─── Session state ────────────────────────────────────────────

if "history" not in st.session_state:
    st.session_state.history = []
if "stats" not in st.session_state:
    st.session_state.stats = {"total": 0, "ok": 0, "clarification": 0, "empty": 0}


# ─── Init pipeline ───────────────────────────────────────────

@st.cache_resource
def init_pipeline():
    try:
        extractor = BusinessTermsExtractor(neo4j_driver)
        resolver = KGResolver(neo4j_driver)
        enricher = RulesEnricher(neo4j_driver)
        builder = SemanticContextBuilder()

        vocab = extractor._vocab
        kg_status = {
            "connected": True,
            "business_terms": len(vocab.business_terms),
            "entities": len(vocab.entities),
            "time_periods": len(vocab.time_periods),
            "metrics": len(vocab.metrics),
            "synonyms": len(vocab.synonyms),
            "preprocessor": extractor._preprocessor is not None,
            "validator": extractor._validator is not None,
        }
        return extractor, resolver, enricher, builder, kg_status
    except Exception as e:
        logger.error("Erreur init pipeline : %s", e)
        return None, None, None, None, {"connected": False, "error": str(e)}


extractor, resolver, enricher, builder, kg_status = init_pipeline()


# ─── Helpers ──────────────────────────────────────────────────

CATEGORY_STYLE = {
    TermCategory.BUSINESS_TERM: ("tag-bt", "#60a5fa", "Business Terms"),
    TermCategory.ENTITY:        ("tag-ent", "#34d399", "Entities"),
    TermCategory.TIME_PERIOD:   ("tag-tp", "#c4b5fd", "Time Periods"),
    TermCategory.METRIC:        ("tag-met", "#67e8f9", "Metrics"),
}


def render_confidence_bar(confidence: float, color: str) -> str:
    pct = int(confidence * 100)
    return (
        f'<div class="confidence-bar">'
        f'<div class="confidence-fill" style="width:{pct}%;background:{color}"></div>'
        f'</div>'
    )


def render_term_chip(text: str, confidence: float, css_class: str) -> str:
    opacity = max(0.6, confidence)
    return (
        f'<span class="tag-chip {css_class}" style="opacity:{opacity}">'
        f'{text} <small>({confidence:.0%})</small>'
        f'</span>'
    )


# ─── Sidebar ─────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## 🔍 AI Data Analyzer")
    st.caption("Semantic Layer — Pipeline complet")
    st.markdown("---")

    st.markdown("### Knowledge Graph")
    if kg_status and kg_status.get("connected"):
        st.success("Connecté", icon="✅")
        c1, c2 = st.columns(2)
        c1.metric("Business Terms", kg_status["business_terms"])
        c1.metric("Entities", kg_status["entities"])
        c2.metric("Time Periods", kg_status["time_periods"])
        c2.metric("Metrics", kg_status["metrics"])
        st.caption(f"🔗 {kg_status['synonyms']} synonymes")
    else:
        st.error("Non connecté", icon="❌")
        if kg_status.get("error"):
            st.caption(f"Erreur: {kg_status['error']}")

    st.markdown("---")

    st.markdown("### Pipeline")
    if kg_status and kg_status.get("connected"):
        pp_icon = "✅" if kg_status.get("preprocessor") else "❌"
        val_icon = "✅" if kg_status.get("validator") else "❌"
        st.markdown(f"{pp_icon} Pré-traitement + Extraction LLM")
        st.markdown(f"{val_icon} Validation sémantique")
        st.markdown("✅ KG Resolver (SP1-48)")
        st.markdown("✅ Règles implicites (SP1-49)")
        st.markdown("✅ SemanticContext (SP1-50)")

    st.markdown("---")

    st.markdown("### Session")
    stats = st.session_state.stats
    if stats["total"] > 0:
        cols = st.columns(3)
        cols[0].metric("✅", stats["ok"])
        cols[1].metric("⚠️", stats["clarification"])
        cols[2].metric("❌", stats["empty"])
        st.caption(f"{stats['total']} questions")
    else:
        st.caption("Aucune question posée")

    st.markdown("---")

    st.markdown("### Exemples")
    examples = [
        "montre le prix du Bitcoin ce mois",
        "quel est le sentiment autour de Solana ce mois",
        "compare le prix du Bitcoin et de l'Ethereum sur 90 jours",
        "impact du taux de la Fed sur Bitcoin en 2024",
        "quelle est la volatilite_30j du Bitcoin",
        "est ce que l ethereum et le bitcoin ont evolué de la meme maniere pendant le premier trimestre 2024",
    ]
    for ex in examples:
        if st.button(f"💬 {ex[:55]}...", key=f"ex_{hash(ex)}", use_container_width=True):
            st.session_state.example_question = ex


# ─── Main ─────────────────────────────────────────────────────

st.markdown("# Semantic Layer — Pipeline complet")
st.markdown(
    "Pré-traitement → Extraction LLM → Validation → "
    "KG Resolver → Règles implicites → SemanticContext JSON"
)
st.markdown("")

default_value = st.session_state.pop("example_question", "")
col_input, col_btn = st.columns([5, 1])

with col_input:
    question = st.text_input(
        "Question",
        value=default_value,
        placeholder="Ex: quel est le sentiment autour de Solana ce mois...",
        label_visibility="collapsed",
    )

with col_btn:
    submitted = st.button("Analyser 🚀", use_container_width=True, type="primary")

st.markdown("")


# ─── Pipeline execution ──────────────────────────────────────

if submitted and question.strip():
    if not extractor:
        st.error("Pipeline non initialisé. Vérifiez Neo4j et les variables d'environnement.")
    else:
        q = question.strip()

        # ── Exécuter le pipeline complet ──────────────────────
        timings = {}

        with st.spinner("① Extraction des termes..."):
            t0 = time.time()
            enriched_terms = extractor.extract(q)
            timings["extraction"] = time.time() - t0

        with st.spinner("② Résolution KG..."):
            t0 = time.time()
            resolved_ctx = resolver.resolve(enriched_terms)
            timings["resolver"] = time.time() - t0

        with st.spinner("③ Règles implicites..."):
            t0 = time.time()
            enriched_ctx = enricher.enrich(resolved_ctx)
            timings["rules"] = time.time() - t0

        with st.spinner("④ Construction du SemanticContext..."):
            t0 = time.time()
            semantic_ctx = builder.build(
                enriched_ctx,
                raw_question=q,
                corrected_question=(
                    enriched_terms.corrected_question
                    if hasattr(enriched_terms, "corrected_question")
                    else q
                ),
                pipeline_confidence=enriched_terms.pipeline_confidence,
            )
            timings["context"] = time.time() - t0

        total_time = sum(timings.values())

        # ── Stats session ─────────────────────────────────────
        st.session_state.stats["total"] += 1
        if semantic_ctx.needs_clarification:
            st.session_state.stats["clarification"] += 1
        elif not semantic_ctx.tables:
            st.session_state.stats["empty"] += 1
        else:
            st.session_state.stats["ok"] += 1

        st.session_state.history.insert(0, {
            "question": q,
            "semantic_ctx": semantic_ctx,
            "timings": timings,
        })

        # ── Header status ─────────────────────────────────────
        col_s, col_c, col_t = st.columns([2, 2, 1])
        with col_s:
            if semantic_ctx.needs_clarification:
                st.markdown('<span class="status-warn">⚠ Clarification nécessaire</span>', unsafe_allow_html=True)
            elif not semantic_ctx.tables:
                st.markdown('<span class="status-empty">❌ Aucun terme résolu</span>', unsafe_allow_html=True)
            else:
                st.markdown('<span class="status-ok">✅ Pipeline complet</span>', unsafe_allow_html=True)

        with col_c:
            conf = semantic_ctx.confidence
            color = "#34d399" if conf >= 0.8 else "#fbbf24" if conf >= 0.6 else "#f87171"
            st.markdown(
                f'**Confiance** {conf:.0%} {render_confidence_bar(conf, color)}',
                unsafe_allow_html=True,
            )

        with col_t:
            st.caption(f"⏱ {total_time:.2f}s")

        # ── Timing breakdown ──────────────────────────────────
        timing_cols = st.columns(4)
        timing_labels = [
            ("① Extraction", timings["extraction"]),
            ("② Resolver", timings["resolver"]),
            ("③ Règles", timings["rules"]),
            ("④ Context", timings["context"]),
        ]
        for col, (label, t) in zip(timing_cols, timing_labels):
            col.caption(f"{label}: {t:.3f}s")

        st.markdown("")

        # ══════════════════════════════════════════════════════
        # STAGE 1 — Extraction + Pré-traitement + Validation
        # ══════════════════════════════════════════════════════

        st.markdown('<div class="stage-header">① Extraction des termes (SP1-46)</div>', unsafe_allow_html=True)

        if enriched_terms.preprocessing and enriched_terms.preprocessing.is_corrected:
            st.markdown("**🔧 Pré-traitement**")
            for c in enriched_terms.preprocessing.corrections:
                st.markdown(
                    f'<div class="correction-box">'
                    f'<code>{c.original}</code> → <strong>{c.corrected}</strong> '
                    f'<em>({c.correction_type.value}, {c.confidence:.0%})</em>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        # Terms by category
        for cat, (css, color, label) in CATEGORY_STYLE.items():
            cat_terms = [t for t in enriched_terms.terms if t.category == cat]
            if cat_terms:
                chips = " ".join(render_term_chip(t.text, t.confidence, css) for t in cat_terms)
                st.markdown(f"**{label}** &nbsp; {chips}", unsafe_allow_html=True)
            else:
                st.markdown(f'**{label}** &nbsp; <span style="color:#64748b">—</span>', unsafe_allow_html=True)

        if enriched_terms.unresolved_terms:
            chips = " ".join(f'<span class="tag-chip tag-unr">{t}</span>' for t in enriched_terms.unresolved_terms)
            st.markdown(f"**Unresolved** &nbsp; {chips}", unsafe_allow_html=True)

        candidate_terms = enriched_terms.candidate_terms()
        if candidate_terms:
            chips = " ".join(
                f'<span class="tag-chip tag-unr">{t.text} <small>(candidate)</small></span>'
                for t in candidate_terms
            )
            st.markdown(f"**Candidates KG** &nbsp; {chips}", unsafe_allow_html=True)

        with st.expander("📋 JSON — EnrichedTerms", expanded=False):
            st.json(enriched_terms.model_dump(mode="json"))

        st.markdown("")

        # ══════════════════════════════════════════════════════
        # STAGE 2 — KG Resolver
        # ══════════════════════════════════════════════════════

        st.markdown('<div class="stage-header">② KG Resolver (SP1-48)</div>', unsafe_allow_html=True)

        if resolved_ctx.entities:
            st.markdown("**📍 Entities résolues**")
            for e in resolved_ctx.entities:
                st.markdown(
                    f'<div class="resolver-box">'
                    f'<strong>{e.name}</strong> ({e.entity_type}) → '
                    f'<code>{e.table}.{e.filter_column} = \'{e.filter_value}\'</code>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        if resolved_ctx.metrics:
            st.markdown("**📊 Metrics résolues**")
            for m in resolved_ctx.metrics:
                formula_preview = m.formula[:80] + "..." if len(m.formula) > 80 else m.formula
                st.markdown(
                    f'<div class="resolver-box">'
                    f'<strong>{m.name}</strong> → <code>{m.source_table}</code><br>'
                    f'<small>formula: {formula_preview}</small>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        if resolved_ctx.business_terms:
            st.markdown("**💼 Business Terms résolus**")
            for bt in resolved_ctx.business_terms:
                st.markdown(
                    f'<div class="resolver-box">'
                    f'<strong>{bt.name}</strong> → <code>{bt.table}.{bt.column}</code>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        if resolved_ctx.time_periods:
            st.markdown("**📅 Time Periods**")
            for tp in resolved_ctx.time_periods:
                status = "✅" if tp.is_canonical else "⚠️ non-canonique"
                filter_str = f" → <code>{tp.filter_expression}</code>" if tp.filter_expression else ""
                st.markdown(
                    f'<div class="resolver-box">'
                    f'{status} <strong>{tp.name}</strong>{filter_str}'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        if resolved_ctx.analytic_gaps:
            chips = " ".join(f'<span class="tag-chip tag-gap">{g}</span>' for g in resolved_ctx.analytic_gaps)
            st.markdown(f"**🔍 Analytic Gaps** &nbsp; {chips}", unsafe_allow_html=True)

        if resolved_ctx.unknown_terms:
            chips = " ".join(f'<span class="tag-chip tag-unk">{u}</span>' for u in resolved_ctx.unknown_terms)
            st.markdown(f"**❓ Unknown Terms** &nbsp; {chips}", unsafe_allow_html=True)

        tables_str = ", ".join(f"`{t}`" for t in sorted(resolved_ctx.all_tables())) or "aucune"
        st.caption(f"Tables impliquées : {tables_str}")

        with st.expander("📋 JSON — ResolvedContext (log)", expanded=False):
            st.json({
                "entities": [
                    {"name": e.name, "type": e.entity_type, "table": e.table,
                     "column": e.filter_column, "value": e.filter_value}
                    for e in resolved_ctx.entities
                ],
                "metrics": [
                    {"name": m.name, "formula": m.formula[:100], "table": m.source_table}
                    for m in resolved_ctx.metrics
                ],
                "business_terms": [
                    {"name": bt.name, "table": bt.table, "column": bt.column}
                    for bt in resolved_ctx.business_terms
                ],
                "time_periods": [
                    {"name": tp.name, "filter": tp.filter_expression, "canonical": tp.is_canonical}
                    for tp in resolved_ctx.time_periods
                ],
                "analytic_gaps": resolved_ctx.analytic_gaps,
                "unknown_terms": resolved_ctx.unknown_terms,
                "tables": sorted(resolved_ctx.all_tables()),
                "resolution_log": resolved_ctx.resolution_log,
            })

        st.markdown("")

        # ══════════════════════════════════════════════════════
        # STAGE 3 — Règles implicites
        # ══════════════════════════════════════════════════════

        st.markdown('<div class="stage-header">③ Règles métier implicites (SP1-49)</div>', unsafe_allow_html=True)

        predicates = [r for r in enriched_ctx.implicit_rules if r.is_predicate()]
        guidelines = [r for r in enriched_ctx.implicit_rules if r.is_guideline()]

        if predicates:
            st.markdown("**🔒 SQL Predicates** (injectés dans WHERE)")
            for r in predicates:
                st.markdown(
                    f'<div class="rule-box">'
                    f'<span class="tag-chip tag-pred">{r.rule_id}</span> '
                    f'<code>{r.table}</code> → <code>{r.sql_condition}</code>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        if guidelines:
            st.markdown("**📐 Query Guidelines** (consignes SQL Agent)")
            for r in guidelines:
                st.markdown(
                    f'<div class="rule-box" style="border-color:#a78bfa">'
                    f'<span class="tag-chip tag-guide">{r.rule_id}</span> '
                    f'{r.sql_condition}'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        if not predicates and not guidelines:
            st.caption("Aucune règle applicable")

        if enriched_ctx.access_filters:
            st.markdown("**🔐 Access Filters (RBAC)**")
            for f in enriched_ctx.access_filters:
                st.markdown(f"- `{f.policy_id}`: {f.sql_condition}")
        else:
            st.caption("🔐 RBAC : aucun filtre (placeholder)")

        with st.expander("📋 JSON — Rules", expanded=False):
            st.json({
                "implicit_rules": [
                    {"rule_id": r.rule_id, "table": r.table,
                     "condition": r.sql_condition, "type": r.rule_type}
                    for r in enriched_ctx.implicit_rules
                ],
                "all_sql_conditions": enriched_ctx.all_sql_conditions(),
                "generation_guidelines": enriched_ctx.generation_guidelines(),
                "access_filters": [],
                "rules_log": enriched_ctx.rules_log,
            })

        st.markdown("")

        # ══════════════════════════════════════════════════════
        # STAGE 4 — SemanticContext JSON final
        # ══════════════════════════════════════════════════════

        st.markdown('<div class="stage-header">④ SemanticContext JSON (SP1-50)</div>', unsafe_allow_html=True)

        if semantic_ctx.needs_clarification:
            st.warning(f"⚠ Clarification nécessaire : {semantic_ctx.clarification_reason}")

        # Summary cards
        cols = st.columns(6)
        summary_data = [
            (len(semantic_ctx.tables), "Tables", "#60a5fa"),
            (len(semantic_ctx.entity_filters), "Entities", "#34d399"),
            (len(semantic_ctx.metrics), "Metrics", "#67e8f9"),
            (len(semantic_ctx.columns), "Columns", "#c4b5fd"),
            (len(semantic_ctx.time_filters), "Time Filters", "#fbbf24"),
            (len(semantic_ctx.implicit_conditions), "Conditions", "#f87171"),
        ]
        for col, (count, label, color) in zip(cols, summary_data):
            col.markdown(
                f'<div class="metric-card">'
                f'<div class="number" style="color:{color}">{count}</div>'
                f'<div class="label">{label}</div></div>',
                unsafe_allow_html=True,
            )

        st.markdown("")

        # Tables detail
        if semantic_ctx.tables:
            st.markdown("**📊 Tables**")
            for t in semantic_ctx.tables:
                role_colors = {
                    "primary": "#34d399", "filter": "#fbbf24",
                    "aggregation": "#60a5fa", "join": "#c4b5fd",
                }
                role_color = role_colors.get(t.role, "#64748b")
                cols_str = ", ".join(f"`{c}`" for c in t.columns_used) if t.columns_used else "—"
                filters_str = " AND ".join(t.filters) if t.filters else "—"
                st.markdown(
                    f'<div class="context-box">'
                    f'<span style="color:{role_color};font-weight:600">[{t.role}]</span> '
                    f'<strong>{t.table_name}</strong><br>'
                    f'<small>colonnes: {cols_str} &nbsp;|&nbsp; filtres: <code>{filters_str}</code></small>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        st.markdown("")
        st.caption(f"🔑 Context hash : `{semantic_ctx.context_hash}` — utilisable comme clé cache Redis")

        # Full JSON
        with st.expander("📋 SemanticContext JSON complet", expanded=True):
            st.json(semantic_ctx.to_dict())


elif submitted:
    st.warning("Veuillez entrer une question.")


# ─── History ──────────────────────────────────────────────────

if st.session_state.history:
    st.markdown("---")
    st.markdown("### 📜 Historique")

    for entry in st.session_state.history[:10]:
        ctx = entry["semantic_ctx"]
        timings = entry["timings"]
        total_t = sum(timings.values())
        status = (
            "✅" if not ctx.needs_clarification and ctx.tables
            else "⚠️" if ctx.needs_clarification
            else "❌"
        )
        n_tables = len(ctx.tables)
        conf = ctx.confidence

        with st.expander(
            f"{status} {entry['question'][:70]} — "
            f"{n_tables} tables · {conf:.0%} · {total_t:.2f}s"
        ):
            st.json(ctx.to_dict())


# ─── Footer ───────────────────────────────────────────────────

st.markdown("---")
st.caption("AI-Powered Data Analyzer — Semantic Layer Pipeline")
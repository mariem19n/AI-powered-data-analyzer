import { useState, useEffect, useRef } from "react";

const BRAND = {
  olive: "#8d9323",
  blue: "#5580b9",
  pink: "#e14480",
  purple: "#6c367e",
};

const MOCK_RESULTS = {
  "montre le prix du Bitcoin ce mois": {
    time: 1.42,
    confidence: 0.88,
    extraction: {
      preprocessing: [{ from: "prix", to: "prix", type: "exact_match" }],
      terms: [
        { text: "prix", category: "BusinessTerm", confidence: 1.0, status: "resolved" },
        { text: "Bitcoin", category: "Entity", confidence: 1.0, status: "resolved" },
        { text: "ce mois", category: "TimePeriod", confidence: 0.9, status: "resolved" },
      ],
      unresolved: [],
    },
    resolver: {
      entities: [{ name: "Bitcoin", type: "crypto", table: "fact_crypto_daily", column: "symbol", value: "BTC" }],
      business_terms: [{ name: "prix", table: "fact_crypto_daily", column: "close_usd" }],
      metrics: [],
      time_periods: [{ name: "ce mois", filter: "date >= DATE_TRUNC('month', CURRENT_DATE)", canonical: true }],
      analytic_gaps: [],
      unknown_terms: [],
    },
    rules: {
      predicates: [
        { id: "exclude_zero_volume", table: "fact_crypto_daily", condition: "volume > 0" },
      ],
      guidelines: [
        { id: "use_parent_table", text: "Utiliser fact_crypto_daily avec WHERE symbol = :symbol" },
      ],
    },
    context: {
      tables: [{ name: "fact_crypto_daily", role: "primary", columns: ["symbol", "close_usd"], filters: ["symbol = 'BTC'", "volume > 0"] }],
      entity_filters: [{ entity: "Bitcoin", table: "fact_crypto_daily", column: "symbol", value: "BTC" }],
      columns: [{ name: "prix", table: "fact_crypto_daily", column: "close_usd" }],
      time_filters: [{ text: "ce mois", clause: "date >= DATE_TRUNC('month', CURRENT_DATE)", canonical: true }],
      conditions: ["volume > 0"],
      guidelines: ["Utiliser fact_crypto_daily avec WHERE symbol = :symbol"],
      hash: "8d805533e33bfe64",
    },
  },
  "quel est le sentiment autour de Solana ce mois": {
    time: 1.67,
    confidence: 0.92,
    extraction: {
      preprocessing: [],
      terms: [
        { text: "sentiment", category: "BusinessTerm", confidence: 1.0, status: "resolved" },
        { text: "Solana", category: "Entity", confidence: 1.0, status: "resolved" },
        { text: "ce mois", category: "TimePeriod", confidence: 0.9, status: "resolved" },
      ],
      unresolved: [],
    },
    resolver: {
      entities: [{ name: "Solana", type: "crypto", table: "fact_crypto_daily", column: "symbol", value: "SOL" }],
      business_terms: [{ name: "sentiment", table: "agg_daily_sentiment", column: "avg_tone" }],
      metrics: [],
      time_periods: [{ name: "ce mois", filter: "date >= DATE_TRUNC('month', CURRENT_DATE)", canonical: true }],
      analytic_gaps: [],
      unknown_terms: [],
    },
    rules: {
      predicates: [
        { id: "exclude_zero_volume", table: "fact_crypto_daily", condition: "volume > 0" },
        { id: "crypto_direct_sentiment", table: "agg_daily_sentiment", condition: "keyword IN ('ECON_BITCOINS', 'ECON_CRYPTOCURRENCY')" },
      ],
      guidelines: [
        { id: "use_parent_table", text: "Utiliser fact_crypto_daily avec WHERE symbol = :symbol" },
      ],
    },
    context: {
      tables: [
        { name: "fact_crypto_daily", role: "filter", columns: ["symbol"], filters: ["symbol = 'SOL'", "volume > 0"] },
        { name: "agg_daily_sentiment", role: "aggregation", columns: ["avg_tone"], filters: ["keyword IN ('ECON_BITCOINS', 'ECON_CRYPTOCURRENCY')"] },
      ],
      entity_filters: [{ entity: "Solana", table: "fact_crypto_daily", column: "symbol", value: "SOL" }],
      columns: [{ name: "sentiment", table: "agg_daily_sentiment", column: "avg_tone" }],
      time_filters: [{ text: "ce mois", clause: "date >= DATE_TRUNC('month', CURRENT_DATE)", canonical: true }],
      conditions: ["volume > 0", "keyword IN ('ECON_BITCOINS', 'ECON_CRYPTOCURRENCY')"],
      guidelines: ["Utiliser fact_crypto_daily avec WHERE symbol = :symbol"],
      hash: "cefcb89df45e055f",
    },
  },
  "est ce que l'ethereum et le bitcoin ont évolué de la même manière pendant le premier trimestre 2024": {
    time: 2.03,
    confidence: 0.78,
    extraction: {
      preprocessing: [],
      terms: [
        { text: "Ethereum", category: "Entity", confidence: 1.0, status: "resolved" },
        { text: "Bitcoin", category: "Entity", confidence: 1.0, status: "resolved" },
        { text: "premier trimestre 2024", category: "TimePeriod", confidence: 0.7, status: "plausible_but_new" },
      ],
      unresolved: ["évolué de la même manière"],
    },
    resolver: {
      entities: [
        { name: "Ethereum", type: "crypto", table: "fact_crypto_daily", column: "symbol", value: "ETH" },
        { name: "Bitcoin", type: "crypto", table: "fact_crypto_daily", column: "symbol", value: "BTC" },
      ],
      business_terms: [],
      metrics: [],
      time_periods: [{ name: "premier trimestre 2024", filter: "", canonical: false }],
      analytic_gaps: ["évolué de la même manière"],
      unknown_terms: [],
    },
    rules: {
      predicates: [{ id: "exclude_zero_volume", table: "fact_crypto_daily", condition: "volume > 0" }],
      guidelines: [{ id: "use_parent_table", text: "Utiliser fact_crypto_daily avec WHERE symbol = :symbol" }],
    },
    context: {
      tables: [{ name: "fact_crypto_daily", role: "primary", columns: ["symbol"], filters: ["symbol = 'ETH'", "symbol = 'BTC'", "volume > 0"] }],
      entity_filters: [
        { entity: "Ethereum", table: "fact_crypto_daily", column: "symbol", value: "ETH" },
        { entity: "Bitcoin", table: "fact_crypto_daily", column: "symbol", value: "BTC" },
      ],
      columns: [],
      time_filters: [{ text: "premier trimestre 2024", clause: "", canonical: false }],
      conditions: ["volume > 0"],
      guidelines: ["Utiliser fact_crypto_daily avec WHERE symbol = :symbol"],
      hash: "ec9ae1413074c28b",
    },
  },
  "impact du taux de la Fed sur le prix du Bitcoin": {
    time: 1.89,
    confidence: 0.75,
    extraction: {
      preprocessing: [],
      terms: [
        { text: "Federal Funds Rate", category: "Entity", confidence: 1.0, status: "resolved" },
        { text: "Bitcoin", category: "Entity", confidence: 1.0, status: "resolved" },
        { text: "prix", category: "Metric", confidence: 0.9, status: "resolved" },
      ],
      unresolved: ["impact"],
    },
    resolver: {
      entities: [
        { name: "Federal Funds Rate", type: "macro_indicator", table: "fact_fred_observation", column: "fred_code", value: "FEDFUNDS" },
        { name: "Bitcoin", type: "crypto", table: "fact_crypto_daily", column: "symbol", value: "BTC" },
      ],
      business_terms: [{ name: "prix", table: "fact_crypto_daily", column: "close_usd" }],
      metrics: [],
      time_periods: [],
      analytic_gaps: ["impact"],
      unknown_terms: [],
    },
    rules: {
      predicates: [
        { id: "exclude_zero_volume", table: "fact_crypto_daily", condition: "volume > 0" },
        { id: "valid_fred_values", table: "fact_fred_observation", condition: "value IS NOT NULL" },
      ],
      guidelines: [{ id: "use_parent_table", text: "Utiliser fact_crypto_daily avec WHERE symbol = :symbol" }],
    },
    context: {
      tables: [
        { name: "fact_crypto_daily", role: "primary", columns: ["symbol", "close_usd"], filters: ["symbol = 'BTC'", "volume > 0"] },
        { name: "fact_fred_observation", role: "filter", columns: ["fred_code"], filters: ["fred_code = 'FEDFUNDS'", "value IS NOT NULL"] },
      ],
      entity_filters: [
        { entity: "Federal Funds Rate", table: "fact_fred_observation", column: "fred_code", value: "FEDFUNDS" },
        { entity: "Bitcoin", table: "fact_crypto_daily", column: "symbol", value: "BTC" },
      ],
      columns: [{ name: "prix", table: "fact_crypto_daily", column: "close_usd" }],
      time_filters: [],
      conditions: ["volume > 0", "value IS NOT NULL"],
      guidelines: ["Utiliser fact_crypto_daily avec WHERE symbol = :symbol"],
      hash: "cb826be25085fbcb",
    },
  },
  "volatilité 30 jours du Bitcoin": {
    time: 1.51,
    confidence: 0.91,
    extraction: {
      preprocessing: [],
      terms: [
        { text: "volatilite_30j", category: "Metric", confidence: 0.95, status: "resolved" },
        { text: "Bitcoin", category: "Entity", confidence: 1.0, status: "resolved" },
      ],
      unresolved: [],
    },
    resolver: {
      entities: [{ name: "Bitcoin", type: "crypto", table: "fact_crypto_daily", column: "symbol", value: "BTC" }],
      business_terms: [],
      metrics: [{ name: "volatilite_30j", formula: "STDDEV(daily_change_pct) OVER (PARTITION BY symbol ORDER BY date ROWS 29 PRECEDING)", table: "stg_daily_metrics" }],
      time_periods: [],
      analytic_gaps: [],
      unknown_terms: [],
    },
    rules: {
      predicates: [{ id: "exclude_zero_volume", table: "fact_crypto_daily", condition: "volume > 0" }],
      guidelines: [{ id: "use_parent_table", text: "Utiliser fact_crypto_daily avec WHERE symbol = :symbol" }],
    },
    context: {
      tables: [
        { name: "stg_daily_metrics", role: "join", columns: [], filters: [] },
        { name: "fact_crypto_daily", role: "filter", columns: ["symbol"], filters: ["symbol = 'BTC'", "volume > 0"] },
      ],
      entity_filters: [{ entity: "Bitcoin", table: "fact_crypto_daily", column: "symbol", value: "BTC" }],
      columns: [],
      time_filters: [],
      conditions: ["volume > 0"],
      guidelines: ["Utiliser fact_crypto_daily avec WHERE symbol = :symbol"],
      hash: "1f3702d25b4b7ed3",
      metrics: [{ name: "volatilite_30j", formula: "STDDEV(daily_change_pct) OVER (...)", table: "stg_daily_metrics" }],
    },
  },
};

const EXAMPLES = Object.keys(MOCK_RESULTS);

const CAT_CONFIG = {
  BusinessTerm: { color: BRAND.blue, label: "Business Term" },
  Entity: { color: "#34d399", label: "Entity" },
  TimePeriod: { color: BRAND.purple, label: "Time Period" },
  Metric: { color: "#22d3ee", label: "Metric" },
};

const ROLE_COLORS = {
  primary: "#34d399",
  filter: BRAND.olive,
  aggregation: BRAND.blue,
  join: BRAND.purple,
};

const STATUS_ICONS = {
  resolved: "✓",
  plausible_but_new: "◊",
  ambiguous: "?",
  invalid: "✗",
};

function Chip({ text, color, small, mono }) {
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 4,
      padding: small ? "2px 8px" : "4px 12px",
      borderRadius: 14,
      background: color + "18",
      border: `1px solid ${color}40`,
      color,
      fontSize: small ? "0.7rem" : "0.78rem",
      fontFamily: mono ? "'IBM Plex Mono', monospace" : "inherit",
      fontWeight: 500,
      whiteSpace: "nowrap",
    }}>
      {text}
    </span>
  );
}

function StageIndicator({ number, label, active, done }) {
  const bg = done ? BRAND.olive + "30" : active ? BRAND.pink + "20" : "rgba(255,255,255,0.03)";
  const border = done ? BRAND.olive : active ? BRAND.pink : "rgba(255,255,255,0.08)";
  const numColor = done ? BRAND.olive : active ? BRAND.pink : "rgba(255,255,255,0.2)";
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 10, padding: "10px 14px",
      borderRadius: 10, background: bg, border: `1px solid ${border}`,
      transition: "all 0.4s ease",
    }}>
      <span style={{
        width: 28, height: 28, borderRadius: "50%",
        display: "flex", alignItems: "center", justifyContent: "center",
        background: numColor + "25", color: numColor,
        fontSize: "0.8rem", fontWeight: 700,
        fontFamily: "'IBM Plex Mono', monospace",
      }}>
        {done ? "✓" : number}
      </span>
      <span style={{ fontSize: "0.82rem", color: done || active ? "#e2e8f0" : "rgba(255,255,255,0.3)", fontWeight: 500 }}>
        {label}
      </span>
    </div>
  );
}

function PipelineArrow() {
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "center", padding: "2px 0" }}>
      <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
        <path d="M10 4 L10 14 M6 11 L10 15 L14 11" stroke={BRAND.olive} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
      </svg>
    </div>
  );
}

function JsonViewer({ data, maxHeight = 300 }) {
  const json = typeof data === "string" ? data : JSON.stringify(data, null, 2);
  return (
    <pre style={{
      background: "rgba(0,0,0,0.3)", borderRadius: 8, padding: 14,
      fontSize: "0.72rem", lineHeight: 1.5, color: "#94a3b8",
      fontFamily: "'IBM Plex Mono', monospace",
      maxHeight, overflow: "auto", margin: 0,
      border: "1px solid rgba(255,255,255,0.05)",
    }}>
      {json}
    </pre>
  );
}

function SectionTitle({ icon, title, color }) {
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 8, marginBottom: 12, marginTop: 4,
    }}>
      <span style={{ fontSize: "1rem" }}>{icon}</span>
      <span style={{ fontSize: "0.9rem", fontWeight: 600, color, letterSpacing: "-0.01em" }}>{title}</span>
    </div>
  );
}

export default function SemanticLayerDemo() {
  const [query, setQuery] = useState("");
  const [result, setResult] = useState(null);
  const [activeStage, setActiveStage] = useState(-1);
  const [processing, setProcessing] = useState(false);
  const inputRef = useRef(null);

  const runPipeline = (q) => {
    const input = q || query;
    if (!input.trim()) return;

    setProcessing(true);
    setResult(null);
    setActiveStage(0);

    const match = EXAMPLES.find(e => e.toLowerCase() === input.trim().toLowerCase());
    const data = match ? MOCK_RESULTS[match] : MOCK_RESULTS[EXAMPLES[0]];

    const stages = [0, 1, 2, 3, 4];
    stages.forEach((s, i) => {
      setTimeout(() => {
        setActiveStage(s);
        if (s === 4) {
          setTimeout(() => {
            setResult(data);
            setProcessing(false);
            setActiveStage(5);
          }, 300);
        }
      }, i * 400);
    });
  };

  return (
    <div style={{
      minHeight: "100vh",
      background: "#0a0e17",
      color: "#e2e8f0",
      fontFamily: "'Outfit', sans-serif",
    }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800&family=IBM+Plex+Mono:wght@400;500;600&display=swap');
        * { box-sizing: border-box; margin: 0; padding: 0; }
        ::-webkit-scrollbar { width: 4px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.1); border-radius: 4px; }
        input::placeholder { color: rgba(255,255,255,0.25); }
      `}</style>

      {/* Ambient glow */}
      <div style={{
        position: "fixed", top: -200, left: "30%", width: 600, height: 600,
        background: `radial-gradient(circle, ${BRAND.purple}15 0%, transparent 70%)`,
        pointerEvents: "none", zIndex: 0,
      }}/>
      <div style={{
        position: "fixed", bottom: -300, right: "10%", width: 800, height: 800,
        background: `radial-gradient(circle, ${BRAND.blue}10 0%, transparent 70%)`,
        pointerEvents: "none", zIndex: 0,
      }}/>

      <div style={{ position: "relative", zIndex: 1, maxWidth: 1200, margin: "0 auto", padding: "40px 24px" }}>

        {/* Header */}
        <div style={{ marginBottom: 48 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 8 }}>
            <div style={{
              width: 36, height: 36, borderRadius: 8,
              background: `linear-gradient(135deg, ${BRAND.pink}, ${BRAND.purple})`,
              display: "flex", alignItems: "center", justifyContent: "center",
              fontSize: "1.1rem",
            }}>◆</div>
            <h1 style={{
              fontSize: "1.6rem", fontWeight: 700, letterSpacing: "-0.03em",
              background: `linear-gradient(135deg, #e2e8f0, ${BRAND.blue})`,
              WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent",
            }}>
              AI-Powered Data Analyzer
            </h1>
          </div>
          <p style={{ fontSize: "0.88rem", color: "rgba(255,255,255,0.4)", marginLeft: 48, fontWeight: 300 }}>
            Semantic Layer 
          </p>
        </div>

        {/* Input */}
        <div style={{
          display: "flex", gap: 10, marginBottom: 16,
          background: "rgba(255,255,255,0.03)",
          border: "1px solid rgba(255,255,255,0.08)",
          borderRadius: 14, padding: 6,
        }}>
          <input
            ref={inputRef}
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={e => e.key === "Enter" && runPipeline()}
            placeholder="Posez une question en langage naturel..."
            style={{
              flex: 1, background: "transparent", border: "none", outline: "none",
              color: "#e2e8f0", fontSize: "0.95rem", padding: "12px 16px",
              fontFamily: "'Outfit', sans-serif",
            }}
          />
          <button
            onClick={() => runPipeline()}
            disabled={processing}
            style={{
              padding: "10px 24px", borderRadius: 10, border: "none",
              background: processing ? "rgba(255,255,255,0.05)" : `linear-gradient(135deg, ${BRAND.pink}, ${BRAND.purple})`,
              color: "#fff", fontSize: "0.85rem", fontWeight: 600, cursor: processing ? "wait" : "pointer",
              fontFamily: "'Outfit', sans-serif", transition: "all 0.2s",
              opacity: processing ? 0.5 : 1,
            }}
          >
            {processing ? "Analyse..." : "Analyser"}
          </button>
        </div>

        {/* Examples */}
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 40 }}>
          {EXAMPLES.map((ex, i) => (
            <button
              key={i}
              onClick={() => { setQuery(ex); runPipeline(ex); }}
              style={{
                padding: "5px 12px", borderRadius: 8,
                background: "rgba(255,255,255,0.03)",
                border: "1px solid rgba(255,255,255,0.06)",
                color: "rgba(255,255,255,0.45)", fontSize: "0.72rem",
                cursor: "pointer", fontFamily: "'Outfit', sans-serif",
                transition: "all 0.15s",
              }}
              onMouseEnter={e => { e.target.style.borderColor = BRAND.olive + "60"; e.target.style.color = BRAND.olive; }}
              onMouseLeave={e => { e.target.style.borderColor = "rgba(255,255,255,0.06)"; e.target.style.color = "rgba(255,255,255,0.45)"; }}
            >
              {ex.length > 50 ? ex.slice(0, 50) + "…" : ex}
            </button>
          ))}
        </div>

        {/* Pipeline stages indicator */}
        {(processing || result) && (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 8, marginBottom: 32 }}>
            <StageIndicator number="1" label="Extraction LLM" active={activeStage === 1} done={activeStage > 1} />
            <StageIndicator number="2" label="KG Resolver" active={activeStage === 2} done={activeStage > 2} />
            <StageIndicator number="3" label="Règles métier" active={activeStage === 3} done={activeStage > 3} />
            <StageIndicator number="4" label="SemanticContext" active={activeStage === 4} done={activeStage > 4} />
          </div>
        )}

        {/* Results */}
        {result && (
          <div style={{ animation: "fadeIn 0.5s ease" }}>
            <style>{`@keyframes fadeIn { from { opacity:0; transform:translateY(12px); } to { opacity:1; transform:translateY(0); } }`}</style>

            {/* Top bar */}
            <div style={{
              display: "flex", justifyContent: "space-between", alignItems: "center",
              padding: "14px 20px", borderRadius: 12, marginBottom: 24,
              background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.06)",
            }}>
              <div style={{ display: "flex", gap: 16, alignItems: "center" }}>
                <span style={{
                  padding: "4px 14px", borderRadius: 20, fontSize: "0.78rem", fontWeight: 600,
                  background: result.confidence >= 0.8 ? "rgba(16,185,129,0.15)" : "rgba(245,158,11,0.15)",
                  color: result.confidence >= 0.8 ? "#34d399" : "#fbbf24",
                  border: `1px solid ${result.confidence >= 0.8 ? "rgba(16,185,129,0.3)" : "rgba(245,158,11,0.3)"}`,
                }}>
                  {result.confidence >= 0.8 ? "✓ Pipeline complet" : "◊ Clarification possible"}
                </span>
                <span style={{ fontSize: "0.82rem", color: "rgba(255,255,255,0.4)" }}>
                  Confiance <strong style={{ color: "#e2e8f0" }}>{Math.round(result.confidence * 100)}%</strong>
                </span>
              </div>
              <span style={{
                fontSize: "0.75rem", color: "rgba(255,255,255,0.3)",
                fontFamily: "'IBM Plex Mono', monospace",
              }}>
                {result.time.toFixed(2)}s
              </span>
            </div>

            {/* Two column layout */}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20 }}>

              {/* Left: Stages 1-3 */}
              <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>

                {/* Stage 1 */}
                <div style={{
                  borderRadius: 14, padding: 20,
                  background: "rgba(255,255,255,0.02)",
                  border: `1px solid ${BRAND.pink}20`,
                }}>
                  <SectionTitle icon="①" title="Extraction des termes" color={BRAND.pink} />
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 12 }}>
                    {result.extraction.terms.map((t, i) => {
                      const cfg = CAT_CONFIG[t.category] || { color: "#64748b", label: t.category };
                      return (
                        <div key={i} style={{
                          display: "flex", alignItems: "center", gap: 6,
                          padding: "6px 12px", borderRadius: 10,
                          background: cfg.color + "12",
                          border: `1px solid ${cfg.color}30`,
                        }}>
                          <span style={{ color: cfg.color, fontSize: "0.78rem", fontWeight: 500 }}>{t.text}</span>
                          <span style={{
                            fontSize: "0.6rem", color: cfg.color + "90",
                            fontFamily: "'IBM Plex Mono', monospace",
                          }}>
                            {Math.round(t.confidence * 100)}%
                          </span>
                          <Chip text={cfg.label} color={cfg.color} small mono />
                        </div>
                      );
                    })}
                  </div>
                  {result.extraction.unresolved.length > 0 && (
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                      <span style={{ fontSize: "0.75rem", color: "rgba(255,255,255,0.35)", marginRight: 4 }}>Unresolved:</span>
                      {result.extraction.unresolved.map((u, i) => (
                        <Chip key={i} text={u} color="#fbbf24" small />
                      ))}
                    </div>
                  )}
                </div>

                <PipelineArrow />

                {/* Stage 2 */}
                <div style={{
                  borderRadius: 14, padding: 20,
                  background: "rgba(255,255,255,0.02)",
                  border: `1px solid ${BRAND.olive}20`,
                }}>
                  <SectionTitle icon="②" title="KG Resolver" color={BRAND.olive} />

                  {result.resolver.entities.map((e, i) => (
                    <div key={i} style={{
                      padding: "8px 12px", borderRadius: 8, marginBottom: 6,
                      background: "rgba(16,185,129,0.06)", borderLeft: `3px solid #34d399`,
                      fontSize: "0.78rem",
                    }}>
                      <strong>{e.name}</strong>
                      <span style={{ color: "rgba(255,255,255,0.35)", margin: "0 6px" }}>→</span>
                      <code style={{ color: "#34d399", fontFamily: "'IBM Plex Mono', monospace", fontSize: "0.72rem" }}>
                        {e.table}.{e.column} = '{e.value}'
                      </code>
                      <Chip text={e.type} color="rgba(255,255,255,0.25)" small />
                    </div>
                  ))}

                  {result.resolver.business_terms.map((bt, i) => (
                    <div key={i} style={{
                      padding: "8px 12px", borderRadius: 8, marginBottom: 6,
                      background: BRAND.blue + "08", borderLeft: `3px solid ${BRAND.blue}`,
                      fontSize: "0.78rem",
                    }}>
                      <strong>{bt.name}</strong>
                      <span style={{ color: "rgba(255,255,255,0.35)", margin: "0 6px" }}>→</span>
                      <code style={{ color: BRAND.blue, fontFamily: "'IBM Plex Mono', monospace", fontSize: "0.72rem" }}>
                        {bt.table}.{bt.column}
                      </code>
                    </div>
                  ))}

                  {result.resolver.metrics.map((m, i) => (
                    <div key={i} style={{
                      padding: "8px 12px", borderRadius: 8, marginBottom: 6,
                      background: "rgba(34,211,238,0.06)", borderLeft: "3px solid #22d3ee",
                      fontSize: "0.78rem",
                    }}>
                      <strong>{m.name}</strong>
                      <span style={{ color: "rgba(255,255,255,0.35)", margin: "0 6px" }}>→</span>
                      <code style={{ color: "#22d3ee", fontFamily: "'IBM Plex Mono', monospace", fontSize: "0.68rem" }}>
                        {m.formula.length > 60 ? m.formula.slice(0, 60) + "…" : m.formula}
                      </code>
                    </div>
                  ))}

                  {result.resolver.time_periods.map((tp, i) => (
                    <div key={i} style={{
                      padding: "8px 12px", borderRadius: 8, marginBottom: 6,
                      background: BRAND.purple + "08", borderLeft: `3px solid ${BRAND.purple}`,
                      fontSize: "0.78rem",
                    }}>
                      {tp.canonical ? "✓" : "◊"} <strong>{tp.name}</strong>
                      {tp.filter && (
                        <>
                          <span style={{ color: "rgba(255,255,255,0.35)", margin: "0 6px" }}>→</span>
                          <code style={{ color: BRAND.purple, fontFamily: "'IBM Plex Mono', monospace", fontSize: "0.68rem" }}>
                            {tp.filter}
                          </code>
                        </>
                      )}
                    </div>
                  ))}

                  {result.resolver.analytic_gaps.length > 0 && (
                    <div style={{ marginTop: 8, display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center" }}>
                      <span style={{ fontSize: "0.72rem", color: "rgba(255,255,255,0.3)" }}>Analytic gaps:</span>
                      {result.resolver.analytic_gaps.map((g, i) => (
                        <Chip key={i} text={g} color="#fb923c" small />
                      ))}
                    </div>
                  )}
                </div>

                <PipelineArrow />

                {/* Stage 3 */}
                <div style={{
                  borderRadius: 14, padding: 20,
                  background: "rgba(255,255,255,0.02)",
                  border: `1px solid ${BRAND.purple}20`,
                }}>
                  <SectionTitle icon="③" title="Règles métier implicites" color={BRAND.purple} />

                  {result.rules.predicates.map((r, i) => (
                    <div key={i} style={{
                      padding: "8px 12px", borderRadius: 8, marginBottom: 6,
                      background: "rgba(16,185,129,0.06)", borderLeft: "3px solid #34d399",
                      fontSize: "0.75rem", display: "flex", alignItems: "center", gap: 8,
                    }}>
                      <Chip text="WHERE" color="#34d399" small mono />
                      <code style={{ color: "#94a3b8", fontFamily: "'IBM Plex Mono', monospace" }}>
                        {r.condition}
                      </code>
                      <span style={{ color: "rgba(255,255,255,0.2)", fontSize: "0.65rem", marginLeft: "auto" }}>{r.table}</span>
                    </div>
                  ))}

                  {result.rules.guidelines.map((g, i) => (
                    <div key={i} style={{
                      padding: "8px 12px", borderRadius: 8, marginBottom: 6,
                      background: BRAND.purple + "08", borderLeft: `3px solid ${BRAND.purple}`,
                      fontSize: "0.75rem",
                    }}>
                      <Chip text="GUIDE" color={BRAND.purple} small mono />
                      <span style={{ marginLeft: 8, color: "rgba(255,255,255,0.5)" }}>{g.text}</span>
                    </div>
                  ))}
                </div>
              </div>

              {/* Right: Stage 4 — SemanticContext */}
              <div>
                <div style={{
                  borderRadius: 14, padding: 20,
                  background: "rgba(255,255,255,0.02)",
                  border: `1px solid ${BRAND.blue}20`,
                  position: "sticky", top: 20,
                }}>
                  <SectionTitle icon="④" title="SemanticContext JSON" color={BRAND.blue} />

                  {/* Summary cards */}
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 8, marginBottom: 16 }}>
                    {[
                      { n: result.context.tables.length, label: "Tables", color: BRAND.blue },
                      { n: result.context.entity_filters.length, label: "Entities", color: "#34d399" },
                      { n: result.context.conditions.length, label: "Conditions", color: BRAND.pink },
                    ].map((d, i) => (
                      <div key={i} style={{
                        textAlign: "center", padding: "12px 8px", borderRadius: 10,
                        background: d.color + "08", border: `1px solid ${d.color}20`,
                      }}>
                        <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "1.4rem", fontWeight: 700, color: d.color }}>{d.n}</div>
                        <div style={{ fontSize: "0.65rem", color: "rgba(255,255,255,0.35)", marginTop: 2 }}>{d.label}</div>
                      </div>
                    ))}
                  </div>

                  {/* Tables */}
                  <div style={{ marginBottom: 14 }}>
                    <div style={{ fontSize: "0.72rem", fontWeight: 600, color: "rgba(255,255,255,0.5)", marginBottom: 8, textTransform: "uppercase", letterSpacing: "0.05em" }}>Tables</div>
                    {result.context.tables.map((t, i) => (
                      <div key={i} style={{
                        padding: "10px 12px", borderRadius: 8, marginBottom: 6,
                        background: "rgba(0,0,0,0.2)", border: "1px solid rgba(255,255,255,0.04)",
                      }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                          <span style={{
                            padding: "2px 8px", borderRadius: 6, fontSize: "0.6rem", fontWeight: 600,
                            background: (ROLE_COLORS[t.role] || "#64748b") + "20",
                            color: ROLE_COLORS[t.role] || "#64748b",
                            fontFamily: "'IBM Plex Mono', monospace", textTransform: "uppercase",
                          }}>{t.role}</span>
                          <span style={{ fontSize: "0.8rem", fontWeight: 600, color: "#e2e8f0" }}>{t.name}</span>
                        </div>
                        {t.columns.length > 0 && (
                          <div style={{ fontSize: "0.68rem", color: "rgba(255,255,255,0.3)", marginBottom: 2 }}>
                            cols: {t.columns.map(c => <code key={c} style={{ color: BRAND.blue, marginRight: 6, fontFamily: "'IBM Plex Mono', monospace" }}>{c}</code>)}
                          </div>
                        )}
                        {t.filters.length > 0 && (
                          <div style={{ fontSize: "0.68rem", color: "rgba(255,255,255,0.3)" }}>
                            where: <code style={{ color: BRAND.olive, fontFamily: "'IBM Plex Mono', monospace" }}>{t.filters.join(" AND ")}</code>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>

                  {/* Time filters */}
                  {result.context.time_filters?.length > 0 && (
                    <div style={{ marginBottom: 14 }}>
                      <div style={{ fontSize: "0.72rem", fontWeight: 600, color: "rgba(255,255,255,0.5)", marginBottom: 8, textTransform: "uppercase", letterSpacing: "0.05em" }}>Time filters</div>
                      {result.context.time_filters.map((tf, i) => (
                        <div key={i} style={{ fontSize: "0.75rem", padding: "6px 10px", borderRadius: 6, background: "rgba(0,0,0,0.15)", marginBottom: 4 }}>
                          <span style={{ color: tf.canonical ? "#34d399" : "#fbbf24" }}>{tf.canonical ? "✓" : "◊"}</span>
                          <span style={{ marginLeft: 6, color: "#e2e8f0" }}>{tf.text}</span>
                          {tf.clause && (
                            <code style={{ display: "block", marginTop: 2, fontSize: "0.65rem", color: BRAND.purple, fontFamily: "'IBM Plex Mono', monospace" }}>
                              {tf.clause}
                            </code>
                          )}
                        </div>
                      ))}
                    </div>
                  )}

                  {/* Hash */}
                  <div style={{
                    display: "flex", alignItems: "center", gap: 8,
                    padding: "8px 12px", borderRadius: 8,
                    background: "rgba(0,0,0,0.2)", marginBottom: 14,
                  }}>
                    <span style={{ fontSize: "0.68rem", color: "rgba(255,255,255,0.3)" }}>Cache key</span>
                    <code style={{
                      fontSize: "0.72rem", color: BRAND.olive,
                      fontFamily: "'IBM Plex Mono', monospace", fontWeight: 600,
                    }}>{result.context.hash}</code>
                  </div>

                  {/* Full JSON */}
                  <details>
                    <summary style={{
                      fontSize: "0.75rem", color: "rgba(255,255,255,0.35)", cursor: "pointer",
                      marginBottom: 8, userSelect: "none",
                    }}>
                      Voir le JSON complet
                    </summary>
                    <JsonViewer data={result.context} maxHeight={400} />
                  </details>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Empty state */}
        {!result && !processing && (
          <div style={{
            textAlign: "center", padding: "80px 40px",
            color: "rgba(255,255,255,0.15)",
          }}>
            <div style={{ fontSize: "2.5rem", marginBottom: 12 }}>◆</div>
            <div style={{ fontSize: "0.9rem", fontWeight: 300 }}>
              Sélectionnez un exemple ou posez une question pour voir le pipeline Semantic Layer
            </div>
          </div>
        )}

        {/* Footer */}
        <div style={{
          marginTop: 60, paddingTop: 20,
          borderTop: "1px solid rgba(255,255,255,0.04)",
          display: "flex", justifyContent: "space-between", alignItems: "center",
        }}>
          <span style={{ fontSize: "0.7rem", color: "rgba(255,255,255,0.15)" }}>
            AI-Powered Data Analyzer — Sprint 1
          </span>
          <div style={{ display: "flex", gap: 12 }}>
            {[BRAND.olive, BRAND.blue, BRAND.pink, BRAND.purple].map((c, i) => (
              <div key={i} style={{ width: 8, height: 8, borderRadius: "50%", background: c, opacity: 0.5 }} />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

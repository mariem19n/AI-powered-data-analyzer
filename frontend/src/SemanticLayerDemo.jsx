import { useState, useRef, useEffect } from "react";

// ─── Design tokens ─────────────────────────────────────────────
const C = {
  olive:   "#8d9323",
  blue:    "#5580b9",
  pink:    "#e14480",
  purple:  "#6c367e",
  bg:      "#f5f4f0",
  surface: "#ffffff",
  border:  "#e5e2db",
  text:    "#18181b",
  muted:   "#71717a",
  subtle:  "#f4f3ef",
};

// ─── Static data ───────────────────────────────────────────────

const DATA_SOURCES = [
  {
    name: "Alpha Vantage",
    icon: "📈",
    status: "live",
    badge: "cryptos · OHLCV",
    detail: "Open, High, Low, Close, Volume — quotidien",
    color: C.blue,
  },
  {
    name: "CoinGecko",
    icon: "🦎",
    status: "live",
    badge: "cryptos · Market cap",
    detail: "Capitalisation boursière & volume",

    color: C.olive,
  },
  {
    name: "FRED",
    icon: "🏛️",
    status: "live",
    badge: "indicateurs macro",
    detail: "Fed Rate, CPI, GDP, M2, Unemployment",
    freshness: "Il y a 3j",
    records: "8 200+",
    color: C.purple,
  },
  {
    name: "GDELT",
    icon: "🌐",
    status: "delayed",
    badge: "Sentiment médiatique",
    detail: "Analyse des médias mondiaux en temps quasi-réel",
    color: C.pink,
  },
];

const EXAMPLES_BY_THEME = [
  {
    theme: "Analyse de prix",
    icon: "📊",
    color: C.blue,
    examples: [
      "Montre le prix du Bitcoin ce mois",
      "Performance de l'Ethereum sur 90 jours",
      "Volatilité 30 jours du Bitcoin",
    ],
  },
  {
    theme: "Sentiment médiatique",
    icon: "📰",
    color: C.pink,
    examples: [
      "Quel est le sentiment autour de Solana ce mois",
      "Couverture médiatique du Bitcoin en 2024",
    ],
  },
  {
    theme: "Indicateurs macro",
    icon: "🏛️",
    color: C.purple,
    examples: [
      "Impact du taux de la Fed sur le Bitcoin",
      "Évolution du CPI et prix des cryptos",
    ],
  },
  {
    theme: "Comparaisons",
    icon: "⚖️",
    color: C.olive,
    examples: [
      "Bitcoin vs Ethereum au premier trimestre 2024",
      "Compare les 3 plus grandes cryptos ce mois",
    ],
  },
];

// ─── Mock responses ────────────────────────────────────────────

const ENTITY_COLORS = {
  BTC:      "#f7931a",
  ETH:      "#627eea",
  SOL:      "#9945ff",
  BNB:      "#f0b90b",
  ADA:      "#0033ad",
  FEDFUNDS: C.purple,
  CPI:      C.blue,
  GDP:      C.olive,
};

const MOCK = {
  "montre le prix du bitcoin ce mois": {
    summary: "Prix de clôture du Bitcoin pour le mois en cours",
    entities: [{ name: "Bitcoin", code: "BTC", type: "Crypto" }],
    timeframe: "Ce mois · avril 2026",
    tables: [
      { name: "fact_crypto_daily", role: "PRIMARY", source: "Alpha Vantage", desc: "OHLCV quotidien" },
    ],
    rules: [
      { label: "volume > 0",           tip: "Exclut les journées sans activité de marché pour garantir des données fiables" },
      { label: "WHERE symbol = 'BTC'", tip: "Filtre automatique — entité Bitcoin résolue via le Knowledge Graph Neo4j" },
    ],
    confidence: 0.88,
  },
  "performance de l'ethereum sur 90 jours": {
    summary: "Évolution du prix de l'Ethereum sur les 90 derniers jours",
    entities: [{ name: "Ethereum", code: "ETH", type: "Crypto" }],
    timeframe: "90 derniers jours",
    tables: [
      { name: "fact_crypto_daily", role: "PRIMARY", source: "Alpha Vantage", desc: "OHLCV quotidien" },
    ],
    rules: [
      { label: "volume > 0",           tip: "Exclut les journées sans activité de marché" },
      { label: "WHERE symbol = 'ETH'", tip: "Filtre automatique — entité Ethereum résolue via le KG" },
    ],
    confidence: 0.91,
  },
  "volatilité 30 jours du bitcoin": {
    summary: "Volatilité glissante sur 30 jours du Bitcoin — métrique calculée",
    entities: [{ name: "Bitcoin", code: "BTC", type: "Crypto" }],
    timeframe: "Fenêtre glissante · 30 jours",
    tables: [
      { name: "stg_daily_metrics", role: "CALCUL",  source: "Interne",       desc: "Métriques dérivées" },
      { name: "fact_crypto_daily", role: "SOURCE",  source: "Alpha Vantage", desc: "Données brutes OHLCV" },
    ],
    metric: "STDDEV(daily_change_pct) OVER (PARTITION BY symbol ORDER BY date ROWS 29 PRECEDING)",
    rules: [
      { label: "volume > 0",        tip: "Exclut les journées sans activité de marché" },
      { label: "ROWS 29 PRECEDING", tip: "Fenêtre SQL de 30 jours glissants pour le calcul statistique de volatilité" },
    ],
    confidence: 0.91,
  },
  "quel est le sentiment autour de solana ce mois": {
    summary: "Score de sentiment médiatique autour de Solana — mois en cours",
    entities: [{ name: "Solana", code: "SOL", type: "Crypto" }],
    timeframe: "Ce mois · avril 2026",
    tables: [
      { name: "agg_daily_sentiment", role: "PRIMARY",   source: "GDELT",         desc: "Sentiment agrégé par jour" },
      { name: "fact_crypto_daily",   role: "RÉFÉRENCE", source: "Alpha Vantage", desc: "Corrélation avec le prix" },
    ],
    rules: [
      { label: "keyword = 'ECON_CRYPTOCURRENCY'", tip: "Filtre GDELT — catégorie thématique des articles liés aux cryptomonnaies" },
      { label: "volume > 0",                      tip: "Exclut les journées sans activité de marché" },
    ],
    confidence: 0.92,
  },
  "impact du taux de la fed sur le bitcoin": {
    summary: "Corrélation entre le taux directeur de la Fed (FEDFUNDS) et le prix du Bitcoin",
    entities: [
      { name: "Bitcoin",            code: "BTC",      type: "Crypto"          },
      { name: "Federal Funds Rate", code: "FEDFUNDS", type: "Indicateur macro" },
    ],
    timeframe: "Historique complet",
    tables: [
      { name: "fact_crypto_daily",     role: "PRIMARY",  source: "Alpha Vantage", desc: "Prix Bitcoin quotidien" },
      { name: "fact_fred_observation", role: "JOINTURE", source: "FRED",          desc: "Taux directeur Fed" },
    ],
    rules: [
      { label: "value IS NOT NULL", tip: "Filtre FRED — exclut les périodes sans publication officielle du taux" },
      { label: "volume > 0",        tip: "Exclut les journées sans activité de marché crypto" },
    ],
    analytic_gap: "La notion d'\"impact\" implique une analyse de corrélation — ce signal analytique est transmis au SQL Agent pour une requête adaptée.",
    confidence: 0.75,
  },
  "bitcoin vs ethereum au premier trimestre 2024": {
    summary: "Comparaison de la performance Bitcoin et Ethereum sur le T1 2024",
    entities: [
      { name: "Bitcoin",  code: "BTC", type: "Crypto" },
      { name: "Ethereum", code: "ETH", type: "Crypto" },
    ],
    timeframe: "T1 2024 · 1 jan → 31 mars 2024",
    tables: [
      { name: "fact_crypto_daily", role: "PRIMARY", source: "Alpha Vantage", desc: "OHLCV BTC + ETH" },
    ],
    rules: [
      { label: "symbol IN ('BTC','ETH')", tip: "Filtre multi-entités — les deux symboles résolus simultanément via le KG" },
      { label: "volume > 0",              tip: "Exclut les journées sans activité de marché" },
    ],
    confidence: 0.84,
  },
  "couverture médiatique du bitcoin en 2024": {
    summary: "Analyse de la couverture médiatique mondiale du Bitcoin sur l'année 2024",
    entities: [{ name: "Bitcoin", code: "BTC", type: "Crypto" }],
    timeframe: "Année 2024 · 1 jan → 31 déc 2024",
    tables: [
      { name: "agg_daily_sentiment", role: "PRIMARY", source: "GDELT", desc: "Sentiment + volume médiatique" },
    ],
    rules: [
      { label: "keyword = 'ECON_BITCOINS'",        tip: "Filtre GDELT spécifique Bitcoin — tag thématique des articles" },
      { label: "date BETWEEN '2024-01-01' AND …",  tip: "Filtre temporel résolu automatiquement pour l'année 2024" },
    ],
    confidence: 0.89,
  },
};

function findMock(input) {
  const n = input.toLowerCase().trim()
    .replace(/['']/g, "'")
    .replace(/é|è|ê/g, "e")
    .replace(/à/g, "a");
  for (const key of Object.keys(MOCK)) {
    const k = key.replace(/['']/g, "'");
    if (n === k || n.includes(k.split(" ").slice(0, 4).join(" "))) return MOCK[key];
  }
  for (const key of Object.keys(MOCK)) {
    const words = key.split(" ").filter(w => w.length > 3);
    if (words.filter(w => n.includes(w)).length >= 2) return MOCK[key];
  }
  return MOCK["montre le prix du bitcoin ce mois"];
}

// ─── Atoms ─────────────────────────────────────────────────────

function Tip({ text, children }) {
  const [show, setShow] = useState(false);
  return (
    <span style={{ position: "relative", display: "inline-flex" }}
      onMouseEnter={() => setShow(true)}
      onMouseLeave={() => setShow(false)}
    >
      {children}
      {show && (
        <span style={{
          position: "absolute", bottom: "calc(100% + 7px)", left: "50%",
          transform: "translateX(-50%)",
          background: "#18181b", color: "#fff",
          padding: "6px 11px", borderRadius: 7, fontSize: "0.7rem",
          whiteSpace: "nowrap", zIndex: 999, lineHeight: 1.5,
          boxShadow: "0 4px 16px rgba(0,0,0,0.25)", pointerEvents: "none",
          maxWidth: 280, whiteSpace: "normal", textAlign: "center",
        }}>
          {text}
          <span style={{
            position: "absolute", top: "100%", left: "50%", transform: "translateX(-50%)",
            borderLeft: "5px solid transparent", borderRight: "5px solid transparent",
            borderTop: "5px solid #18181b",
          }} />
        </span>
      )}
    </span>
  );
}

function ConfBar({ value }) {
  const pct = Math.round(value * 100);
  const color = pct >= 85 ? "#22c55e" : pct >= 70 ? C.olive : C.pink;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
      <div style={{ flex: 1, height: 6, background: C.subtle, borderRadius: 3, overflow: "hidden" }}>
        <div style={{
          width: `${pct}%`, height: "100%", borderRadius: 3,
          background: `linear-gradient(90deg, ${color}80, ${color})`,
          transition: "width 0.9s ease",
        }} />
      </div>
      <span style={{ fontSize: "0.8rem", fontWeight: 800, color, minWidth: 36 }}>{pct}%</span>
    </div>
  );
}

function EntityChip({ entity }) {
  const color = ENTITY_COLORS[entity.code] || C.blue;
  return (
    <div style={{
      display: "inline-flex", alignItems: "center", gap: 7,
      padding: "6px 12px 6px 6px", borderRadius: 24,
      background: color + "10", border: `1.5px solid ${color}30`,
    }}>
      <div style={{
        width: 24, height: 24, borderRadius: "50%",
        background: color,
        display: "flex", alignItems: "center", justifyContent: "center",
        fontSize: "0.6rem", fontWeight: 800, color: "#fff", flexShrink: 0,
      }}>{entity.code.slice(0, 2)}</div>
      <span style={{ fontWeight: 700, fontSize: "0.83rem", color: C.text }}>{entity.name}</span>
      <span style={{
        fontSize: "0.67rem", color, fontWeight: 700,
        background: color + "15", padding: "1px 6px", borderRadius: 10,
        border: `1px solid ${color}30`,
      }}>{entity.code}</span>
      <span style={{ fontSize: "0.66rem", color: C.muted }}>{entity.type}</span>
    </div>
  );
}

function RuleChip({ rule }) {
  return (
    <Tip text={rule.tip}>
      <div style={{
        display: "inline-flex", alignItems: "center", gap: 5,
        padding: "5px 10px", borderRadius: 20, cursor: "default",
        background: C.olive + "0e", border: `1px solid ${C.olive}30`,
        transition: "background 0.15s",
      }}>
        <span style={{ color: C.olive, fontSize: "0.72rem", fontWeight: 700 }}>✓</span>
        <span style={{ fontSize: "0.73rem", color: C.text, fontFamily: "'IBM Plex Mono', monospace" }}>
          {rule.label}
        </span>
        <span style={{ fontSize: "0.63rem", color: C.muted, marginLeft: 1 }}>ⓘ</span>
      </div>
    </Tip>
  );
}

function TableRow({ table }) {
  const ROLE_COLOR = {
    PRIMARY:   "#22c55e",
    JOINTURE:  C.blue,
    CALCUL:    C.pink,
    SOURCE:    C.olive,
    RÉFÉRENCE: C.purple,
  };
  const color = ROLE_COLOR[table.role] || C.muted;
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 8, padding: "8px 12px",
      borderRadius: 9, background: color + "07", border: `1px solid ${color}20`,
      marginBottom: 5,
    }}>
      <span style={{
        fontSize: "0.59rem", fontWeight: 800, color,
        background: color + "18", border: `1px solid ${color}30`,
        padding: "1px 6px", borderRadius: 4, letterSpacing: "0.04em", flexShrink: 0,
      }}>{table.role}</span>
      <span style={{
        fontFamily: "'IBM Plex Mono', monospace",
        fontSize: "0.78rem", fontWeight: 600, color: C.text, flex: 1,
      }}>{table.name}</span>
      <span style={{ fontSize: "0.68rem", color: C.muted, whiteSpace: "nowrap" }}>
        {table.source} · {table.desc}
      </span>
    </div>
  );
}

// ─── Stage progress (during processing) ───────────────────────

function StageProgress({ stage }) {
  const stages = [
    { label: "Extraction des entités",       color: C.blue   },
    { label: "Résolution Knowledge Graph",   color: C.purple },
    { label: "Application des règles métier",color: C.olive  },
    { label: "Construction du SemanticContext", color: C.pink },
  ];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
      {stages.map((s, i) => {
        const done   = stage > i;
        const active = stage === i;
        return (
          <div key={i} style={{ display: "flex", alignItems: "center", gap: 9, opacity: done || active ? 1 : 0.32 }}>
            <div style={{
              width: 20, height: 20, borderRadius: "50%", flexShrink: 0,
              background: done ? s.color : active ? s.color + "25" : C.subtle,
              border: `1.5px solid ${done || active ? s.color : C.border}`,
              display: "flex", alignItems: "center", justifyContent: "center",
              fontSize: "0.6rem", color: done ? "#fff" : s.color,
              transition: "all 0.3s",
            }}>
              {done ? "✓" : i + 1}
            </div>
            <span style={{ fontSize: "0.75rem", color: done || active ? C.text : C.muted, fontWeight: done || active ? 600 : 400 }}>
              {s.label}
            </span>
            {active && (
              <span style={{ marginLeft: "auto", display: "flex", gap: 3 }}>
                {[0,1,2].map(j => (
                  <span key={j} style={{
                    width: 5, height: 5, borderRadius: "50%", background: s.color,
                    display: "inline-block",
                    animation: `dot 1.2s ease ${j * 0.2}s infinite`,
                  }} />
                ))}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ─── Chat messages ─────────────────────────────────────────────

function UserBubble({ text }) {
  return (
    <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 6 }}>
      <div style={{
        maxWidth: "72%", padding: "11px 17px",
        background: `linear-gradient(135deg, ${C.pink}, ${C.purple})`,
        borderRadius: "16px 16px 2px 16px",
        color: "#fff", fontSize: "0.9rem", lineHeight: 1.55, fontWeight: 500,
        boxShadow: `0 2px 10px ${C.pink}40`,
      }}>{text}</div>
    </div>
  );
}

function UnderstandingCard({ data }) {
  return (
    <div style={{ display: "flex", gap: 10, marginBottom: 20, animation: "fadeUp 0.4s ease" }}>
      {/* Bot avatar */}
      <div style={{
        width: 34, height: 34, borderRadius: "50%", flexShrink: 0, marginTop: 2,
        background: `linear-gradient(135deg, ${C.pink}, ${C.purple})`,
        display: "flex", alignItems: "center", justifyContent: "center",
        fontSize: "0.85rem", color: "#fff",
      }}>◆</div>

      {/* Card */}
      <div style={{
        flex: 1, background: C.surface,
        border: `1px solid ${C.border}`,
        borderRadius: "2px 16px 16px 16px",
        boxShadow: "0 2px 10px rgba(0,0,0,0.06)",
        overflow: "hidden",
      }}>
        {/* Card header */}
        <div style={{
          padding: "13px 20px 11px",
          borderBottom: `1px solid ${C.border}`,
          background: `linear-gradient(135deg, ${C.pink}07, ${C.purple}07)`,
          display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12,
        }}>
          <div>
            <div style={{ fontSize: "0.66rem", fontWeight: 700, color: C.pink, textTransform: "uppercase", letterSpacing: "0.07em", marginBottom: 4 }}>
              Ce que j'ai compris
            </div>
            <div style={{ fontSize: "0.93rem", fontWeight: 700, color: C.text, lineHeight: 1.4 }}>
              {data.summary}
            </div>
          </div>
          <div style={{ flexShrink: 0, textAlign: "right" }}>
            <div style={{ fontSize: "0.62rem", color: C.muted, marginBottom: 4, whiteSpace: "nowrap" }}>Confiance du pipeline</div>
            <div style={{ width: 130 }}>
              <ConfBar value={data.confidence} />
            </div>
          </div>
        </div>

        {/* Body */}
        <div style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 16 }}>

          {/* Entities */}
          <div>
            <div style={{ fontSize: "0.64rem", fontWeight: 700, color: C.muted, textTransform: "uppercase", letterSpacing: "0.07em", marginBottom: 9 }}>
              Entités détectées
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 7 }}>
              {data.entities.map((e, i) => <EntityChip key={i} entity={e} />)}
            </div>
          </div>

          {/* Time + Tables in 2 columns */}
          <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "0 28px", alignItems: "start" }}>
            {/* Timeframe */}
            <div>
              <div style={{ fontSize: "0.64rem", fontWeight: 700, color: C.muted, textTransform: "uppercase", letterSpacing: "0.07em", marginBottom: 9 }}>
                Période temporelle
              </div>
              <div style={{
                display: "inline-flex", alignItems: "center", gap: 8,
                padding: "8px 14px", borderRadius: 10,
                background: C.purple + "0c", border: `1.5px solid ${C.purple}28`,
              }}>
                <span style={{ fontSize: "0.9rem" }}>📅</span>
                <span style={{ fontSize: "0.82rem", fontWeight: 700, color: C.purple }}>{data.timeframe}</span>
              </div>
            </div>

            {/* Tables */}
            <div>
              <div style={{ fontSize: "0.64rem", fontWeight: 700, color: C.muted, textTransform: "uppercase", letterSpacing: "0.07em", marginBottom: 9 }}>
                Tables interrogées
              </div>
              {data.tables.map((t, i) => <TableRow key={i} table={t} />)}
            </div>
          </div>

          {/* Metric formula (optional) */}
          {data.metric && (
            <div>
              <div style={{ fontSize: "0.64rem", fontWeight: 700, color: C.muted, textTransform: "uppercase", letterSpacing: "0.07em", marginBottom: 9 }}>
                Métrique calculée
              </div>
              <div style={{
                padding: "9px 14px", borderRadius: 9,
                background: C.pink + "07", border: `1px solid ${C.pink}22`,
                fontFamily: "'IBM Plex Mono', monospace",
                fontSize: "0.74rem", color: C.text, wordBreak: "break-all",
              }}>
                {data.metric}
              </div>
            </div>
          )}

          {/* Rules */}
          <div>
            <div style={{ fontSize: "0.64rem", fontWeight: 700, color: C.muted, textTransform: "uppercase", letterSpacing: "0.07em", marginBottom: 9 }}>
              Règles métier appliquées automatiquement
              <span style={{ color: C.olive, textTransform: "none", letterSpacing: 0, fontWeight: 500, marginLeft: 4 }}>
                — survolez pour en savoir plus
              </span>
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {data.rules.map((r, i) => <RuleChip key={i} rule={r} />)}
            </div>
          </div>

          {/* Analytic gap warning */}
          {data.analytic_gap && (
            <div style={{
              display: "flex", alignItems: "flex-start", gap: 9,
              padding: "10px 14px", borderRadius: 10,
              background: "#f59e0b0c", border: "1px solid #f59e0b28",
            }}>
              <span style={{ fontSize: "0.9rem", flexShrink: 0 }}>⚠️</span>
              <span style={{ fontSize: "0.78rem", color: C.text, lineHeight: 1.55 }}>{data.analytic_gap}</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ─── Main ──────────────────────────────────────────────────────

export default function SemanticLayerDemo() {
  const [query, setQuery]           = useState("");
  const [messages, setMessages]     = useState([]);
  const [processing, setProcessing] = useState(false);
  const [procStage, setProcStage]   = useState(0);
  const inputRef  = useRef(null);
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, processing]);

  const send = (q) => {
    const text = (q || query).trim();
    if (!text || processing) return;
    setQuery("");
    inputRef.current?.focus();

    setMessages(prev => [...prev, { type: "user", text }]);
    setProcessing(true);
    setProcStage(0);

    [0, 1, 2, 3].forEach(i => {
      setTimeout(() => setProcStage(i), i * 520 + 150);
    });

    setTimeout(() => {
      const data = findMock(text);
      setMessages(prev => [...prev, { type: "response", data }]);
      setProcessing(false);
    }, 2500);
  };

  return (
    <div style={{
      height: "100vh", display: "flex", flexDirection: "column",
      background: C.bg, fontFamily: "'Outfit', 'Inter', sans-serif", color: C.text,
    }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800&family=IBM+Plex+Mono:wght@400;500;600&display=swap');
        * { box-sizing: border-box; }
        body { margin: 0; background: ${C.bg}; }
        #root {
          width: 100% !important; max-width: 100% !important;
          border: none !important; text-align: left !important;
          min-height: 100vh; display: block !important;
          flex-direction: unset !important;
        }
        input, button, textarea { font-family: inherit; }
        input:focus { outline: none; }
        button { cursor: pointer; }
        ::selection { background: ${C.pink}30; }
        ::-webkit-scrollbar { width: 4px; }
        ::-webkit-scrollbar-thumb { background: ${C.border}; border-radius: 4px; }
        @keyframes dot {
          0%, 80%, 100% { transform: translateY(0); opacity: 0.3; }
          40%            { transform: translateY(-4px); opacity: 1; }
        }
        @keyframes fadeUp {
          from { opacity: 0; transform: translateY(10px); }
          to   { opacity: 1; transform: translateY(0); }
        }
      `}</style>

      {/* ══ HEADER ════════════════════════════════════════════ */}
      <header style={{
        background: C.surface, borderBottom: `1px solid ${C.border}`,
        padding: "0 28px", height: 56, flexShrink: 0,
        display: "flex", alignItems: "center", gap: 20,
        boxShadow: "0 1px 4px rgba(0,0,0,0.05)", zIndex: 100,
      }}>
        {/* Logo */}
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{
            width: 30, height: 30, borderRadius: 8, flexShrink: 0,
            background: `linear-gradient(135deg, ${C.pink}, ${C.purple})`,
            display: "flex", alignItems: "center", justifyContent: "center",
            color: "#fff", fontSize: "0.9rem",
          }}>◆</div>
          <div style={{ lineHeight: 1.2 }}>
            <div style={{ fontWeight: 800, fontSize: "0.9rem", letterSpacing: "-0.02em", color: C.text }}>
              AI Data Analyzer
            </div>
            <div style={{ fontSize: "0.61rem", color: C.muted, fontWeight: 500 }}>
              Semantic Layer 
            </div>
          </div>
        </div>

        <div style={{ width: 1, height: 26, background: C.border }} />

        {/* KG Stats */}
        {[
          { value: "10",  label: "cryptos suivies",    color: C.blue   },
          { value: "5",   label: "indicateurs macro",  color: C.purple },
          { value: "47",  label: "termes dans le KG",  color: C.olive  },
          { value: "4",   label: "sources actives",    color: C.pink   },
        ].map((s, i) => (
          <div key={i} style={{ display: "flex", alignItems: "baseline", gap: 5 }}>
            <span style={{ fontWeight: 800, fontSize: "1.05rem", color: s.color, lineHeight: 1 }}>{s.value}</span>
            <span style={{ fontSize: "0.71rem", color: C.muted }}>{s.label}</span>
            {i < 3 && <div style={{ width: 1, height: 14, background: C.border, marginLeft: 10 }} />}
          </div>
        ))}

        <div style={{ flex: 1 }} />

        <span style={{
          fontSize: "0.65rem", fontWeight: 700, color: C.olive,
          background: C.olive + "12", border: `1px solid ${C.olive}30`,
          padding: "3px 11px", borderRadius: 20, letterSpacing: "0.04em",
        }}>
        </span>
      </header>

      {/* ══ DATA SOURCES STRIP ════════════════════════════════ */}
      <div style={{
        background: C.surface, borderBottom: `1px solid ${C.border}`,
        padding: "10px 28px", display: "flex", gap: 10, flexShrink: 0,
      }}>
        {DATA_SOURCES.map((src, i) => (
          <div key={i} style={{
            flex: "1 1 0", minWidth: 0, padding: "10px 14px", borderRadius: 11,
            background: C.subtle, border: `1px solid ${C.border}`,
            display: "flex", alignItems: "center", gap: 10,
          }}>
            <span style={{ fontSize: "1.15rem", flexShrink: 0 }}>{src.icon}</span>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 3 }}>
                <span style={{ fontWeight: 700, fontSize: "0.8rem", color: C.text }}>{src.name}</span>
                <span style={{
                  width: 7, height: 7, borderRadius: "50%", flexShrink: 0,
                  background: src.status === "live" ? "#22c55e" : "#f59e0b",
                  boxShadow: `0 0 0 2px ${src.status === "live" ? "#22c55e30" : "#f59e0b30"}`,
                }} />
              </div>
              <div style={{ fontSize: "0.7rem", color: src.color, fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", marginBottom: 1 }}>
                {src.badge}
              </div>
              <div style={{ fontSize: "0.63rem", color: C.muted }}>
                {src.records} enregistrements · ↻ {src.freshness}
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* ══ BODY ══════════════════════════════════════════════ */}
      <div style={{ flex: 1, display: "flex", minHeight: 0 }}>

        {/* ── Sidebar: examples ── */}
        <aside style={{
          width: 268, flexShrink: 0,
          background: C.surface, borderRight: `1px solid ${C.border}`,
          overflowY: "auto", padding: "16px 14px 20px",
        }}>
          <div style={{ fontSize: "0.65rem", fontWeight: 700, color: C.muted, textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 14, paddingLeft: 2 }}>
            Exemples de questions
          </div>

          {EXAMPLES_BY_THEME.map((group, gi) => (
            <div key={gi} style={{ marginBottom: 18 }}>
              {/* Theme label */}
              <div style={{
                display: "flex", alignItems: "center", gap: 7,
                marginBottom: 7, padding: "5px 8px", borderRadius: 7,
                background: group.color + "0c",
              }}>
                <span style={{ fontSize: "0.85rem" }}>{group.icon}</span>
                <span style={{ fontSize: "0.73rem", fontWeight: 700, color: group.color }}>{group.theme}</span>
              </div>

              {/* Example buttons */}
              <div style={{ display: "flex", flexDirection: "column", gap: 4, paddingLeft: 2 }}>
                {group.examples.map((ex, ei) => (
                  <button key={ei} onClick={() => send(ex)} disabled={processing}
                    style={{
                      textAlign: "left", padding: "8px 11px", borderRadius: 9,
                      background: C.surface, border: `1px solid ${C.border}`,
                      color: C.muted, fontSize: "0.75rem", lineHeight: 1.45,
                      cursor: processing ? "not-allowed" : "pointer",
                      transition: "all 0.15s",
                    }}
                    onMouseEnter={e => {
                      if (!processing) {
                        e.currentTarget.style.borderColor = group.color + "70";
                        e.currentTarget.style.color = C.text;
                        e.currentTarget.style.background = group.color + "07";
                      }
                    }}
                    onMouseLeave={e => {
                      e.currentTarget.style.borderColor = C.border;
                      e.currentTarget.style.color = C.muted;
                      e.currentTarget.style.background = C.surface;
                    }}
                  >
                    {ex}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </aside>

        {/* ── Chat main ── */}
        <main style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0, minHeight: 0 }}>

          {/* Messages */}
          <div style={{ flex: 1, overflowY: "auto", padding: "24px 32px 8px" }}>

            {/* Empty state */}
            {messages.length === 0 && !processing && (
              <div style={{
                display: "flex", flexDirection: "column", alignItems: "center",
                justifyContent: "center", height: "100%", textAlign: "center",
                gap: 14, color: C.muted, padding: "40px 0",
              }}>
                <div style={{
                  width: 60, height: 60, borderRadius: 18,
                  background: `linear-gradient(135deg, ${C.pink}18, ${C.purple}18)`,
                  border: `2px solid ${C.pink}25`,
                  display: "flex", alignItems: "center", justifyContent: "center",
                  fontSize: "1.6rem",
                }}>◆</div>
                <div>
                  <div style={{ fontWeight: 800, fontSize: "1.15rem", color: C.text, marginBottom: 8 }}>
                    Posez votre première question
                  </div>
                  <div style={{ fontSize: "0.86rem", maxWidth: 400, lineHeight: 1.65, color: C.muted }}>
                    Le système comprend le langage naturel et vous montre exactement
                    ce qu'il a compris — entités, sources, règles appliquées.
                  </div>
                </div>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", justifyContent: "center", maxWidth: 500 }}>
                  {["Montre le prix du Bitcoin ce mois", "Quel est le sentiment autour de Solana"].map((ex, i) => (
                    <button key={i} onClick={() => send(ex)}
                      style={{
                        padding: "6px 14px", borderRadius: 20,
                        background: C.surface, border: `1px solid ${C.border}`,
                        color: C.muted, fontSize: "0.76rem", cursor: "pointer",
                        transition: "all 0.15s",
                      }}
                      onMouseEnter={e => { e.currentTarget.style.borderColor = C.pink + "60"; e.currentTarget.style.color = C.pink; }}
                      onMouseLeave={e => { e.currentTarget.style.borderColor = C.border; e.currentTarget.style.color = C.muted; }}
                    >
                      {ex}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Message list */}
            {messages.map((msg, i) => (
              <div key={i} style={{ animation: "fadeUp 0.35s ease" }}>
                {msg.type === "user"     && <UserBubble text={msg.text} />}
                {msg.type === "response" && <UnderstandingCard data={msg.data} />}
              </div>
            ))}

            {/* Processing animation */}
            {processing && (
              <div style={{ display: "flex", gap: 10, animation: "fadeUp 0.3s ease", marginBottom: 12 }}>
                <div style={{
                  width: 34, height: 34, borderRadius: "50%", flexShrink: 0, marginTop: 2,
                  background: `linear-gradient(135deg, ${C.pink}, ${C.purple})`,
                  display: "flex", alignItems: "center", justifyContent: "center",
                  fontSize: "0.85rem", color: "#fff",
                }}>◆</div>
                <div style={{
                  background: C.surface, border: `1px solid ${C.border}`,
                  borderRadius: "2px 16px 16px 16px",
                  boxShadow: "0 2px 8px rgba(0,0,0,0.06)",
                  padding: "14px 20px", minWidth: 260,
                }}>
                  <div style={{ fontSize: "0.66rem", fontWeight: 700, color: C.muted, textTransform: "uppercase", letterSpacing: "0.07em", marginBottom: 12 }}>
                    Analyse en cours…
                  </div>
                  <StageProgress stage={procStage} />
                </div>
              </div>
            )}

            <div ref={bottomRef} />
          </div>

          {/* ── Input bar ── */}
          <div style={{
            padding: "10px 28px 14px",
            background: C.surface, borderTop: `1px solid ${C.border}`, flexShrink: 0,
          }}>
            <div style={{
              display: "flex", gap: 8, alignItems: "center",
              background: C.subtle, border: `2px solid ${C.border}`,
              borderRadius: 14, padding: "5px 5px 5px 18px",
              transition: "border-color 0.2s",
            }}
              onFocusCapture={e => { e.currentTarget.style.borderColor = C.pink + "90"; }}
              onBlurCapture={e => { e.currentTarget.style.borderColor = C.border; }}
            >
              <input
                ref={inputRef}
                value={query}
                onChange={e => setQuery(e.target.value)}
                onKeyDown={e => e.key === "Enter" && send()}
                disabled={processing}
                placeholder="Posez votre question en langage naturel..."
                style={{
                  flex: 1, background: "transparent", border: "none",
                  color: C.text, fontSize: "0.92rem",
                }}
              />
              <button onClick={() => send()} disabled={processing || !query.trim()}
                style={{
                  padding: "9px 22px", borderRadius: 10, border: "none",
                  background: processing || !query.trim()
                    ? C.border
                    : `linear-gradient(135deg, ${C.pink}, ${C.purple})`,
                  color: processing || !query.trim() ? C.muted : "#fff",
                  fontSize: "0.83rem", fontWeight: 700, transition: "all 0.2s",
                  boxShadow: processing || !query.trim() ? "none" : `0 2px 10px ${C.pink}45`,
                }}
              >
                {processing ? "…" : "Analyser →"}
              </button>
            </div>
            <div style={{ fontSize: "0.64rem", color: C.muted, marginTop: 7, textAlign: "center" }}>
              Langage naturel · Français ou anglais · Knowledge Graph Neo4j · LLM Claude
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}

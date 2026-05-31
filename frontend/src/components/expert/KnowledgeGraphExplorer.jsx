import { useEffect, useState } from "react";
import { fetchKgStats } from "../../services/expertApi.js";

const IMPORTANT_LABELS = ["BusinessTerm", "Metric", "Entity", "Insight", "Anomaly", "SQLQuery", "Feedback", "Correction"];

export default function KnowledgeGraphExplorer() {
  const [data, setData] = useState(null);
  const [status, setStatus] = useState("loading");

  useEffect(() => {
    fetchKgStats()
      .then((payload) => {
        setData(payload);
        setStatus("ready");
      })
      .catch(() => setStatus("error"));
  }, []);

  return (
    <section className="expert-panel">
      <div className="expert-section-header compact">
        <div>
          <span>KG Explorer</span>
          <h2>Knowledge Graph</h2>
        </div>
        {data && <em className={`expert-status ${data.available ? "accepted" : "rejected"}`}>{data.available ? "healthy" : "unavailable"}</em>}
      </div>
      {status === "loading" && <p className="expert-empty">Chargement...</p>}
      {status === "error" && <p className="expert-error">KG stats indisponibles.</p>}
      {data && !data.available && <p className="expert-empty">{data.message || "KG unavailable"}</p>}
      {data?.available && (
        <>
          <div className="kg-count-grid">
            {IMPORTANT_LABELS.map((label) => (
              <article key={label}><span>{label}</span><strong>{data.node_counts?.[label] ?? 0}</strong></article>
            ))}
          </div>
          <div className="expert-mini-list">
            <h3>Low confidence terms</h3>
            {(data.low_confidence_terms || []).length === 0 ? <p>No low confidence terms.</p> : null}
            {(data.low_confidence_terms || []).slice(0, 5).map((item, index) => (
              <div key={index}><span>{item.labels?.join(", ")}</span><button disabled>Promote</button><button disabled>Delete</button></div>
            ))}
          </div>
          <div className="expert-mini-list">
            <h3>Plausible but new</h3>
            {(data.plausible_but_new || []).length === 0 ? <p>No plausible-but-new nodes.</p> : null}
            {(data.plausible_but_new || []).slice(0, 5).map((item, index) => (
              <div key={index}>
                <span>{item.name || item.id || item.labels?.join(", ") || "Node"}</span>
                <button disabled>Promote</button>
                <button disabled>Delete</button>
              </div>
            ))}
          </div>
        </>
      )}
    </section>
  );
}

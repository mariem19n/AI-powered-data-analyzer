import { useEffect, useState } from "react";
import { fetchPromptPerformance } from "../../services/expertApi.js";

export default function PromptPerformance() {
  const [items, setItems] = useState([]);
  const [status, setStatus] = useState("loading");

  useEffect(() => {
    fetchPromptPerformance()
      .then((payload) => {
        setItems(payload.items || []);
        setStatus("ready");
      })
      .catch(() => setStatus("error"));
  }, []);

  return (
    <section className="expert-panel">
      <div className="expert-section-header compact">
        <div>
          <span>Prompt Performance</span>
          <h2>Métriques LLM</h2>
        </div>
      </div>
      {status === "loading" && <p className="expert-empty">Chargement...</p>}
      {status === "error" && <p className="expert-error">Erreur de chargement.</p>}
      {status === "ready" && items.length === 0 && <p className="expert-empty">Aucune trace LLM historisée pour le moment.</p>}
      {items.length > 0 && (
        <table className="expert-table">
          <thead><tr><th>Purpose</th><th>Calls</th><th>Latency</th><th>Tokens</th><th>Fallback</th></tr></thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.purpose}>
                <td>{item.purpose}</td><td>{item.calls}</td><td>{item.avg_latency_ms} ms</td><td>{item.avg_tokens}</td><td>{Math.round((item.fallback_rate || 0) * 100)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

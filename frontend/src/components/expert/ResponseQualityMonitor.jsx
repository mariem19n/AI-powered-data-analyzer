import { useEffect, useState } from "react";
import { fetchResponseQuality } from "../../services/expertApi.js";

export default function ResponseQualityMonitor() {
  const [items, setItems] = useState([]);
  const [selected, setSelected] = useState(null);
  const [status, setStatus] = useState("loading");

  useEffect(() => {
    fetchResponseQuality(20)
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
          <span>Response Quality</span>
          <h2>Dernières réponses</h2>
        </div>
      </div>
      {status === "loading" && <p className="expert-empty">Chargement...</p>}
      {status === "error" && <p className="expert-error">Erreur de chargement.</p>}
      {status === "ready" && items.length === 0 && <p className="expert-empty">Aucune réponse historisée.</p>}
      {items.length > 0 && (
        <div className="expert-table-wrap">
          <table className="expert-table">
            <thead>
              <tr><th>Date</th><th>Question</th><th>Intent</th><th>LLM</th><th>Warnings</th><th>Rating</th><th>Status</th></tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.message_id} onClick={() => setSelected(item)}>
                  <td>{item.created_at?.slice(0, 10)}</td>
                  <td>{item.question || "-"}</td>
                  <td>{item.intent || "-"}</td>
                  <td>{item.llm_calls ?? "-"}</td>
                  <td>{item.warnings_count ?? 0}</td>
                  <td>{item.user_rating ?? "-"}</td>
                  <td>{item.validation_status || "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {selected && (
        <details className="expert-details" open>
          <summary>Détail réponse</summary>
          <pre className="expert-json-block">{JSON.stringify(selected.detail, null, 2)}</pre>
        </details>
      )}
    </section>
  );
}

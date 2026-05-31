import { useEffect, useState } from "react";
import { fetchSemanticHealth } from "../../services/expertApi.js";

export default function SemanticLayerHealth() {
  const [items, setItems] = useState([]);
  const [status, setStatus] = useState("loading");

  useEffect(() => {
    fetchSemanticHealth()
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
          <span>Semantic Health</span>
          <h2>Gaps fréquents</h2>
        </div>
      </div>
      {status === "loading" && <p className="expert-empty">Chargement...</p>}
      {status === "error" && <p className="expert-error">Erreur de chargement.</p>}
      {status === "ready" && items.length === 0 && <p className="expert-empty">No semantic gaps recorded yet.</p>}
      {items.length > 0 && (
        <table className="expert-table">
          <thead><tr><th>Term / Gap</th><th>Type</th><th>Count</th><th>Last seen</th></tr></thead>
          <tbody>
            {items.map((item) => (
              <tr key={`${item.type}-${item.term}`}>
                <td>{item.term}</td><td>{item.type}</td><td>{item.count}</td><td>{item.last_seen?.slice(0, 10) || "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

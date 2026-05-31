import { useEffect, useState } from "react";
import { fetchDataFreshness } from "../../services/expertApi.js";

export default function DataFreshnessMonitor() {
  const [items, setItems] = useState([]);
  const [status, setStatus] = useState("loading");

  useEffect(() => {
    fetchDataFreshness()
      .then((payload) => {
        setItems(payload.items || []);
        setStatus("ready");
      })
      .catch(() => setStatus("error"));
  }, []);

  return (
    <section className="expert-panel data-freshness-panel">
      <div className="expert-section-header compact">
        <div>
          <span>Data Freshness Monitor</span>
          <h2>Fraîcheur des pipelines</h2>
        </div>
      </div>
      {status === "loading" && <p className="expert-empty">Chargement...</p>}
      {status === "error" && <p className="expert-error">Erreur de chargement.</p>}
      {items.length > 0 && (
        <table className="expert-table">
          <thead><tr><th>Source</th><th>Table</th><th>Last update</th><th>Age</th><th>Status</th><th>Records</th></tr></thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.table_name}>
                <td>{item.source_name}</td>
                <td>{item.table_name}</td>
                <td>{item.latest_date || "-"}</td>
                <td>{item.age_days == null ? "-" : `${item.age_days}d`}</td>
                <td><span className={`expert-status ${item.status}`}>{item.status}</span></td>
                <td>{item.record_count ?? "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

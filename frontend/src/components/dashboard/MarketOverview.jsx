import { useEffect, useMemo, useState } from "react";
import CryptoCard from "./CryptoCard.jsx";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

const FALLBACK_ASSETS = [
  { symbol: "BTC", name: "Bitcoin", price_usd: 78605.54, change_24h_pct: 1.8, volume: 27800000000, sparkline: [72, 74, 73, 76, 79, 78, 80] },
  { symbol: "ETH", name: "Ethereum", price_usd: 3850.2, change_24h_pct: -0.6, volume: 15200000000, sparkline: [41, 42, 40, 39, 41, 40, 39] },
  { symbol: "SOL", name: "Solana", price_usd: 168.42, change_24h_pct: 2.4, volume: 3900000000, sparkline: [22, 23, 23, 25, 26, 27, 28] },
  { symbol: "BNB", name: "BNB", price_usd: 612.1, change_24h_pct: 0.3, volume: 1800000000, sparkline: [32, 32, 33, 33, 34, 34, 35] },
  { symbol: "XRP", name: "XRP", price_usd: 0.62, change_24h_pct: -1.1, volume: 1100000000, sparkline: [17, 18, 17, 16, 17, 16, 15] },
  { symbol: "ADA", name: "Cardano", price_usd: 0.49, change_24h_pct: 0.9, volume: 680000000, sparkline: [12, 13, 13, 14, 13, 14, 15] },
];

const WATCHLIST = new Set(["BTC", "ETH", "SOL"]);

function cryptoName(asset) {
  return asset.name || asset.symbol;
}

export default function MarketOverview({ onOpenChatQuestion }) {
  const [assets, setAssets] = useState(FALLBACK_ASSETS);
  const [filter, setFilter] = useState("all");
  const [status, setStatus] = useState("fallback");

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const response = await fetch(`${API_BASE_URL}/api/crypto/latest-prices`, {
          signal: controller.signal,
        });
        if (!response.ok) throw new Error("prices unavailable");
        const payload = await response.json();
        if (Array.isArray(payload) && payload.length) {
          setAssets(payload.map((item, index) => ({
            ...item,
            sparkline: item.sparkline || FALLBACK_ASSETS[index % FALLBACK_ASSETS.length]?.sparkline,
          })));
          setStatus("live");
        }
      } catch (error) {
        if (error.name !== "AbortError") setStatus("fallback");
      }
    }
    load();
    return () => controller.abort();
  }, []);

  const visibleAssets = useMemo(() => {
    if (filter === "top5") return assets.slice(0, 5);
    if (filter === "watchlist") return assets.filter((asset) => WATCHLIST.has(asset.symbol));
    return assets;
  }, [assets, filter]);

  return (
    <section className="dashboard-panel market-overview-panel">
      <div className="dashboard-section-header">
        <div>
          <span>Market Overview</span>
          <h2>Crypto cards</h2>
        </div>
        <div className="segmented-control" role="group" aria-label="Filtre crypto">
          {[
            ["all", "Toutes"],
            ["top5", "Top 5"],
            ["watchlist", "Watchlist"],
          ].map(([key, label]) => (
            <button
              type="button"
              key={key}
              className={filter === key ? "active" : ""}
              onClick={() => setFilter(key)}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="market-status">{status === "live" ? "Database close" : "Fallback preview"}</div>
      <div className="dashboard-crypto-grid">
        {visibleAssets.map((asset) => (
          <CryptoCard
            key={asset.symbol}
            asset={asset}
            onClick={() => onOpenChatQuestion?.(`Analyse ${cryptoName(asset)} cette semaine`)}
          />
        ))}
      </div>
    </section>
  );
}

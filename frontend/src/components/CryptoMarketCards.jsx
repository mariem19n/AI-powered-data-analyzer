import { useEffect, useState } from "react";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";
const SKELETON_CARDS = 5;

const currencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
});

const compactCurrencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  notation: "compact",
  maximumFractionDigits: 2,
});

const dateFormatter = new Intl.DateTimeFormat("en", {
  month: "short",
  day: "numeric",
  year: "numeric",
});

function formatPrice(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "-";
  return currencyFormatter.format(n);
}

function formatVolume(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "-";
  return compactCurrencyFormatter.format(n);
}

function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return String(value);
  return dateFormatter.format(date);
}

function formatChange(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "-";
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}%`;
}

function getChangeClass(value) {
  const n = Number(value);
  if (!Number.isFinite(n) || n === 0) return "neutral";
  return n > 0 ? "positive" : "negative";
}

function SkeletonCard() {
  return (
    <article className="crypto-card skeleton-card" aria-hidden="true">
      <div className="skeleton-line short" />
      <div className="skeleton-line price" />
      <div className="skeleton-line" />
      <div className="skeleton-line tiny" />
    </article>
  );
}

export default function CryptoMarketCards() {
  const [prices, setPrices] = useState([]);
  const [status, setStatus] = useState("loading");

  useEffect(() => {
    const controller = new AbortController();

    async function loadPrices() {
      try {
        const response = await fetch(`${API_BASE_URL}/api/crypto/latest-prices`, {
          signal: controller.signal,
        });

        if (!response.ok) {
          throw new Error(`Request failed with status ${response.status}`);
        }

        const payload = await response.json();
        setPrices(Array.isArray(payload) ? payload : []);
        setStatus("ready");
      } catch (error) {
        if (error.name !== "AbortError") {
          setStatus("error");
        }
      }
    }

    loadPrices();
    return () => controller.abort();
  }, []);

  if (status === "error") {
    return (
      <section className="crypto-market-section" aria-label="Latest crypto prices">
        <div className="crypto-market-header">
          <div>
            <span className="crypto-market-kicker">Market snapshot</span>
            <h2>Latest crypto prices</h2>
          </div>
          <p className="crypto-market-error">Unable to load live prices.</p>
        </div>
      </section>
    );
  }

  return (
    <section className="crypto-market-section" aria-label="Latest crypto prices">
      <div className="crypto-market-header">
        <div>
          <span className="crypto-market-kicker">Market snapshot</span>
          <h2>Latest crypto prices</h2>
        </div>
        <span className="crypto-market-source">Database close</span>
      </div>

      <div className="crypto-card-grid">
        {status === "loading"
          ? Array.from({ length: SKELETON_CARDS }, (_, index) => (
              <SkeletonCard key={index} />
            ))
          : prices.map((item) => (
              <article className="crypto-card" key={item.symbol}>
                <div className="crypto-card-top">
                  <div>
                    <strong>{item.symbol}</strong>
                    <span>{item.name || item.symbol}</span>
                  </div>
                  <span className={`crypto-change ${getChangeClass(item.change_24h_pct)}`}>
                    {formatChange(item.change_24h_pct)}
                  </span>
                </div>

                <div className="crypto-price">{formatPrice(item.price_usd)}</div>

                <dl className="crypto-card-meta">
                  <div>
                    <dt>Date</dt>
                    <dd>{formatDate(item.date)}</dd>
                  </div>
                  <div>
                    <dt>Volume</dt>
                    <dd>{formatVolume(item.volume)}</dd>
                  </div>
                </dl>
              </article>
            ))}
      </div>
    </section>
  );
}

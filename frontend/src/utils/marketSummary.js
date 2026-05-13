export function extractRecords(response) {
  const datasets = Array.isArray(response?.data) ? response.data : [];
  const firstWithRecords = datasets.find((item) => Array.isArray(item?.records));
  return firstWithRecords?.records ?? [];
}

function toNumber(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function toTime(value) {
  const time = new Date(value).getTime();
  return Number.isFinite(time) ? time : null;
}

export function getDateRange(records) {
  const dates = records
    .map((record) => record?.date)
    .filter(Boolean)
    .map((value) => new Date(value))
    .filter((date) => Number.isFinite(date.getTime()))
    .sort((a, b) => a.getTime() - b.getTime());

  if (!dates.length) {
    return { start: null, end: null, label: "No date range available" };
  }

  const format = new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
  const start = dates[0];
  const end = dates[dates.length - 1];
  return {
    start,
    end,
    label:
      start.getTime() === end.getTime()
        ? format.format(start)
        : `${format.format(start)} - ${format.format(end)}`,
  };
}

export function computeMarketSummary(records) {
  if (!Array.isArray(records) || records.length === 0) {
    return null;
  }

  const clean = records
    .map((record) => ({
      ...record,
      _time: toTime(record?.date),
      _price: toNumber(record?.close_usd),
    }))
    .filter((record) => record._time !== null && record._price !== null)
    .sort((a, b) => a._time - b._time);

  if (!clean.length) {
    return null;
  }

  const prices = clean.map((record) => record._price);
  const firstPrice = prices[0];
  const latestPrice = prices[prices.length - 1];
  const variationPercent =
    firstPrice !== 0 ? ((latestPrice - firstPrice) / Math.abs(firstPrice)) * 100 : null;
  const minPrice = Math.min(...prices);
  const maxPrice = Math.max(...prices);
  const averagePrice = prices.reduce((sum, price) => sum + price, 0) / prices.length;
  const symbol = clean.find((record) => record?.symbol)?.symbol ?? null;
  const dateRange = getDateRange(clean);

  return {
    symbol,
    latestPrice,
    firstPrice,
    variationPercent,
    minPrice,
    maxPrice,
    averagePrice,
    trend: variationPercent === null || variationPercent >= 0 ? "up" : "down",
    observations: clean.length,
    dateRange,
    sparkline: clean.map((record) => ({
      date: record.date,
      value: record._price,
    })),
    metric: "close_usd",
  };
}

export function formatCurrency(value) {
  if (!Number.isFinite(value)) {
    return "-";
  }
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: value >= 100 ? 0 : 2,
  }).format(value);
}

export function formatPercent(value) {
  if (!Number.isFinite(value)) {
    return "-";
  }
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}

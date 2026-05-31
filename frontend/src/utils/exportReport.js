import { jsPDF } from "jspdf";

const TALAN_COLORS = {
  purple: "#6C367E",
  pink: "#E14480",
  blue: "#5580B9",
  olive: "#8D9323",
  background: "#F5F4F0",
  text: "#111827",
  muted: "#6B7280",
  border: "#DDD8E2",
  white: "#FFFFFF",
};

const PDF_MARGIN = 18;
const PDF_FOOTER_HEIGHT = 18;
const PLOTLY_IMAGE_WIDTH = 1000;
const PLOTLY_IMAGE_HEIGHT = 550;

function hasObjectValue(value) {
  return value && typeof value === "object" && Object.keys(value).length > 0;
}

function asArray(value) {
  return Array.isArray(value) ? value.filter(Boolean) : [];
}

function safeText(value, fallback = "") {
  if (value == null) return fallback;
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return fallback;
}

function getIntentValue(response) {
  return response?.intent?.primary || response?.intent || "";
}

function getResponseDate(response) {
  return response?.created_at || new Date().toISOString();
}

function responseModeLabel(response) {
  const mode = response?.response_mode || response?.provenance?.response_mode || "";
  if (mode === "internal") return "Données internes";
  if (mode === "external") return "Sources externes";
  if (mode === "hybrid") return "Hybride";
  return mode || "Non disponible";
}

function formatDateForFilename(date = new Date()) {
  const pad = (value) => String(value).padStart(2, "0");
  return [
    date.getFullYear(),
    pad(date.getMonth() + 1),
    pad(date.getDate()),
  ].join("-") + `-${pad(date.getHours())}-${pad(date.getMinutes())}`;
}

function formatDisplayDate(value) {
  const date = value ? new Date(value) : new Date();
  if (Number.isNaN(date.getTime())) return safeText(value);
  return new Intl.DateTimeFormat("fr-FR", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function reportFilename(extension) {
  return `analysis-report-${formatDateForFilename()}.${extension}`;
}

function escapeMarkdown(value) {
  return safeText(value).replace(/\|/g, "\\|");
}

function sourceTitle(source, index) {
  return source?.title || source?.name || source?.url || `Source ${index + 1}`;
}

function sourceDomain(source) {
  return source?.domain || source?.source_domain || source?.provider || "";
}

function sourceSnippet(source) {
  return source?.snippet || source?.description || source?.summary || "";
}

function cleanFormattedNumber(value) {
  return safeText(value).replace(/\u202f/g, " ").replace(/\u00a0/g, " ");
}

function formatNumber(value, options = {}) {
  if (value == null || value === "") return "-";
  const number = Number(value);
  if (!Number.isFinite(number)) return safeText(value);
  const maximumFractionDigits = options.maximumFractionDigits ?? (Math.abs(number) >= 100 ? 0 : 2);
  return cleanFormattedNumber(
    new Intl.NumberFormat("fr-FR", { maximumFractionDigits }).format(number)
  );
}

function formatPercent(value) {
  if (value == null || value === "") return "-";
  const number = Number(value);
  if (!Number.isFinite(number)) return safeText(value);
  return `${formatNumber(number, { maximumFractionDigits: 1 })} %`;
}

function isPrimitive(value) {
  return value == null || ["string", "number", "boolean"].includes(typeof value);
}

function isStatsBlock(value) {
  if (!hasObjectValue(value)) return false;
  return Boolean(
    value.metrics_by_symbol ||
      value.mean != null ||
      value.median != null ||
      value.trend_direction ||
      value.pct_change_total != null ||
      value.anomaly_count != null ||
      value.n_anomalies != null ||
      value.anomalies_count != null ||
      value.forecast_horizon != null ||
      value.model_used ||
      value.model ||
      value.metadata?.horizon_days ||
      value.evaluation ||
      value.diagnostics
  );
}

function getStatsEntries(stats) {
  if (!hasObjectValue(stats)) return [];
  if (isStatsBlock(stats)) return [{ stepId: "", stats }];
  return Object.entries(stats)
    .filter(([, value]) => hasObjectValue(value))
    .map(([stepId, value]) => ({ stepId, stats: value }));
}

function getComparisonRows(stats) {
  const metrics = stats?.metrics_by_symbol;
  if (!metrics || typeof metrics !== "object") return [];
  return Object.entries(metrics).map(([symbol, values]) => ({
    symbol,
    mean: formatNumber(values?.mean_volume ?? values?.mean),
    median: formatNumber(values?.median_volume ?? values?.median),
    min: formatNumber(values?.min_volume ?? values?.min),
    max: formatNumber(values?.max_volume ?? values?.max),
    total: formatNumber(values?.total_volume ?? values?.total),
  }));
}

function getDescriptiveRows(stats) {
  const rows = [
    ["Nombre de points", stats?.n ?? stats?.count ?? stats?.aligned_points],
    ["Moyenne", stats?.mean],
    ["Médiane", stats?.median],
    ["Minimum", stats?.min],
    ["Maximum", stats?.max],
    ["Écart-type", stats?.std],
    ["Tendance", stats?.trend_direction],
    ["Variation totale", stats?.pct_change_total != null ? formatPercent(Number(stats.pct_change_total) * 100) : null],
  ];
  return rows
    .filter(([, value]) => value != null && value !== "")
    .map(([label, value]) => [label, typeof value === "number" ? formatNumber(value) : safeText(value)]);
}

function getAnomalyRows(stats) {
  const anomalyDates = asArray(stats?.anomaly_dates)
    .concat(asArray(stats?.top_anomalies).map((item) => item?.date))
    .filter(Boolean)
    .slice(0, 6);
  const rows = [
    ["Méthode utilisée", stats?.algorithm ?? stats?.method],
    ["Nombre d'anomalies", stats?.anomaly_count ?? stats?.n_anomalies ?? stats?.anomalies_count],
    ["Seuil", stats?.threshold ?? stats?.z_threshold ?? stats?.iqr_multiplier],
    ["Dates principales", anomalyDates.join(", ")],
  ];
  return rows
    .filter(([, value]) => value != null && value !== "")
    .map(([label, value]) => [label, typeof value === "number" ? formatNumber(value) : safeText(value)]);
}

function getForecastRows(stats) {
  const evaluation = stats?.evaluation || {};
  const metadata = stats?.metadata || {};
  const rows = [
    ["Modèle utilisé", stats?.model_used ?? stats?.model ?? metadata.model],
    ["Horizon", stats?.forecast_horizon ?? metadata.horizon_days],
    ["MAE", stats?.mae ?? evaluation.mae],
    ["RMSE", stats?.rmse ?? evaluation.rmse],
    ["MAPE", stats?.mape ?? evaluation.mape],
  ];
  return rows
    .filter(([, value]) => value != null && value !== "")
    .map(([label, value]) => [label, typeof value === "number" ? formatNumber(value) : safeText(value)]);
}

function getPrimitiveRows(stats) {
  if (!hasObjectValue(stats)) return [];
  return Object.entries(stats)
    .filter(([, value]) => isPrimitive(value) && value != null && value !== "")
    .slice(0, 18)
    .map(([key, value]) => [key, typeof value === "number" ? formatNumber(value) : safeText(value)]);
}

function getStatsPresentation(stats) {
  const comparisonRows = getComparisonRows(stats);
  if (comparisonRows.length) {
    const details = [
      ["Actif dominant", stats?.higher_volume_asset],
      ["Écart moyen", stats?.average_volume_pct_diff != null ? formatPercent(stats.average_volume_pct_diff) : null],
    ].filter(([, value]) => value != null && value !== "");
    return { type: "comparison", comparisonRows, details };
  }

  const forecastRows = getForecastRows(stats);
  if (forecastRows.length >= 2) return { type: "keyValue", rows: forecastRows };

  const anomalyRows = getAnomalyRows(stats);
  if (anomalyRows.length >= 2) return { type: "keyValue", rows: anomalyRows };

  const descriptiveRows = getDescriptiveRows(stats);
  if (descriptiveRows.length >= 2) return { type: "keyValue", rows: descriptiveRows };

  return { type: "keyValue", rows: getPrimitiveRows(stats) };
}

export function getExternalSources(response) {
  const direct = asArray(response?.external_data?.sources);
  const provenanceGroups = asArray(response?.provenance?.external_sources);
  const nested = provenanceGroups.flatMap((group) => asArray(group?.sources));
  const flatProvenance = provenanceGroups.filter((item) => item?.url);
  return [...direct, ...nested, ...flatProvenance].filter((source) => source?.url);
}

export function stringifyStats(stats) {
  if (!hasObjectValue(stats)) return "";
  return JSON.stringify(stats, null, 2);
}

function pushListSection(lines, title, items) {
  if (!items.length) return;
  lines.push(`## ${title}`, "");
  items.forEach((item) => lines.push(`- ${escapeMarkdown(item)}`));
  lines.push("");
}

function pushMarkdownKeyValueTable(lines, rows) {
  if (!rows.length) return;
  lines.push("| Indicateur | Valeur |", "|---|---:|");
  rows.forEach(([label, value]) => {
    lines.push(`| ${escapeMarkdown(label)} | ${escapeMarkdown(value)} |`);
  });
  lines.push("");
}

function pushStatsSection(lines, stats) {
  const entries = getStatsEntries(stats);
  if (!entries.length) return;
  lines.push("## Statistiques calculées", "");
  entries.forEach(({ stepId, stats: stepStats }) => {
    if (stepId) lines.push(`### ${escapeMarkdown(stepId)}`, "");
    const presentation = getStatsPresentation(stepStats);
    if (presentation.type === "comparison") {
      lines.push("| Symbole | Moyenne | Médiane | Minimum | Maximum | Total |");
      lines.push("|---|---:|---:|---:|---:|---:|");
      presentation.comparisonRows.forEach((row) => {
        lines.push(`| ${escapeMarkdown(row.symbol)} | ${row.mean} | ${row.median} | ${row.min} | ${row.max} | ${row.total} |`);
      });
      lines.push("");
      pushMarkdownKeyValueTable(lines, presentation.details);
      return;
    }
    pushMarkdownKeyValueTable(lines, presentation.rows);
  });
}

function pushDataSection(lines, response) {
  const datasets = asArray(response?.data);
  if (!datasets.length) return;

  lines.push("## Données utilisées", "");
  datasets.forEach((item, index) => {
    const title = item?.step_id || `dataset_${index + 1}`;
    lines.push(`### ${escapeMarkdown(title)}`, "");
    if (item?.row_count != null) lines.push(`- row_count: ${formatNumber(item.row_count)}`);
    if (Array.isArray(item?.columns) && item.columns.length) {
      lines.push(`- columns: ${item.columns.map(escapeMarkdown).join(", ")}`);
    }
    if (item?.sql) lines.push("", "```sql", item.sql, "```");
    lines.push("");
  });
}

function pushSourcesSection(lines, response) {
  const sources = getExternalSources(response);
  if (!sources.length) return;

  lines.push("## Sources externes", "");
  sources.forEach((source, index) => {
    const title = sourceTitle(source, index);
    const domain = sourceDomain(source);
    const snippet = sourceSnippet(source);
    const suffix = [domain, snippet].filter(Boolean).join(" - ");
    lines.push(`- [${escapeMarkdown(title)}](${source.url})${suffix ? ` - ${escapeMarkdown(suffix)}` : ""}`);
  });
  lines.push("");
}

function methodToLine(method) {
  if (typeof method === "string") return method;
  if (!hasObjectValue(method)) return "";
  const parts = [method.name, method.description].filter(Boolean).join(" — ");
  const suffix = [method.algorithm, method.reliability_note].filter(Boolean).join(" — ");
  return [parts, suffix].filter(Boolean).join(" — ");
}

function dataSourceToLine(source) {
  if (typeof source === "string") return source;
  if (!hasObjectValue(source)) return "";
  const count = source.record_count != null ? `${formatNumber(source.record_count)} lignes analysées` : "";
  const tables = asArray(source.tables).length ? `table: ${source.tables.join(", ")}` : "";
  return [source.name, source.description, count, tables, source.time_range].filter(Boolean).join(" — ");
}

function pushProvenanceSection(lines, response) {
  const provenance = response?.provenance;
  if (!hasObjectValue(provenance)) return;
  const methods = asArray(provenance.methods).map(methodToLine).filter(Boolean);
  const dataSources = asArray(provenance.data_sources).map(dataSourceToLine).filter(Boolean);
  const externalGroups = asArray(provenance.external_sources);
  if (!provenance.summary && !methods.length && !dataSources.length && !externalGroups.length) return;

  lines.push("## Provenance / Méthode", "");
  if (provenance.summary) lines.push(`${provenance.summary}`, "");
  lines.push(`- Mode de réponse: ${responseModeLabel(response)}`, "");
  if (methods.length) pushListSection(lines, "Méthodes utilisées", methods);
  if (dataSources.length) pushListSection(lines, "Données utilisées par la méthode", dataSources);
  externalGroups.forEach((group, index) => {
    lines.push(`### Sources externes ${index + 1}`, "");
    if (group.provider) lines.push(`- Provider: ${escapeMarkdown(group.provider)}`);
    if (group.query) lines.push(`- Query: ${escapeMarkdown(group.query)}`);
    asArray(group.sources).forEach((source, sourceIndex) => {
      const title = sourceTitle(source, sourceIndex);
      if (source.url) lines.push(`- [${escapeMarkdown(title)}](${source.url})`);
    });
    lines.push("");
  });
}

function pushVisualizationSection(lines, response) {
  if (!asArray(response?.visualizations).length) return;
  lines.push("## Visualisation", "", "Visualisation disponible dans l'interface.", "");
}

export function buildMarkdownReport(response, originalQuestion) {
  const question = originalQuestion || response?.question || "";
  const insights = asArray(response?.insights);
  const recommendations = asArray(response?.recommendations);

  const lines = [
    "# Rapport d'analyse",
    "",
    "## Question",
    question || "Question non disponible.",
    "",
    "## Synthèse",
    `- Type d'analyse: ${getIntentValue(response) || "Non disponible"}`,
    `- Mode de réponse: ${responseModeLabel(response)}`,
    `- Date: ${formatDisplayDate(getResponseDate(response))}`,
    "",
  ];

  pushListSection(lines, "Insights clés", insights);
  pushListSection(lines, "Recommandations", recommendations);
  pushVisualizationSection(lines, response);
  pushStatsSection(lines, response?.analysis_stats);
  pushDataSection(lines, response);
  pushSourcesSection(lines, response);
  pushProvenanceSection(lines, response);

  return lines.join("\n");
}

export function downloadTextFile(filename, content, mimeType) {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export function exportResponseAsMarkdown(response, originalQuestion) {
  const markdown = buildMarkdownReport(response, originalQuestion);
  downloadTextFile(reportFilename("md"), markdown, "text/markdown;charset=utf-8");
}

function loadImageDataUrl(src) {
  return new Promise((resolve) => {
    const image = new Image();
    image.crossOrigin = "anonymous";
    image.onload = () => {
      try {
        const canvas = document.createElement("canvas");
        canvas.width = image.naturalWidth || image.width;
        canvas.height = image.naturalHeight || image.height;
        const context = canvas.getContext("2d");
        context.drawImage(image, 0, 0);
        resolve(canvas.toDataURL("image/png"));
      } catch {
        resolve(null);
      }
    };
    image.onerror = () => resolve(null);
    image.src = src;
  });
}

async function plotlyFigureToPng(visualization) {
  const PlotlyModule = await import("plotly.js-dist-min");
  const Plotly = PlotlyModule.default || PlotlyModule;
  const figure = {
    data: visualization?.data || [],
    layout: {
      ...(visualization?.layout || {}),
      width: PLOTLY_IMAGE_WIDTH,
      height: PLOTLY_IMAGE_HEIGHT,
      paper_bgcolor: TALAN_COLORS.white,
      plot_bgcolor: TALAN_COLORS.white,
    },
  };

  return Plotly.toImage(figure, {
    format: "png",
    width: PLOTLY_IMAGE_WIDTH,
    height: PLOTLY_IMAGE_HEIGHT,
  });
}

function createPdfWriter(doc) {
  const pageWidth = doc.internal.pageSize.getWidth();
  const pageHeight = doc.internal.pageSize.getHeight();
  const contentWidth = pageWidth - PDF_MARGIN * 2;
  let y = 18;

  function ensureSpace(height = 10) {
    if (y + height > pageHeight - PDF_FOOTER_HEIGHT) {
      doc.addPage();
      y = 18;
    }
  }

  function setY(nextY) {
    y = nextY;
  }

  function addSectionTitle(title, color = TALAN_COLORS.purple) {
    ensureSpace(16);
    doc.setFont("helvetica", "bold");
    doc.setFontSize(13);
    doc.setTextColor(color);
    doc.text(title, PDF_MARGIN, y);
    doc.setDrawColor(color);
    doc.setLineWidth(0.4);
    doc.line(PDF_MARGIN, y + 2.8, PDF_MARGIN + 36, y + 2.8);
    y += 9;
  }

  function addParagraph(text, options = {}) {
    const {
      fontSize = 9.5,
      color = TALAN_COLORS.text,
      font = "normal",
      indent = 0,
      lineGap = 4.8,
    } = options;
    doc.setFont("helvetica", font);
    doc.setFontSize(fontSize);
    doc.setTextColor(color);
    const lines = doc.splitTextToSize(safeText(text), contentWidth - indent);
    lines.forEach((line) => {
      ensureSpace(lineGap + 1);
      doc.text(line, PDF_MARGIN + indent, y);
      y += lineGap;
    });
  }

  function addBulletList(items) {
    items.filter(Boolean).forEach((item) => {
      ensureSpace(8);
      doc.setFillColor(TALAN_COLORS.pink);
      doc.circle(PDF_MARGIN + 1.5, y - 1.4, 0.8, "F");
      addParagraph(safeText(item), { indent: 6 });
      y += 1.2;
    });
  }

  function addInfoBlock(rows) {
    const cleanRows = rows.filter((row) => row?.value);
    if (!cleanRows.length) return;
    const rowHeight = 8;
    ensureSpace(cleanRows.length * rowHeight + 10);
    doc.setFillColor(TALAN_COLORS.background);
    doc.setDrawColor(TALAN_COLORS.border);
    doc.roundedRect(PDF_MARGIN, y, contentWidth, cleanRows.length * rowHeight + 7, 2, 2, "FD");
    y += 7;
    cleanRows.forEach((row) => {
      doc.setFont("helvetica", "bold");
      doc.setFontSize(8.3);
      doc.setTextColor(TALAN_COLORS.purple);
      doc.text(row.label, PDF_MARGIN + 5, y);
      doc.setFont("helvetica", "normal");
      doc.setTextColor(TALAN_COLORS.text);
      const wrapped = doc.splitTextToSize(safeText(row.value), contentWidth - 58);
      doc.text(wrapped, PDF_MARGIN + 50, y);
      y += Math.max(rowHeight, wrapped.length * 4.2);
    });
    y += 4;
  }

  function addKeyValueTable(rows) {
    if (!rows.length) return;
    const rowHeight = 8;
    const labelWidth = 62;
    ensureSpace(rowHeight * (rows.length + 1) + 4);
    doc.setFillColor(TALAN_COLORS.blue);
    doc.rect(PDF_MARGIN, y, contentWidth, rowHeight, "F");
    doc.setFont("helvetica", "bold");
    doc.setFontSize(8.5);
    doc.setTextColor(TALAN_COLORS.white);
    doc.text("Indicateur", PDF_MARGIN + 3, y + 5.3);
    doc.text("Valeur", PDF_MARGIN + labelWidth + 3, y + 5.3);
    y += rowHeight;
    rows.forEach(([label, value], index) => {
      ensureSpace(rowHeight + 2);
      doc.setFillColor(index % 2 === 0 ? TALAN_COLORS.white : TALAN_COLORS.background);
      doc.setDrawColor("#E5E7EB");
      doc.rect(PDF_MARGIN, y, contentWidth, rowHeight, "FD");
      doc.setFont("helvetica", "normal");
      doc.setFontSize(8.2);
      doc.setTextColor(TALAN_COLORS.text);
      doc.text(safeText(label), PDF_MARGIN + 3, y + 5.3);
      doc.text(safeText(value), PDF_MARGIN + labelWidth + 3, y + 5.3);
      y += rowHeight;
    });
    y += 5;
  }

  function addCodeBlock(code) {
    if (!code) return;
    const lines = doc.splitTextToSize(safeText(code), contentWidth - 8);
    const lineHeight = 4.1;
    let index = 0;
    while (index < lines.length) {
      ensureSpace(14);
      const availableLines = Math.max(1, Math.floor((pageHeight - PDF_FOOTER_HEIGHT - y - 8) / lineHeight));
      const chunk = lines.slice(index, index + availableLines);
      const boxHeight = chunk.length * lineHeight + 7;
      doc.setFillColor(TALAN_COLORS.background);
      doc.setDrawColor("#E5E7EB");
      doc.roundedRect(PDF_MARGIN, y, contentWidth, boxHeight, 1.5, 1.5, "FD");
      doc.setFont("courier", "normal");
      doc.setFontSize(8);
      doc.setTextColor(TALAN_COLORS.text);
      let lineY = y + 5;
      chunk.forEach((line) => {
        doc.text(line, PDF_MARGIN + 4, lineY);
        lineY += lineHeight;
      });
      y += boxHeight + 4;
      index += chunk.length;
    }
  }

  function addComparisonTable(rows) {
    if (!rows.length) return;
    const headers = ["Symbole", "Moyenne", "Médiane", "Minimum", "Maximum", "Total"];
    const widths = [27, 29, 29, 28, 28, 29];
    const rowHeight = 8;
    ensureSpace(rowHeight * (rows.length + 2));
    doc.setFont("helvetica", "bold");
    doc.setFontSize(8.1);
    doc.setTextColor(TALAN_COLORS.white);
    doc.setFillColor(TALAN_COLORS.blue);
    doc.rect(PDF_MARGIN, y, contentWidth, rowHeight, "F");
    let x = PDF_MARGIN + 3;
    headers.forEach((header, index) => {
      doc.text(header, x, y + 5.3);
      x += widths[index];
    });
    y += rowHeight;
    rows.forEach((row, index) => {
      ensureSpace(rowHeight + 2);
      doc.setFillColor(index % 2 === 0 ? TALAN_COLORS.white : TALAN_COLORS.background);
      doc.setDrawColor("#E5E7EB");
      doc.rect(PDF_MARGIN, y, contentWidth, rowHeight, "FD");
      doc.setFont("helvetica", index === 0 ? "bold" : "normal");
      doc.setFontSize(8);
      doc.setTextColor(TALAN_COLORS.text);
      x = PDF_MARGIN + 3;
      [row.symbol, row.mean, row.median, row.min, row.max, row.total].forEach((cell, cellIndex) => {
        doc.text(safeText(cell), x, y + 5.3);
        x += widths[cellIndex];
      });
      y += rowHeight;
    });
    y += 5;
  }

  function addImage(dataUrl, title = "") {
    if (title) addParagraph(title, { font: "bold", color: TALAN_COLORS.text });
    const imageWidth = contentWidth;
    const imageHeight = imageWidth * (PLOTLY_IMAGE_HEIGHT / PLOTLY_IMAGE_WIDTH);
    ensureSpace(imageHeight + 8);
    doc.setDrawColor("#E5E7EB");
    doc.setFillColor(TALAN_COLORS.white);
    doc.roundedRect(PDF_MARGIN, y, imageWidth, imageHeight, 1.5, 1.5, "FD");
    doc.addImage(dataUrl, "PNG", PDF_MARGIN, y, imageWidth, imageHeight, undefined, "FAST");
    y += imageHeight + 8;
  }

  function addDataset(item, index) {
    ensureSpace(18);
    doc.setFont("helvetica", "bold");
    doc.setFontSize(10);
    doc.setTextColor(TALAN_COLORS.olive);
    doc.text(item?.step_id || `dataset_${index + 1}`, PDF_MARGIN, y);
    y += 6;
    addParagraph(`row_count: ${formatNumber(item?.row_count ?? 0)}`, {
      color: TALAN_COLORS.muted,
      fontSize: 8.8,
    });
    if (Array.isArray(item?.columns) && item.columns.length) {
      addParagraph(`columns: ${item.columns.join(", ")}`, {
        color: TALAN_COLORS.muted,
        fontSize: 8.8,
      });
    }
    addCodeBlock(item?.sql);
  }

  function addFooter() {
    const pages = doc.getNumberOfPages();
    for (let page = 1; page <= pages; page += 1) {
      doc.setPage(page);
      doc.setDrawColor("#E5E7EB");
      doc.line(PDF_MARGIN, pageHeight - 12, pageWidth - PDF_MARGIN, pageHeight - 12);
      doc.setFont("helvetica", "normal");
      doc.setFontSize(7.5);
      doc.setTextColor(TALAN_COLORS.muted);
      doc.text("Generated by AI-Powered Data Analyzer", PDF_MARGIN, pageHeight - 7);
      doc.text(`${page} / ${pages}`, pageWidth - PDF_MARGIN, pageHeight - 7, { align: "right" });
    }
  }

  return {
    addBulletList,
    addCodeBlock,
    addComparisonTable,
    addDataset,
    addFooter,
    addImage,
    addInfoBlock,
    addKeyValueTable,
    addParagraph,
    addSectionTitle,
    get y() {
      return y;
    },
    setY,
  };
}

function addHeader(doc, response, logoDataUrl) {
  const pageWidth = doc.internal.pageSize.getWidth();
  doc.setFillColor(TALAN_COLORS.background);
  doc.rect(0, 0, pageWidth, 40, "F");
  doc.setFont("helvetica", "bold");
  doc.setFontSize(20);
  doc.setTextColor(TALAN_COLORS.purple);
  doc.text("Rapport d'analyse", PDF_MARGIN, 18);

  doc.setFont("helvetica", "normal");
  doc.setFontSize(9.5);
  doc.setTextColor(TALAN_COLORS.muted);
  doc.text("AI-Powered Data Analyzer", PDF_MARGIN, 25);
  doc.text(formatDisplayDate(getResponseDate(response)), PDF_MARGIN, 31);

  doc.setDrawColor(TALAN_COLORS.pink);
  doc.setLineWidth(0.8);
  doc.line(PDF_MARGIN, 36, PDF_MARGIN + 48, 36);

  if (logoDataUrl) {
    try {
      doc.addImage(logoDataUrl, "PNG", pageWidth - PDF_MARGIN - 32, 12, 32, 12, undefined, "FAST");
    } catch {
      // The report must still export if the optional logo cannot be rendered.
    }
  }
}

function renderPdfStatsSection(pdf, stats) {
  const entries = getStatsEntries(stats);
  if (!entries.length) return;
  pdf.addSectionTitle("Statistiques calculées", TALAN_COLORS.purple);
  entries.forEach(({ stepId, stats: stepStats }) => {
    if (stepId) pdf.addParagraph(stepId, { font: "bold", color: TALAN_COLORS.olive });
    const presentation = getStatsPresentation(stepStats);
    if (presentation.type === "comparison") {
      pdf.addComparisonTable(presentation.comparisonRows);
      pdf.addKeyValueTable(presentation.details);
    } else {
      pdf.addKeyValueTable(presentation.rows);
    }
  });
}

function renderPdfProvenanceSection(pdf, response) {
  const provenance = response?.provenance;
  if (!hasObjectValue(provenance)) return;
  const methods = asArray(provenance.methods).map(methodToLine).filter(Boolean);
  const dataSources = asArray(provenance.data_sources).map(dataSourceToLine).filter(Boolean);
  const externalGroups = asArray(provenance.external_sources);
  if (!provenance.summary && !methods.length && !dataSources.length && !externalGroups.length) return;

  pdf.addSectionTitle("Provenance / Méthode", TALAN_COLORS.olive);
  if (provenance.summary) pdf.addParagraph(provenance.summary);
  pdf.addInfoBlock([{ label: "Mode", value: responseModeLabel(response) }]);
  if (methods.length) {
    pdf.addParagraph("Méthodes utilisées", { font: "bold", color: TALAN_COLORS.text });
    pdf.addBulletList(methods);
  }
  if (dataSources.length) {
    pdf.addParagraph("Données utilisées", { font: "bold", color: TALAN_COLORS.text });
    pdf.addBulletList(dataSources);
  }
  externalGroups.forEach((group) => {
    pdf.addParagraph(group.provider || "Sources externes", { font: "bold", color: TALAN_COLORS.text });
    if (group.query) pdf.addParagraph(`Requête: ${group.query}`, { color: TALAN_COLORS.muted, fontSize: 8.7 });
    asArray(group.sources).forEach((source, index) => {
      const title = sourceTitle(source, index);
      const line = [title, source.url].filter(Boolean).join(" — ");
      if (line) pdf.addParagraph(line, { color: source.url ? TALAN_COLORS.blue : TALAN_COLORS.text, fontSize: 8.7 });
    });
  });
}

async function renderPdfVisualizations(pdf, visualizations) {
  const charts = asArray(visualizations);
  if (!charts.length) return;
  pdf.addSectionTitle(charts.length > 1 ? "Visualisations" : "Visualisation", TALAN_COLORS.blue);
  for (let index = 0; index < charts.length; index += 1) {
    const chart = charts[index];
    const title = chart?.layout?.title?.text || chart?.layout?.title || (charts.length > 1 ? `Graphique ${index + 1}` : "");
    try {
      const image = await plotlyFigureToPng(chart);
      pdf.addImage(image, safeText(title));
    } catch {
      pdf.addParagraph("La visualisation n'a pas pu être intégrée au PDF.", {
        color: TALAN_COLORS.muted,
        fontSize: 8.8,
      });
    }
  }
}

export async function exportResponseAsPdf(response, originalQuestion) {
  const doc = new jsPDF({ unit: "mm", format: "a4" });
  const question = originalQuestion || response?.question || "Question non disponible.";
  const insights = asArray(response?.insights);
  const recommendations = asArray(response?.recommendations);
  const sources = getExternalSources(response);
  const datasets = asArray(response?.data);
  const logoDataUrl = await loadImageDataUrl("/talan-logo.png");
  const pdf = createPdfWriter(doc);

  addHeader(doc, response, logoDataUrl);
  pdf.setY(50);

  pdf.addInfoBlock([
    { label: "Question", value: question },
    { label: "Type d'analyse", value: getIntentValue(response) || "Non disponible" },
    { label: "Mode de réponse", value: responseModeLabel(response) },
    { label: "Généré le", value: formatDisplayDate(getResponseDate(response)) },
  ]);

  if (insights.length) {
    pdf.addSectionTitle("Insights clés", TALAN_COLORS.purple);
    pdf.addBulletList(insights);
  }

  if (recommendations.length) {
    pdf.addSectionTitle("Recommandations", TALAN_COLORS.blue);
    pdf.addBulletList(recommendations);
  }

  renderPdfStatsSection(pdf, response?.analysis_stats);
  await renderPdfVisualizations(pdf, response?.visualizations);

  if (datasets.length) {
    pdf.addSectionTitle("Données utilisées", TALAN_COLORS.olive);
    datasets.forEach((item, index) => pdf.addDataset(item, index));
  }

  if (sources.length) {
    pdf.addSectionTitle("Sources externes", TALAN_COLORS.blue);
    sources.forEach((source, index) => {
      pdf.addParagraph(sourceTitle(source, index), { font: "bold" });
      pdf.addParagraph(source.url, { color: TALAN_COLORS.blue, fontSize: 8.6 });
      const sourceMeta = [sourceDomain(source), sourceSnippet(source)].filter(Boolean).join(" - ");
      if (sourceMeta) {
        pdf.addParagraph(sourceMeta, { color: TALAN_COLORS.muted, fontSize: 8.5 });
      }
      pdf.setY(pdf.y + 2);
    });
    if (response?.source_disclaimer) {
      pdf.addParagraph(`Disclaimer: ${response.source_disclaimer}`, {
        color: TALAN_COLORS.muted,
        fontSize: 8.5,
      });
    }
  }

  renderPdfProvenanceSection(pdf, response);

  pdf.addFooter();
  doc.save(reportFilename("pdf"));
}

export function hasExportableResponse(response) {
  return Boolean(
    response &&
      (asArray(response.insights).length ||
        asArray(response.recommendations).length ||
        asArray(response.data).length ||
        hasObjectValue(response.analysis_stats) ||
        asArray(response.visualizations).length ||
        getExternalSources(response).length ||
        hasObjectValue(response.provenance))
  );
}

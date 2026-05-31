const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

async function request(path, options = {}) {
  const separator = path.includes("?") ? "&" : "?";
  const response = await fetch(`${API_BASE_URL}${path}${separator}role=expert`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Role": "expert",
      ...(options.headers || {}),
    },
  });
  if (!response.ok) {
    throw new Error(`Expert API request failed: ${response.status}`);
  }
  return response.json();
}

export function fetchFeedbackQueue() {
  return request("/api/expert/feedback-queue");
}

export function reviewFeedback(feedbackId, payload) {
  return request(`/api/expert/feedback/${feedbackId}/review`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function fetchResponseQuality(limit = 20) {
  return request(`/api/expert/response-quality?limit=${limit}`);
}

export function fetchKgStats() {
  return request("/api/expert/kg-stats");
}

export function fetchPromptPerformance() {
  return request("/api/expert/prompt-performance");
}

export function fetchDataFreshness() {
  return request("/api/expert/data-freshness");
}

export function fetchSemanticHealth() {
  return request("/api/expert/semantic-health");
}

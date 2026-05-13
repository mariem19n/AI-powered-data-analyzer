import { useEffect, useRef, useState } from "react";
import Sidebar from "./Sidebar.jsx";
import TopBar from "./TopBar.jsx";
import UserBubble from "./UserBubble.jsx";
import AssistantBubble from "./AssistantBubble.jsx";
import InputBar from "./InputBar.jsx";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";
const REQUEST_TIMEOUT_MS = 45000;

function getOrCreateSessionId() {
  let id = localStorage.getItem("session_id");
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem("session_id", id);
  }
  return id;
}

function getErrorMessage(error, status) {
  if (error?.name === "AbortError") {
    return "The request timed out. Check the API workload and try again.";
  }
  if (status === 400) {
    return "The backend rejected this question. Try making the asset, period, or intent more explicit.";
  }
  if (status != null && status >= 500) {
    return "The backend returned a server error.";
  }
  if (error?.message === "Invalid JSON") {
    return "The backend returned an unreadable response.";
  }
  if (error?.message) {
    return error.message;
  }
  return "Cannot reach the backend. Make sure FastAPI is running on port 8000.";
}

export default function DashboardPage() {
  const [messages, setMessages] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [apiStatus, setApiStatus] = useState("checking");
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const sessionId = useRef(getOrCreateSessionId());
  const bottomRef = useRef(null);

  useEffect(() => {
    const controller = new AbortController();
    fetch(`${API_BASE_URL}/health`, { signal: controller.signal })
      .then((r) => setApiStatus(r.ok ? "online" : "degraded"))
      .catch(() => setApiStatus("offline"));
    return () => controller.abort();
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isLoading]);

  async function handleSubmit(question) {
    const text = question.trim();
    if (!text || isLoading) return;

    setSidebarOpen(false);
    setMessages((prev) => [...prev, { role: "user", content: text }]);
    setIsLoading(true);

    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    let status = null;

    try {
      const response = await fetch(`${API_BASE_URL}/ask`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: text, session_id: sessionId.current }),
        signal: controller.signal,
      });
      status = response.status;

      if (!response.ok) {
        throw new Error(`Request failed with status ${response.status}`);
      }

      let payload;
      try {
        payload = await response.json();
      } catch {
        throw new Error("Invalid JSON");
      }

      if (payload?.error) {
        throw new Error(String(payload.error));
      }

      setApiStatus("online");
      setMessages((prev) => [...prev, { role: "assistant", response: payload }]);
    } catch (requestError) {
      setApiStatus(status != null ? "degraded" : "offline");
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          isError: true,
          errorMessage: getErrorMessage(requestError, status),
        },
      ]);
    } finally {
      window.clearTimeout(timeout);
      setIsLoading(false);
    }
  }

  function handleNewConversation() {
    setMessages([]);
    const newId = crypto.randomUUID();
    sessionId.current = newId;
    localStorage.setItem("session_id", newId);
    setSidebarOpen(false);
  }

  return (
    <div className="app-shell">
      <Sidebar
        apiStatus={apiStatus}
        apiBase={API_BASE_URL}
        isOpen={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        onNewConversation={handleNewConversation}
      />

      <div className="main-area">
        <TopBar onToggleSidebar={() => setSidebarOpen((v) => !v)} />

        <div className="chat-scroll">
          {messages.length === 0 && !isLoading && (
            <div className="chat-empty">
              <div className="chat-empty-icon" aria-hidden="true">✦</div>
              <h2>How can I help you today?</h2>
              <p>Ask anything about crypto markets — prices, trends, anomalies, and more.</p>
            </div>
          )}

          {messages.map((msg, i) =>
            msg.role === "user" ? (
              <UserBubble key={i} content={msg.content} />
            ) : (
              <AssistantBubble
                key={i}
                response={msg.response}
                isError={msg.isError}
                errorMessage={msg.errorMessage}
                onSubmit={handleSubmit}
              />
            )
          )}

          {isLoading && (
            <div className="message-row assistant">
              <div className="assistant-bubble loading-bubble">
                <span />
                <span />
                <span />
              </div>
            </div>
          )}

          <div ref={bottomRef} />
        </div>

        <InputBar
          onSubmit={handleSubmit}
          isLoading={isLoading}
          hasMessages={messages.length > 0}
        />
      </div>
    </div>
  );
}

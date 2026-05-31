import { useCallback, useEffect, useRef, useState } from "react";
import ConversationSidebar from "../components/ConversationSidebar.jsx";
import TopBar from "../components/TopBar.jsx";
import UserBubble from "../components/UserBubble.jsx";
import AssistantBubble from "../components/AssistantBubble.jsx";
import InputBar from "../components/InputBar.jsx";

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
  if (error?.name === "AbortError") return "The request timed out. Check the API workload and try again.";
  if (status === 400) return "The backend rejected this question. Try making the asset, period, or intent more explicit.";
  if (status != null && status >= 500) return "The backend returned a server error.";
  if (error?.message === "Invalid JSON") return "The backend returned an unreadable response.";
  if (error?.message) return error.message;
  return "Cannot reach the backend. Make sure FastAPI is running on port 8000.";
}

export default function ChatPage({ activePage, onNavigate, prefillQuestion }) {
  const [messages, setMessages] = useState([]);
  const [conversations, setConversations] = useState([]);
  const [activeConversationId, setActiveConversationId] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isConversationLoading, setIsConversationLoading] = useState(false);
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

  const refreshConversations = useCallback(async () => {
    setIsConversationLoading(true);
    try {
      const response = await fetch(`${API_BASE_URL}/conversations`);
      if (!response.ok) throw new Error("Failed to load conversations");
      const payload = await response.json();
      setConversations(Array.isArray(payload) ? payload : []);
      setApiStatus("online");
    } catch {
      setApiStatus("offline");
    } finally {
      setIsConversationLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshConversations();
  }, [refreshConversations]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isLoading]);

  function mapStoredMessages(storedMessages) {
    let lastUserQuestion = "";
    return storedMessages.map((message) => {
      if (message.role === "user") {
        lastUserQuestion = message.content;
        return { role: "user", content: message.content, messageId: message.id };
      }

      if (message.response_json) {
        return {
          role: "assistant",
          messageId: message.id,
          conversationId: message.conversation_id,
          response: message.response_json,
          originalQuestion: message.response_json?.question || lastUserQuestion,
        };
      }

      return {
        role: "assistant",
        messageId: message.id,
        conversationId: message.conversation_id,
        response: { insights: [message.content], data: [], visualizations: [] },
        originalQuestion: lastUserQuestion,
      };
    });
  }

  function getPreviousUserQuestion(index) {
    for (let i = index - 1; i >= 0; i -= 1) {
      if (messages[i]?.role === "user" && messages[i]?.content) return messages[i].content;
    }
    return "";
  }

  async function handleSelectConversation(conversationId) {
    if (isLoading) return;
    setIsConversationLoading(true);
    try {
      const response = await fetch(`${API_BASE_URL}/conversations/${conversationId}/messages`);
      if (!response.ok) throw new Error("Failed to load conversation");
      const payload = await response.json();
      setMessages(mapStoredMessages(Array.isArray(payload) ? payload : []));
      setActiveConversationId(conversationId);
      setSidebarOpen(false);
      setApiStatus("online");
    } catch {
      setApiStatus("degraded");
    } finally {
      setIsConversationLoading(false);
    }
  }

  async function handleDeleteConversation(conversationId) {
    if (!window.confirm("Supprimer cette conversation ?")) return;
    try {
      const response = await fetch(`${API_BASE_URL}/conversations/${conversationId}`, {
        method: "DELETE",
      });
      if (!response.ok) throw new Error("Failed to delete conversation");
      if (conversationId === activeConversationId) handleNewConversation();
      await refreshConversations();
    } catch {
      setApiStatus("degraded");
    }
  }

  async function handleRenameConversation(conversation) {
    const title = window.prompt("Nouveau titre", conversation.title);
    if (!title || title.trim() === conversation.title) return;
    try {
      const response = await fetch(`${API_BASE_URL}/conversations/${conversation.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: title.trim() }),
      });
      if (!response.ok) throw new Error("Failed to rename conversation");
      await refreshConversations();
    } catch {
      setApiStatus("degraded");
    }
  }

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
        body: JSON.stringify({
          question: text,
          session_id: sessionId.current,
          conversation_id: activeConversationId,
        }),
        signal: controller.signal,
      });
      status = response.status;
      if (!response.ok) throw new Error(`Request failed with status ${response.status}`);

      let payload;
      try {
        payload = await response.json();
      } catch {
        throw new Error("Invalid JSON");
      }
      if (payload?.error) throw new Error(String(payload.error));

      setApiStatus("online");
      if (payload?.conversation_id) setActiveConversationId(payload.conversation_id);
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          messageId: payload?.message_id,
          conversationId: payload?.conversation_id,
          response: payload,
          originalQuestion: text,
        },
      ]);
      await refreshConversations();
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
    setActiveConversationId(null);
    const newId = crypto.randomUUID();
    sessionId.current = newId;
    localStorage.setItem("session_id", newId);
    setSidebarOpen(false);
  }

  return (
    <div className="app-shell">
      <ConversationSidebar
        activePage={activePage}
        apiStatus={apiStatus}
        apiBase={API_BASE_URL}
        conversations={conversations}
        activeConversationId={activeConversationId}
        isOpen={sidebarOpen}
        isLoading={isConversationLoading}
        onClose={() => setSidebarOpen(false)}
        onDeleteConversation={handleDeleteConversation}
        onNavigate={onNavigate}
        onNewConversation={handleNewConversation}
        onRenameConversation={handleRenameConversation}
        onSelectConversation={handleSelectConversation}
      />

      <div className="main-area">
        <TopBar title="Chat" onToggleSidebar={() => setSidebarOpen((v) => !v)} />

        <div className="chat-scroll">
          {messages.length === 0 && !isLoading && (
            <div className="chat-empty">
              <div className="chat-empty-icon" aria-hidden="true">✦</div>
              <h2>How can I help you today?</h2>
              <p>Ask anything about crypto markets, prices, trends, anomalies, and more.</p>
            </div>
          )}

          {messages.map((msg, i) =>
            msg.role === "user" ? (
              <UserBubble key={i} content={msg.content} isLoading={isLoading} onRerun={handleSubmit} />
            ) : (
              <AssistantBubble
                key={i}
                response={msg.response}
                isError={msg.isError}
                errorMessage={msg.errorMessage}
                onSubmit={handleSubmit}
                conversationId={msg.conversationId || activeConversationId}
                messageId={msg.messageId || msg.response?.message_id}
                originalQuestion={msg.originalQuestion || msg.response?.question || getPreviousUserQuestion(i)}
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
          prefillValue={prefillQuestion}
        />
      </div>
    </div>
  );
}

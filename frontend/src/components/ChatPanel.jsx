import { useEffect, useRef, useState } from "react";

export default function ChatPanel({ messages, isLoading, onSubmit, suggestions = [] }) {
  const [value, setValue] = useState("");
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isLoading]);

  function submit(event) {
    event.preventDefault();
    const question = value.trim();
    if (!question) return;
    onSubmit(question);
    setValue("");
  }

  function handleSuggestion(question) {
    if (isLoading) return;
    setValue(question);
  }

  return (
    <div className="panel chat-panel">
      <div className="panel-heading">
        <div>
          <span className="section-kicker">Conversation</span>
          <h2>Ask the market</h2>
        </div>
        <span className={`run-state ${isLoading ? "active" : ""}`}>
          {isLoading ? "Running" : "Ready"}
        </span>
      </div>

      <div className="messages">
        {messages.length === 0 && !isLoading ? (
          <div className="empty-chat">
            <span className="empty-icon" aria-hidden="true">?</span>
            <p>Ask a question to generate market insights.</p>
            <div className="inline-prompts">
              {suggestions.slice(0, 3).map((suggestion) => (
                <button
                  type="button"
                  key={suggestion}
                  onClick={() => handleSuggestion(suggestion)}
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        ) : null}

        {messages.map((message, index) => (
          <div
            className={`message-row ${message.role} ${message.isError ? "error" : ""}`}
            key={`${message.role}-${index}`}
          >
            <div className="message-bubble">
              {message.label ? <span className="message-label">{message.label}</span> : null}
              <p>{message.content}</p>
            </div>
          </div>
        ))}

        {isLoading ? (
          <div className="message-row assistant">
            <div className="message-bubble loading-bubble">
              <span />
              <span />
              <span />
            </div>
          </div>
        ) : null}
        <div ref={bottomRef} />
      </div>

      <form className="chat-input" onSubmit={submit}>
        <input
          value={value}
          onChange={(event) => setValue(event.target.value)}
          placeholder="Ask about Bitcoin, Ethereum, anomalies, correlations..."
          disabled={isLoading}
          aria-label="Market analysis question"
        />
        <button type="submit" disabled={isLoading || !value.trim()}>
          Analyze
        </button>
      </form>
    </div>
  );
}

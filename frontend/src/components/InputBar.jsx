import { useState } from "react";

const STARTER_CHIPS = [
  "What's Bitcoin doing today?",
  "Compare ETH and SOL this week",
  "Why is the market down?",
  "Show me Solana sentiment",
];

export default function InputBar({ onSubmit, isLoading, hasMessages }) {
  const [value, setValue] = useState("");

  function submit(event) {
    event.preventDefault();
    const text = value.trim();
    if (!text || isLoading) return;
    onSubmit(text);
    setValue("");
  }

  function selectChip(chip) {
    if (isLoading) return;
    setValue(chip);
  }

  return (
    <div className="input-bar">
      {!hasMessages && (
        <div className="starter-chips">
          {STARTER_CHIPS.map((chip) => (
            <button
              type="button"
              key={chip}
              onClick={() => selectChip(chip)}
              disabled={isLoading}
            >
              {chip}
            </button>
          ))}
        </div>
      )}

      <form className="input-form" onSubmit={submit}>
        <input
          className="input-field"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="Ask about Bitcoin, Ethereum, market trends…"
          disabled={isLoading}
          aria-label="Market question"
        />
        <button
          type="submit"
          className="send-btn"
          disabled={isLoading || !value.trim()}
          aria-label="Send"
        >
          ↑
        </button>
      </form>
    </div>
  );
}

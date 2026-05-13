export default function ClarificationChips({ question, suggestions, onSelect }) {
  const chips = Array.isArray(suggestions) ? suggestions.filter(Boolean) : [];
  if (!question && !chips.length) return null;

  return (
    <div className="clarification">
      {question && <p className="clarif-question">{question}</p>}
      {chips.length > 0 && (
        <div className="clarif-chips">
          {chips.map((chip, i) => (
            <button
              key={i}
              type="button"
              onClick={() => onSelect(chip)}
            >
              {chip}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

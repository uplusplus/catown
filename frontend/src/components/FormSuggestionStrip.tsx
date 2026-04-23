type FormSuggestionItem = {
  key: string;
  label: string;
  value: string;
  title?: string;
  monospace?: boolean;
};

type FormSuggestionStripProps = {
  label: string;
  items: FormSuggestionItem[];
  onSelect: (value: string) => void;
};

export function FormSuggestionStrip({ label, items, onSelect }: FormSuggestionStripProps) {
  if (items.length === 0) return null;

  return (
    <div className="form-suggestion-strip">
      <span className="form-suggestion-strip__label">{label}</span>
      <div className="form-suggestion-strip__chips">
        {items.map((item) => (
          <button
            key={item.key}
            type="button"
            className={`form-suggestion-chip ${item.monospace ? "form-suggestion-chip--mono" : ""}`}
            onClick={() => onSelect(item.value)}
            title={item.title || item.label}
          >
            {item.label}
          </button>
        ))}
      </div>
    </div>
  );
}

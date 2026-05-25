import { memo, useCallback, useState, type CSSProperties } from "react";

/** Choice Box types matching backend services/choice_box.py */

export type ChoiceBoxOption = {
  id: string;
  label: string;
  description?: string;
};

export type ChoiceBoxData = {
  id: string;
  type: "choice" | "multi_choice" | "confirm" | "edit";
  source_agent: string;
  question: string;
  options?: ChoiceBoxOption[];
  context?: string;
  multi?: boolean;
  default_value?: string | null;
  edit_placeholder?: string;
  timeout_seconds?: number | null;
  status: "pending" | "responded" | "timed_out" | "cancelled";
  response_value?: string | null;
  responded_at?: string | null;
  created_at?: string;
};

type ChoiceBoxProps = {
  data: ChoiceBoxData;
  onRespond: (boxId: string, value: string) => void;
  onCancel?: (boxId: string) => void;
  disabled?: boolean;
  style?: CSSProperties;
};

const BOX_STYLES = {
  container: {
    border: "1px solid #e2e8f0",
    borderRadius: "12px",
    padding: "16px",
    margin: "8px 0",
    background: "linear-gradient(135deg, #f8fafc 0%, #f1f5f9 100%)",
    maxWidth: "480px",
    boxShadow: "0 1px 3px rgba(0,0,0,0.06)",
  } as CSSProperties,
  containerResponded: {
    opacity: 0.7,
    background: "#f8fafc",
  } as CSSProperties,
  context: {
    fontSize: "12px",
    color: "#64748b",
    marginBottom: "6px",
    fontWeight: 500,
  } as CSSProperties,
  question: {
    fontSize: "14px",
    color: "#1e293b",
    marginBottom: "12px",
    fontWeight: 600,
    lineHeight: 1.5,
  } as CSSProperties,
  optionBtn: {
    display: "flex",
    alignItems: "center",
    width: "100%",
    padding: "10px 14px",
    margin: "4px 0",
    border: "1px solid #e2e8f0",
    borderRadius: "8px",
    background: "#fff",
    cursor: "pointer",
    textAlign: "left" as const,
    transition: "all 0.15s ease",
    fontSize: "13px",
    color: "#334155",
  } as CSSProperties,
  optionBtnHover: {
    borderColor: "#3b82f6",
    background: "#eff6ff",
    boxShadow: "0 0 0 1px #3b82f6",
  } as CSSProperties,
  optionBtnSelected: {
    borderColor: "#3b82f6",
    background: "#dbeafe",
    color: "#1d4ed8",
    fontWeight: 600,
  } as CSSProperties,
  optionLabel: {
    flex: 1,
  } as CSSProperties,
  optionDesc: {
    fontSize: "11px",
    color: "#94a3b8",
    marginTop: "2px",
  } as CSSProperties,
  editArea: {
    width: "100%",
    minHeight: "80px",
    padding: "10px",
    border: "1px solid #e2e8f0",
    borderRadius: "8px",
    fontSize: "13px",
    fontFamily: "inherit",
    resize: "vertical" as const,
    outline: "none",
    transition: "border-color 0.15s",
  } as CSSProperties,
  confirmRow: {
    display: "flex",
    gap: "8px",
    marginTop: "8px",
  } as CSSProperties,
  confirmBtn: {
    flex: 1,
    padding: "10px 16px",
    border: "1px solid #e2e8f0",
    borderRadius: "8px",
    cursor: "pointer",
    fontSize: "13px",
    fontWeight: 600,
    transition: "all 0.15s ease",
  } as CSSProperties,
  confirmBtnPrimary: {
    background: "#3b82f6",
    color: "#fff",
    borderColor: "#3b82f6",
  } as CSSProperties,
  confirmBtnSecondary: {
    background: "#fff",
    color: "#64748b",
  } as CSSProperties,
  respondedBadge: {
    display: "inline-flex",
    alignItems: "center",
    gap: "4px",
    padding: "4px 10px",
    background: "#dcfce7",
    color: "#166534",
    borderRadius: "6px",
    fontSize: "12px",
    fontWeight: 500,
    marginTop: "8px",
  } as CSSProperties,
  agentBadge: {
    display: "inline-block",
    padding: "2px 8px",
    background: "#ede9fe",
    color: "#6d28d9",
    borderRadius: "4px",
    fontSize: "11px",
    fontWeight: 600,
    marginBottom: "6px",
  } as CSSProperties,
} as const;

/** Single-select or multi-select choice box */
function ChoiceSelect({
  data,
  onRespond,
  disabled,
}: {
  data: ChoiceBoxData;
  onRespond: (boxId: string, value: string) => void;
  disabled?: boolean;
}) {
  const [selected, setSelected] = useState<string | null>(data.default_value || null);
  const [multiSelected, setMultiSelected] = useState<Set<string>>(new Set());
  const isMulti = data.type === "multi_choice" || data.multi;

  const handleOptionClick = useCallback(
    (optionId: string) => {
      if (disabled) return;
      if (isMulti) {
        setMultiSelected((prev) => {
          const next = new Set(prev);
          if (next.has(optionId)) next.delete(optionId);
          else next.add(optionId);
          return next;
        });
      } else {
        setSelected(optionId);
      }
    },
    [disabled, isMulti],
  );

  const handleConfirm = useCallback(() => {
    if (disabled) return;
    if (isMulti) {
      onRespond(data.id, Array.from(multiSelected).join(","));
    } else if (selected) {
      onRespond(data.id, selected);
    }
  }, [data.id, selected, multiSelected, isMulti, onRespond, disabled]);

  const options = data.options || [];
  const canConfirm = isMulti ? multiSelected.size > 0 : selected !== null;

  return (
    <div>
      {options.map((opt) => {
        const isSelected = isMulti ? multiSelected.has(opt.id) : selected === opt.id;
        return (
          <button
            key={opt.id}
            type="button"
            style={{
              ...BOX_STYLES.optionBtn,
              ...(isSelected ? BOX_STYLES.optionBtnSelected : {}),
            }}
            onClick={() => handleOptionClick(opt.id)}
            disabled={disabled}
            onMouseEnter={(e) => {
              if (!isSelected) Object.assign(e.currentTarget.style, BOX_STYLES.optionBtnHover);
            }}
            onMouseLeave={(e) => {
              if (!isSelected) {
                e.currentTarget.style.borderColor = "#e2e8f0";
                e.currentTarget.style.background = "#fff";
                e.currentTarget.style.boxShadow = "none";
              }
            }}
          >
            <span style={BOX_STYLES.optionLabel}>
              {opt.label}
              {opt.description && (
                <div style={BOX_STYLES.optionDesc}>{opt.description}</div>
              )}
            </span>
            {isSelected && <span>✓</span>}
          </button>
        );
      })}
      <div style={BOX_STYLES.confirmRow}>
        <button
          type="button"
          style={{
            ...BOX_STYLES.confirmBtn,
            ...(canConfirm ? BOX_STYLES.confirmBtnPrimary : BOX_STYLES.confirmBtnSecondary),
            opacity: canConfirm ? 1 : 0.5,
          }}
          onClick={handleConfirm}
          disabled={disabled || !canConfirm}
        >
          确认选择
        </button>
      </div>
    </div>
  );
}

/** Confirm/cancel choice box */
function ChoiceConfirm({
  data,
  onRespond,
  disabled,
}: {
  data: ChoiceBoxData;
  onRespond: (boxId: string, value: string) => void;
  disabled?: boolean;
}) {
  return (
    <div style={BOX_STYLES.confirmRow}>
      <button
        type="button"
        style={{ ...BOX_STYLES.confirmBtn, ...BOX_STYLES.confirmBtnPrimary }}
        onClick={() => onRespond(data.id, "confirm")}
        disabled={disabled}
      >
        ✅ 确认
      </button>
      <button
        type="button"
        style={{ ...BOX_STYLES.confirmBtn, ...BOX_STYLES.confirmBtnSecondary }}
        onClick={() => onRespond(data.id, "cancel")}
        disabled={disabled}
      >
        ❌ 取消
      </button>
    </div>
  );
}

/** Edit text choice box */
function ChoiceEdit({
  data,
  onRespond,
  disabled,
}: {
  data: ChoiceBoxData;
  onRespond: (boxId: string, value: string) => void;
  disabled?: boolean;
}) {
  const [text, setText] = useState(data.default_value || "");

  return (
    <div>
      <textarea
        style={BOX_STYLES.editArea}
        placeholder={data.edit_placeholder || "输入内容..."}
        value={text}
        onChange={(e) => setText(e.target.value)}
        disabled={disabled}
        onFocus={(e) => {
          e.currentTarget.style.borderColor = "#3b82f6";
        }}
        onBlur={(e) => {
          e.currentTarget.style.borderColor = "#e2e8f0";
        }}
      />
      <div style={BOX_STYLES.confirmRow}>
        <button
          type="button"
          style={{
            ...BOX_STYLES.confirmBtn,
            ...(text.trim() ? BOX_STYLES.confirmBtnPrimary : BOX_STYLES.confirmBtnSecondary),
            opacity: text.trim() ? 1 : 0.5,
          }}
          onClick={() => onRespond(data.id, text)}
          disabled={disabled || !text.trim()}
        >
          提交
        </button>
      </div>
    </div>
  );
}

/** Main Choice Box component */
export const ChoiceBox = memo(function ChoiceBox({
  data,
  onRespond,
  onCancel,
  disabled = false,
  style,
}: ChoiceBoxProps) {
  const isResponded = data.status === "responded";
  const isCancelled = data.status === "cancelled";
  const isDone = isResponded || isCancelled;

  return (
    <div
      style={{
        ...BOX_STYLES.container,
        ...(isDone ? BOX_STYLES.containerResponded : {}),
        ...style,
      }}
    >
      {/* Agent badge */}
      {data.source_agent && (
        <div style={BOX_STYLES.agentBadge}>{data.source_agent}</div>
      )}

      {/* Context */}
      {data.context && <div style={BOX_STYLES.context}>{data.context}</div>}

      {/* Question */}
      <div style={BOX_STYLES.question}>{data.question}</div>

      {/* Options / Input */}
      {!isDone && (
        <>
          {(data.type === "choice" || data.type === "multi_choice") && (
            <ChoiceSelect data={data} onRespond={onRespond} disabled={disabled} />
          )}
          {data.type === "confirm" && (
            <ChoiceConfirm data={data} onRespond={onRespond} disabled={disabled} />
          )}
          {data.type === "edit" && (
            <ChoiceEdit data={data} onRespond={onRespond} disabled={disabled} />
          )}
        </>
      )}

      {/* Response badge */}
      {isResponded && (
        <div style={BOX_STYLES.respondedBadge}>
          ✅ 已选择: {data.response_value}
        </div>
      )}
      {isCancelled && (
        <div style={{ ...BOX_STYLES.respondedBadge, background: "#fee2e2", color: "#991b1b" }}>
          ❌ 已取消
        </div>
      )}
    </div>
  );
});

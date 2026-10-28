import { useEffect, useRef, type FormEvent } from "react";
import { ArrowUp, ChevronDown, Square, Sparkles } from "lucide-react";
import type { Mode } from "../lib/types";
export const modeNames: Record<Mode, string> = {
  dsh: "DSH Agent",
  sse: "自定义 Agent",
  demo: "演示 Agent",
};
interface Props {
  value: string;
  setValue: (value: string) => void;
  onSend: () => void;
  onStop: () => void;
  busy: boolean;
  stopping: boolean;
  mode: Mode;
  onSettings: () => void;
}
export function Composer({
  value,
  setValue,
  onSend,
  onStop,
  busy,
  stopping,
  mode,
  onSettings,
}: Props) {
  const textarea = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const el = textarea.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
    }
  }, [value]);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!busy && value.trim()) onSend();
  };
  return (
    <form className="composer" onSubmit={submit}>
      <textarea
        ref={textarea}
        rows={2}
        aria-label="消息"
        placeholder="向 Agent 提问，或者描述你想做的事…"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (
            e.key === "Enter" &&
            !e.shiftKey &&
            !e.nativeEvent.isComposing &&
            e.keyCode !== 229
          ) {
            e.preventDefault();
            if (!busy && value.trim()) onSend();
          }
        }}
      />
      <div className="composer-toolbar">
        <button
          className="agent-select"
          type="button"
          onClick={onSettings}
          disabled={busy}
        >
          <Sparkles size={15} />
          {modeNames[mode]}
          <ChevronDown size={13} />
        </button>
        <div className="composer-right">
          <span className="keyboard-hint">
            {busy ? "正在生成回复" : "Enter 发送"}
          </span>
          {busy ? (
            <button
              className="send-button stop-button"
              type="button"
              onClick={onStop}
              aria-label="停止生成"
              disabled={stopping}
            >
              <Square size={14} fill="currentColor" />
            </button>
          ) : (
            <button
              className="send-button"
              type="submit"
              disabled={!value.trim()}
              aria-label="发送消息"
            >
              <ArrowUp size={20} />
            </button>
          )}
        </div>
      </div>
    </form>
  );
}

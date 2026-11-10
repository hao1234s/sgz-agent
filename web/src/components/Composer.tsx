import { useEffect, useRef, type FormEvent } from "react";
import { ArrowUp, Square, Sparkles } from "lucide-react";
interface Props {
  value: string;
  setValue: (value: string) => void;
  onSend: () => void;
  onStop: () => void;
  busy: boolean;
  stopping: boolean;
  disabled?: boolean;
  onAccount: () => void;
}
export function Composer({
  value,
  setValue,
  onSend,
  onStop,
  busy,
  stopping,
  disabled,
  onAccount,
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
    if (!busy && !disabled && value.trim()) onSend();
  };
  return (
    <form className="composer" onSubmit={submit}>
      <textarea
        ref={textarea}
        rows={2}
        aria-label="消息"
        maxLength={32000}
        placeholder="向 Agent 提问，或者描述你想做的事…"
        value={value}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          if (
            event.key === "Enter" &&
            !event.shiftKey &&
            !event.nativeEvent.isComposing &&
            event.keyCode !== 229
          ) {
            event.preventDefault();
            if (!busy && !disabled && value.trim()) onSend();
          }
        }}
      />
      <div className="composer-toolbar">
        <button className="agent-select" type="button" onClick={onAccount}>
          <Sparkles size={15} />
          SGZ Agent
        </button>
        <div className="composer-right">
          <span className="keyboard-hint">
            {stopping ? "正在停止…" : busy ? "正在生成回复" : "Enter 发送"}
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
              disabled={disabled || !value.trim()}
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

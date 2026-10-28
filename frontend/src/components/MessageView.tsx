import { useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Asterisk, Check, Copy, LoaderCircle } from "lucide-react";
import type { Message } from "../lib/types";

export function MessageView({ message }: { message: Message }) {
  const [copyState, setCopyState] = useState<"idle" | "copied" | "error">(
    "idle",
  );
  async function copy() {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopyState("copied");
    } catch {
      setCopyState("error");
    }
    setTimeout(() => setCopyState("idle"), 2000);
  }
  if (message.role === "user")
    return (
      <article className="user-message" aria-label="你的消息">
        <div>{message.content}</div>
      </article>
    );
  return (
    <article
      className="assistant-message"
      aria-label="Agent 回复"
      aria-busy={message.status === "streaming"}
    >
      <div className="assistant-heading">
        <span className="mini-mark">
          <Asterisk size={19} />
        </span>
        <strong>Agent</strong>
        {message.status === "streaming" && (
          <span className="stream-status">
            <LoaderCircle size={12} className="spin" />
            {message.activity || "正在思考"}
          </span>
        )}
      </div>
      {message.content ? (
        <div
          className={`markdown ${message.status === "streaming" ? "is-streaming" : ""}`}
        >
          <Markdown
            remarkPlugins={[remarkGfm]}
            components={{
              a: (props) => (
                <a {...props} target="_blank" rel="noopener noreferrer" />
              ),
              img: ({ alt }) => (
                <span className="image-placeholder">
                  [图片：{alt || "图片内容"}]
                </span>
              ),
            }}
          >
            {message.content}
          </Markdown>
        </div>
      ) : message.status === "streaming" ? (
        <div className="thinking-dots" aria-label="正在等待回复">
          <i />
          <i />
          <i />
        </div>
      ) : null}
      {message.error && (
        <div className="message-error" role="alert">
          {message.error}
        </div>
      )}
      <div className="message-actions">
        {message.content && (
          <button className="copy-button" onClick={copy} aria-label="复制回复">
            {copyState === "copied" ? <Check size={14} /> : <Copy size={14} />}
            <span>
              {copyState === "copied"
                ? "已复制"
                : copyState === "error"
                  ? "复制失败，请手动选择"
                  : "复制"}
            </span>
          </button>
        )}
        {message.status === "stopped" && (
          <span className="muted">已停止生成</span>
        )}
      </div>
    </article>
  );
}

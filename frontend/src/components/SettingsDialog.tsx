import { useEffect, useRef, useState } from "react";
import {
  ArrowUpRight,
  Check,
  ChevronRight,
  FlaskConical,
  LoaderCircle,
  Plug,
  Radio,
  X,
} from "lucide-react";
import { authenticateDsh, dshRpc } from "../lib/dsh";
import type { Mode, Settings } from "../lib/types";

interface Props {
  settings: Settings;
  token: string;
  onClose: () => void;
  onSave: (settings: Settings, token: string, connected: boolean) => void;
}
const modes: {
  value: Mode;
  title: string;
  description: string;
  icon: typeof Plug;
}[] = [
  {
    value: "dsh",
    title: "DSH Agent",
    description: "连接当前项目的 Agent",
    icon: Plug,
  },
  {
    value: "sse",
    title: "自定义接口",
    description: "连接 HTTP 流式服务",
    icon: Radio,
  },
  {
    value: "demo",
    title: "体验演示",
    description: "试试本地流式对话",
    icon: FlaskConical,
  },
];
export function SettingsDialog({ settings, token, onClose, onSave }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [draft, setDraft] = useState(settings);
  const [secret, setSecret] = useState(settings.mode === "sse" ? token : "");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  async function save() {
    setError("");
    setBusy(true);
    try {
      if (draft.mode === "dsh") {
        if (!draft.cwd.trim()) throw new Error("请填写 Agent 工作目录。");
        if (secret) await authenticateDsh(secret);
        await dshRpc("session/list", {}, AbortSignal.timeout(10_000));
      }
      if (draft.mode === "sse") {
        if (!draft.endpoint.trim()) throw new Error("请填写流式接口地址。");
        const url = new URL(draft.endpoint, location.href);
        if (!["http:", "https:"].includes(url.protocol))
          throw new Error("接口地址必须是 HTTP 或 HTTPS。");
      }
      onSave(
        {
          ...draft,
          endpoint: draft.endpoint.trim(),
          cwd: draft.cwd.trim(),
          model: draft.model.trim(),
        },
        draft.mode === "sse" ? secret : "",
        draft.mode === "dsh",
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "连接失败，请稍后再试。");
    } finally {
      setBusy(false);
    }
  }
  return (
    <dialog
      ref={dialog}
      className="settings-dialog"
      onCancel={(e) => {
        e.preventDefault();
        if (!busy) onClose();
      }}
      onClick={(e) => {
        if (e.target === dialog.current && !busy) onClose();
      }}
      aria-labelledby="settings-title"
    >
      <div className="dialog-body">
        <div className="dialog-heading">
          <div>
            <span className="eyebrow">让对话连接起来</span>
            <h2 id="settings-title">连接你的 Agent</h2>
          </div>
          <button
            className="icon-button"
            aria-label="关闭设置"
            onClick={onClose}
            disabled={busy}
          >
            <X size={20} />
          </button>
        </div>
        <div className="connection-options" role="group" aria-label="连接方式">
          {modes.map(({ value, title, description, icon: Icon }) => (
            <button
              key={value}
              className={`connection-option ${draft.mode === value ? "selected" : ""}`}
              onClick={() => {
                setDraft({ ...draft, mode: value });
                setSecret("");
                setError("");
              }}
              disabled={busy}
            >
              <Icon size={20} />
              <span>
                <strong>{title}</strong>
                <small>{description}</small>
              </span>
              {draft.mode === value ? (
                <Check size={17} />
              ) : (
                <ChevronRight size={17} />
              )}
            </button>
          ))}
        </div>
        {draft.mode === "dsh" && (
          <div className="settings-fields">
            <label>
              工作目录
              <input
                value={draft.cwd}
                onChange={(e) => setDraft({ ...draft, cwd: e.target.value })}
                spellCheck={false}
              />
            </label>
            <label>
              DSH 连接 token
              <input
                type="password"
                autoComplete="off"
                value={secret}
                placeholder="粘贴 token 或完整的 DSH 启动链接"
                onChange={(e) => setSecret(e.target.value)}
              />
            </label>
            <p className="field-help">
              已认证时可留空。代理地址在 <code>frontend/.env.local</code>{" "}
              中配置；DSH 重启后请确认端口。连接 token 仅用于本次认证。
            </p>
            <details>
              <summary>如何获取连接信息？</summary>
              <p>
                使用 DSH 提供的浏览器启动链接，复制其中的
                token。将同一服务的地址设为 <code>DSH_BACKEND_URL</code>{" "}
                并重启前端。使用包含本项目插件的 DSH
                实例，避免同时启动第二个游戏 Worker。
              </p>
              <p>需要批准工具调用时，请回到 DSH 完成批准。</p>
            </details>
          </div>
        )}
        {draft.mode === "sse" && (
          <div className="settings-fields">
            <label>
              流式接口地址
              <input
                value={draft.endpoint}
                placeholder="/agent-api/chat/stream"
                onChange={(e) =>
                  setDraft({ ...draft, endpoint: e.target.value })
                }
                spellCheck={false}
              />
            </label>
            <div className="form-row">
              <label>
                模型 <span>可选</span>
                <input
                  value={draft.model}
                  placeholder="使用后端默认模型"
                  onChange={(e) =>
                    setDraft({ ...draft, model: e.target.value })
                  }
                />
              </label>
              <label>
                Bearer token <span>可选</span>
                <input
                  type="password"
                  autoComplete="off"
                  value={secret}
                  onChange={(e) => setSecret(e.target.value)}
                  placeholder="仅保存在内存中"
                />
              </label>
            </div>
            <p className="field-help">
              以 POST 发送消息，接收 SSE
              回复。相对地址通过本地代理访问；绝对地址需要后端允许跨域。设置保存后将在首次发送时验证连接。
            </p>
          </div>
        )}
        {draft.mode === "demo" && (
          <div className="demo-explanation">
            <FlaskConical size={28} />
            <h3>先感受一次对话</h3>
            <p>
              无需配置，体验逐字回复、Markdown
              和停止生成。演示内容在本地生成，不连接模型或执行工具。
            </p>
          </div>
        )}
        {error && (
          <p role="alert" className="form-error">
            {error}
          </p>
        )}
        <div className="dialog-footer">
          <span>更换连接后将开始新会话</span>
          <button className="primary-button" onClick={save} disabled={busy}>
            {busy ? <LoaderCircle size={16} className="spin" /> : null}
            {busy
              ? "正在连接"
              : draft.mode === "dsh"
                ? "连接 Agent"
                : "保存设置"}
            {!busy && <ArrowUpRight size={16} />}
          </button>
        </div>
      </div>
    </dialog>
  );
}

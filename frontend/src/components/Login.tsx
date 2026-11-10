import { useEffect, useRef, useState, type FormEvent } from "react";
import { Asterisk, ArrowUpRight } from "lucide-react";
import { api, ApiError, errorText, type User } from "../lib/api";

export function Login({
  onLogin,
  notice = "",
  blocked = false,
}: {
  onLogin: (user: User) => void;
  notice?: string;
  blocked?: boolean;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const pending = useRef<AbortController | null>(null);
  useEffect(() => () => pending.current?.abort(), []);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (pending.current || blocked) return;
    const controller = new AbortController();
    pending.current = controller;
    setBusy(true);
    setError("");
    try {
      const result = await api.login(
        username.trim(),
        password,
        controller.signal,
      );
      if (!controller.signal.aborted) {
        setPassword("");
        onLogin(result.user);
      }
    } catch (error) {
      if (!controller.signal.aborted)
        setError(
          error instanceof ApiError && error.status === 401
            ? "用户名或密码不正确，或账户已被停用。"
            : errorText(error),
        );
    } finally {
      if (!controller.signal.aborted) {
        pending.current = null;
        setBusy(false);
      }
    }
  }
  return (
    <main className="login-page">
      <div className="login-brand brand">
        <Asterisk size={31} />
        <span>
          Agent<span className="brand-period">.</span>
        </span>
      </div>
      <section className="login-card">
        <div className="welcome-label">
          <span className="tiny-star">✳</span> 属于你的对话空间
        </div>
        <h1>欢迎回来。</h1>
        <p>登录账户，继续与 Agent 探索。</p>
        <form className="settings-fields login-form" onSubmit={submit}>
          <label>
            <span>用户名</span>
            <input
              autoComplete="username"
              required
              minLength={3}
              maxLength={64}
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              disabled={busy || blocked}
              autoFocus
            />
          </label>
          <label>
            <span>密码</span>
            <input
              type="password"
              autoComplete="current-password"
              required
              minLength={10}
              maxLength={1024}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={busy || blocked}
            />
          </label>
          {(error || notice) && (
            <div className="message-error" role="alert">
              {error || notice}
            </div>
          )}
          <button
            className="primary-button"
            disabled={busy || blocked}
            type="submit"
          >
            {busy ? "正在登录…" : blocked ? "正在退出…" : "登录"}
            <ArrowUpRight size={16} />
          </button>
        </form>
        <p className="login-help">
          账户由管理员创建。如需访问或重置密码，请联系管理员。
        </p>
      </section>
      <span className="login-footer">把问题聊清楚，把事情做成。</span>
    </main>
  );
}

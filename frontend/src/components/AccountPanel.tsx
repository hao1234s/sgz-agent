import { useEffect, useLayoutEffect, useRef, useState, type FormEvent } from "react";
import { LogOut, X } from "lucide-react";
import { api, ApiError, errorText, type User } from "../lib/api";

export function AccountPanel({
  user,
  suspended,
  onClose,
  onLogout,
  onExpired,
}: {
  user: User;
  suspended: boolean;
  onClose: () => void;
  onLogout: () => void;
  onExpired: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const life = useRef<AbortController | null>(null);
  const [users, setUsers] = useState<User[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<User["role"]>("user");
  const [resetId, setResetId] = useState<string | null>(null);
  const [resetPassword, setResetPassword] = useState("");
  function failure(error: unknown, signal: AbortSignal) {
    if (signal.aborted) return;
    if (error instanceof ApiError && error.status === 401) onExpired();
    else setError(errorText(error));
  }
  useLayoutEffect(() => {
    const element = dialog.current;
    if (!element) return;
    const close = () => {
      if (!element.open) return;
      if (typeof element.close === "function") element.close();
      else element.removeAttribute("open");
    };
    if (suspended) close();
    else if (!element.open) {
      if (typeof element.showModal === "function") element.showModal();
      else element.setAttribute("open", "");
    }
    // A hidden ancestor does not release a native modal's top-layer inertness.
    return close;
  }, [suspended]);
  useEffect(() => {
    const controller = new AbortController();
    life.current = controller;
    if (user.role === "admin")
      void api
        .users(controller.signal)
        .then(({ users }) => {
          if (!controller.signal.aborted) setUsers(users);
        })
        .catch((error) => failure(error, controller.signal));
    return () => controller.abort();
  }, []);
  async function mutate(
    action: (signal: AbortSignal) => Promise<{ user: User }>,
    success: string,
  ) {
    if (busy || !life.current) return;
    const signal = life.current.signal;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await action(signal);
      if (signal.aborted) return;
      if (result.user.id === user.id && resetId === user.id) {
        onExpired();
        return;
      }
      setUsers((users) =>
        users.some((item) => item.id === result.user.id)
          ? users.map((item) =>
              item.id === result.user.id ? result.user : item,
            )
          : [...users, result.user],
      );
      setNotice(success);
      setPassword("");
      setUsername("");
      setResetId(null);
      setResetPassword("");
    } catch (error) {
      failure(error, signal);
    } finally {
      if (!signal.aborted) setBusy(false);
    }
  }
  function create(event: FormEvent) {
    event.preventDefault();
    void mutate(
      (signal) => api.createUser(username.trim(), password, role, signal),
      "账户已创建，请将登录信息安全地交给使用者。",
    );
  }
  return (
    <dialog
      ref={dialog}
      className="settings-dialog account-dialog"
      aria-labelledby="account-title"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        if (event.target === dialog.current) onClose();
      }}
    >
      <div className="dialog-body">
        <div className="dialog-heading">
          <div>
            <h2 id="account-title">账户与管理</h2>
            <p>
              {user.username} · {user.role === "admin" ? "管理员" : "成员"}
            </p>
          </div>
          <button
            className="icon-button"
            aria-label="关闭账户面板"
            onClick={onClose}
          >
            <X size={19} />
          </button>
        </div>
        {error && (
          <div className="message-error" role="alert">
            {error}
          </div>
        )}
        {notice && (
          <p className="account-notice" role="status">
            {notice}
          </p>
        )}
        {user.role === "admin" && (
          <>
            <h3 className="account-section-title">账户管理</h3>
            <div className="account-list">
              {users.map((item) => (
                <div className="account-row" key={item.id}>
                  <div>
                    <strong>{item.username}</strong>
                    <span>
                      {item.role === "admin" ? "管理员" : "成员"} ·{" "}
                      {item.disabled ? "已停用" : "正常"}
                    </span>
                  </div>
                  <button
                    disabled={busy || item.id === user.id}
                    onClick={() =>
                      void mutate(
                        (signal) =>
                          api.updateUser(
                            item.id,
                            { disabled: !item.disabled },
                            signal,
                          ),
                        item.disabled
                          ? "账户已启用。"
                          : "账户已停用，登录会话已撤销。",
                      )
                    }
                  >
                    {item.disabled ? "启用" : "停用"}
                  </button>
                  <button
                    disabled={busy}
                    onClick={() => {
                      setResetId(item.id);
                      setResetPassword("");
                      setError("");
                    }}
                  >
                    重置密码<span className="sr-only">：{item.username}</span>
                  </button>
                </div>
              ))}
            </div>
            {resetId && (
              <form
                className="settings-fields reset-form"
                onSubmit={(event) => {
                  event.preventDefault();
                  void mutate(
                    (signal) =>
                      api.updateUser(
                        resetId,
                        { password: resetPassword },
                        signal,
                      ),
                    "密码已重置，原有登录会话已撤销。",
                  );
                }}
              >
                <label>
                  <span>
                    新密码（
                    {users.find((item) => item.id === resetId)?.username}）
                  </span>
                  <input
                    aria-label="新密码"
                    type="password"
                    autoComplete="new-password"
                    required
                    minLength={10}
                    maxLength={1024}
                    value={resetPassword}
                    onChange={(event) => setResetPassword(event.target.value)}
                    disabled={busy}
                  />
                </label>
                <p className="field-help">
                  至少 10 位。重置后该账户需要重新登录。
                </p>
                <div className="account-form-actions">
                  <button
                    type="button"
                    onClick={() => {
                      setResetId(null);
                      setResetPassword("");
                    }}
                  >
                    取消
                  </button>
                  <button className="primary-button" disabled={busy}>
                    确认重置
                  </button>
                </div>
              </form>
            )}
            <h3 className="account-section-title">创建账户</h3>
            <form className="settings-fields account-create" onSubmit={create}>
              <label>
                <span>新账户用户名</span>
                <input
                  required
                  minLength={3}
                  maxLength={64}
                  autoComplete="off"
                  pattern="[A-Za-z0-9_.-]+"
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  disabled={busy}
                />
              </label>
              <label>
                <span>初始密码</span>
                <input
                  type="password"
                  autoComplete="new-password"
                  required
                  minLength={10}
                  maxLength={1024}
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  disabled={busy}
                />
              </label>
              <label>
                <span>角色</span>
                <select
                  value={role}
                  onChange={(event) =>
                    setRole(event.target.value as User["role"])
                  }
                  disabled={busy}
                >
                  <option value="user">成员</option>
                  <option value="admin">管理员</option>
                </select>
              </label>
              <p className="field-help">
                用户名为 3–64 位字母、数字、下划线、点或短横线。密码至少 10 位。
              </p>
              <button className="primary-button" disabled={busy}>
                创建账户
              </button>
            </form>
          </>
        )}
        <div className="account-logout">
          <p>会话保存在服务器，重新登录后可继续查看。</p>
          <button onClick={onLogout}>
            <LogOut size={16} />
            退出登录
          </button>
        </div>
      </div>
    </dialog>
  );
}

import { useEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { sessionEvents } from "./lib/session-events";
import {
  ArrowDown,
  ArrowUpRight,
  Asterisk,
  BookOpen,
  Check,
  ChevronRight,
  Compass,
  FileText,
  MessageSquare,
  PanelLeftClose,
  PanelLeftOpen,
  PenLine,
  Plus,
  Search,
  Settings2,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import { Composer } from "./components/Composer";
import { MessageView } from "./components/MessageView";
import { Login } from "./components/Login";
import { AccountPanel } from "./components/AccountPanel";
import {
  api,
  ApiError,
  errorText,
  type ConversationSummary,
  type ServerConversation,
  type ServerStatus,
  type User,
} from "./lib/api";

const prompts = [
  {
    icon: Compass,
    title: "了解 Agent 能力",
    detail: "看看能帮你做些什么",
    text: "请介绍你能提供的三国志战略版分析与计算能力。",
  },
  {
    icon: BookOpen,
    title: "探索阵容搭配",
    detail: "找到适合你的组合",
    text: "我想了解如何搭配一套阵容，请先问我已有的武将和战法。",
  },
  {
    icon: PenLine,
    title: "制定战斗计划",
    detail: "拆解目标，找到下一步",
    text: "我想制定一份阵容培养计划，请先了解我的目标和现有资源。",
  },
  {
    icon: FileText,
    title: "分析战法效果",
    detail: "把复杂机制聊清楚",
    text: "请帮我了解战法分析与伤害计算需要提供哪些信息。",
  },
];

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [checking, setChecking] = useState(true);
  const [loggingOut, setLoggingOut] = useState(false);
  const [notice, setNotice] = useState("");
  const identity = useRef<AbortController | null>(null);
  const version = useRef(0);
  const events = useRef<ReturnType<typeof sessionEvents> | null>(null);
  function invalidate(clearWorkspace = true) {
    version.current++;
    identity.current?.abort();
    if (clearWorkspace) setUser(null);
    setChecking(true);
    setNotice("");
  }
  async function revalidate(preserveWorkspace = false) {
    invalidate(!preserveWorkspace);
    const current = version.current;
    const controller = new AbortController();
    identity.current = controller;
    const valid = () => !controller.signal.aborted && current === version.current;
    try {
      const result = await api.me(controller.signal);
      if (valid()) {
        setUser(result.user);
        setChecking(false);
      }
    } catch (error) {
      if (!valid()) return;
      if (error instanceof ApiError && error.status === 401) {
        setUser(null);
        setChecking(false);
      } else {
        // Keep retained content concealed until the cookie identity is confirmed.
        setNotice(errorText(error));
      }
    }
  }
  useEffect(() => {
    const bus = sessionEvents(phase => {
      // Commit the unmount (including abort cleanup) before starting another identity request.
      flushSync(() => invalidate());
      if (phase === "changed") void revalidate();
    });
    events.current = bus;
    const focus = () => {
      // Conceal synchronously, but keep the existing workspace lifetime during checks.
      flushSync(() => invalidate(false));
      void revalidate(true);
    };
    const visible = () => { if (document.visibilityState === "visible") focus(); };
    window.addEventListener("focus", focus);
    document.addEventListener("visibilitychange", visible);
    void revalidate();
    return () => {
      version.current++;
      identity.current?.abort();
      bus.close();
      window.removeEventListener("focus", focus);
      document.removeEventListener("visibilitychange", visible);
    };
  }, []);
  function expired() {
    version.current++;
    identity.current?.abort();
    setUser(null);
    setChecking(false);
    setNotice("登录已过期，请重新登录。");
    events.current?.publish("changed");
  }
  async function logout() {
    invalidate();
    setChecking(false);
    setLoggingOut(true);
    events.current?.publish("changing");
    try {
      await api.logout();
    } catch (error) {
      if (!(error instanceof ApiError && error.status === 401)) setNotice(`退出请求失败：${errorText(error)}`);
    } finally {
      setLoggingOut(false);
      events.current?.publish("changed");
    }
  }
  const identityCheck = (
    <main className="login-page" role="status">
      {notice ? (
        <div>
          <p role="alert">无法确认登录状态：{notice}</p>
          <button onClick={() => void revalidate(true)}>重新检查</button>
        </div>
      ) : "正在恢复登录…"}
    </main>
  );
  if (checking && !user) return identityCheck;
  if (!user)
    return (
      <Login
        blocked={loggingOut}
        notice={notice}
        onLogin={(user) => {
          version.current++;
          identity.current?.abort();
          setNotice("");
          setUser(user);
          events.current?.publish("changed");
        }}
      />
    );
  return (
    <>
      {checking && identityCheck}
      <div hidden={checking} inert={checking}>
        <Workspace
          key={user.id}
          user={user}
          suspended={checking}
          onLogout={() => void logout()}
          onExpired={expired}
        />
      </div>
    </>
  );
}

function Workspace({
  user,
  suspended,
  onLogout,
  onExpired,
}: {
  user: User;
  suspended: boolean;
  onLogout: () => void;
  onExpired: () => void;
}) {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [active, setActive] = useState<ServerConversation | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [sidebar, setSidebar] = useState(() => window.innerWidth > 800);
  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [showAccount, setShowAccount] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [status, setStatus] = useState<ServerStatus | null>(null);
  const [notice, setNotice] = useState("");
  const [showJump, setShowJump] = useState(false);
  const lifetime = useRef<AbortController | null>(null);
  const controller = useRef<AbortController | null>(null);
  const sending = useRef(false);
  const cancelling = useRef(false);
  const detailVersion = useRef(0);
  const scroll = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const messages = active?.messages || [];
  const isEmpty = !messages.length;
  const running = busy || active?.status === "running";
  const connected = !!status?.ready;
  const statusLabel =
    status === null ? "检查服务" : status.ready ? "服务已就绪" : "服务未就绪";
  function failure(error: unknown, signal: AbortSignal) {
    if (signal.aborted) return;
    if (error instanceof ApiError && error.status === 401) onExpired();
    else setNotice(errorText(error));
  }
  function upsert(conversation: ServerConversation) {
    const { messages: _, ...summary } = conversation;
    setConversations((items) => [
      summary,
      ...items.filter((item) => item.id !== summary.id),
    ]);
  }
  async function refreshStatus() {
    const signal = lifetime.current?.signal;
    if (!signal || signal.aborted) return;
    try {
      const result = await api.status(signal);
      if (!signal.aborted) setStatus(result);
    } catch (error) {
      failure(error, signal);
    }
  }
  useEffect(() => {
    const scope = new AbortController();
    lifetime.current = scope;
    void api
      .conversations(scope.signal)
      .then(({ conversations }) => {
        if (!scope.signal.aborted) setConversations(conversations);
      })
      .catch((error) => failure(error, scope.signal));
    void api
      .status(scope.signal)
      .then((status) => {
        if (!scope.signal.aborted) setStatus(status);
      })
      .catch((error) => failure(error, scope.signal));
    return () => {
      scope.abort();
      controller.current?.abort();
    };
  }, []);
  useEffect(() => {
    if (follow.current && scroll.current)
      scroll.current.scrollTop = scroll.current.scrollHeight;
  }, [messages]);
  // A page reloaded during a run can follow its durable messages without creating a second run.
  useEffect(() => {
    if (!active || active.status !== "running" || busy) return;
    const scope = new AbortController();
    const id = active.id;
    const timer = setInterval(() => {
      void api
        .conversation(id, scope.signal)
        .then(({ conversation }) => {
          if (!scope.signal.aborted) {
            setActive(conversation);
            upsert(conversation);
          }
        })
        .catch((error) => failure(error, scope.signal));
    }, 1500);
    return () => {
      clearInterval(timer);
      scope.abort();
    };
  }, [active?.id, active?.status, busy]);
  function newChat() {
    if (sending.current || running || cancelling.current) return;
    detailVersion.current++;
    setActiveId(null);
    setActive(null);
    setLoading(false);
    setDraft("");
    setNotice("");
    follow.current = true;
    setShowJump(false);
    if (window.innerWidth <= 800) setSidebar(false);
  }
  async function select(id: string) {
    if (sending.current || running || cancelling.current) return;
    const signal = lifetime.current?.signal;
    if (!signal || signal.aborted) return;
    const version = ++detailVersion.current;
    setActiveId(id);
    setActive(null);
    setLoading(true);
    setDraft("");
    setNotice("");
    follow.current = true;
    if (window.innerWidth <= 800) setSidebar(false);
    try {
      const { conversation } = await api.conversation(id, signal);
      if (!signal.aborted && version === detailVersion.current)
        setActive(conversation);
    } catch (error) {
      if (version === detailVersion.current) failure(error, signal);
    } finally {
      if (!signal.aborted && version === detailVersion.current)
        setLoading(false);
    }
  }
  async function deleteConversation(id: string) {
    if (sending.current || running || cancelling.current) return;
    const signal = lifetime.current?.signal;
    if (!signal || signal.aborted) return;
    try {
      await api.remove(id, signal);
      if (!signal.aborted) {
        setConversations((items) => items.filter((item) => item.id !== id));
        if (activeId === id) newChat();
      }
    } catch (error) {
      failure(error, signal);
    }
  }
  async function send() {
    const text = draft.trim();
    const scope = lifetime.current;
    if (
      !scope ||
      scope.signal.aborted ||
      !text ||
      sending.current ||
      running ||
      cancelling.current ||
      loading ||
      !connected
    )
      return;
    const abort = new AbortController();
    controller.current = abort;
    sending.current = true;
    const onAbort = () => abort.abort();
    scope.signal.addEventListener("abort", onAbort, { once: true });
    setBusy(true);
    setStopping(false);
    setNotice("");
    follow.current = true;
    let target = active;
    let assistantId = "";
    try {
      if (!target) {
        target = (await api.create(abort.signal)).conversation;
        if (abort.signal.aborted) return;
        setActive(target);
        setActiveId(target.id);
        upsert(target);
      }
      const conversation = target;
      setDraft("");
      const final = await api.send(
        conversation.id,
        text,
        crypto.randomUUID(),
        abort.signal,
        (event) => {
          if (abort.signal.aborted || scope.signal.aborted) return;
          if (event.type === "run") {
            assistantId = event.assistantMessage.id;
            const next = {
              ...conversation,
              title: conversation.messages.length
                ? conversation.title
                : text.slice(0, 60),
              status: "running",
              updatedAt: Date.now(),
              messages: [
                ...conversation.messages,
                event.userMessage,
                event.assistantMessage,
              ],
            };
            setActive(next);
            upsert(next);
          } else if (event.type === "done") {
            setActive(event.conversation);
            upsert(event.conversation);
          } else if (
            event.type === "delta" ||
            event.type === "replace" ||
            event.type === "status"
          ) {
            setActive((current) =>
              current?.id !== conversation.id
                ? current
                : {
                    ...current,
                    messages: current.messages.map((message) =>
                      message.id !== assistantId
                        ? message
                        : event.type === "status"
                          ? { ...message, activity: event.text }
                          : {
                              ...message,
                              content:
                                event.type === "delta"
                                  ? message.content + event.text
                                  : event.text,
                            },
                    ),
                  },
            );
          }
        },
      );
      if (!scope.signal.aborted && !abort.signal.aborted) {
        setActive(final);
        upsert(final);
      }
    } catch (error) {
      if (scope.signal.aborted) return;
      if (!abort.signal.aborted) {
        failure(error, scope.signal);
        if (!assistantId) setDraft(text);
      }
      if (target) {
        try {
          const result = await api.conversation(target.id, scope.signal);
          if (!scope.signal.aborted) {
            setActive(result.conversation);
            upsert(result.conversation);
          }
        } catch (error) {
          failure(error, scope.signal);
        }
      }
    } finally {
      scope.signal.removeEventListener("abort", onAbort);
      if (!scope.signal.aborted) {
        controller.current = null;
        sending.current = false;
        setBusy(false);
      }
    }
  }
  async function stop() {
    const scope = lifetime.current;
    if (!active || !scope || scope.signal.aborted || cancelling.current) return;
    cancelling.current = true;
    setStopping(true);
    const stream = controller.current;
    const id = active.id;
    try {
      await api.cancel(id, scope.signal);
      if (scope.signal.aborted) return;
      stream?.abort();
      const { conversation } = await api.conversation(id, scope.signal);
      if (!scope.signal.aborted) {
        setActive(conversation);
        upsert(conversation);
        setNotice("已停止生成，收到的内容已保存。");
      }
    } catch (error) {
      if (!scope.signal.aborted) {
        stream?.abort();
        failure(error, scope.signal);
      }
    } finally {
      if (!scope.signal.aborted) {
        cancelling.current = false;
        setStopping(false);
      }
    }
  }
  const filtered = [...conversations]
    .sort((a, b) => b.updatedAt - a.updatedAt)
    .filter((c) => c.title.toLowerCase().includes(query.toLowerCase()));
  return (
    <div className={`app-shell ${sidebar ? "sidebar-open" : "sidebar-closed"}`}>
      <a href="#main-content" className="skip-link">
        跳至对话
      </a>
      {sidebar && (
        <button
          className="sidebar-overlay"
          onClick={() => setSidebar(false)}
          aria-label="关闭侧栏"
        />
      )}
      <aside className="sidebar" aria-label="会话导航" inert={!sidebar}>
        <div className="sidebar-brand">
          <button
            onClick={newChat}
            className="brand"
            aria-label="Agent 首页"
            disabled={!!running || stopping}
          >
            <Asterisk size={29} strokeWidth={2.1} />
            <span>
              Agent<span className="brand-period">.</span>
            </span>
          </button>
          <button
            className="icon-button sidebar-collapse"
            aria-label="收起侧栏"
            onClick={() => setSidebar(false)}
          >
            <PanelLeftClose size={18} />
          </button>
        </div>
        <button
          className="new-chat"
          onClick={newChat}
          disabled={!!running || stopping}
        >
          <Plus size={18} />
          <span>开启新会话</span>
          <span className="new-chat-symbol">↗</span>
        </button>
        <button
          className="sidebar-search"
          onClick={() => setSearching(!searching)}
        >
          <Search size={17} />
          <span>搜索会话</span>
        </button>
        {searching && (
          <div className="search-input">
            <input
              aria-label="搜索历史会话"
              placeholder="搜索会话标题…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              autoFocus
            />
            <button
              className="icon-button"
              aria-label="清除搜索"
              onClick={() => {
                setQuery("");
                setSearching(false);
              }}
            >
              <X size={14} />
            </button>
          </div>
        )}
        <div className="history-heading">
          你的会话
          <span>
            {conversations.length
              ? String(conversations.length).padStart(2, "0")
              : ""}
          </span>
        </div>
        <div className="conversation-list">
          {filtered.map((c) => (
            <div
              className={`conversation-row ${activeId === c.id ? "active" : ""}`}
              key={c.id}
            >
              <button
                className="conversation-link"
                disabled={!!running || stopping}
                onClick={() => void select(c.id)}
              >
                <MessageSquare size={15} />
                <span>{c.title}</span>
              </button>
              <button
                className="delete-conversation icon-button"
                disabled={!!running || stopping}
                aria-label={`删除会话：${c.title}`}
                onClick={() => deleteConversation(c.id)}
              >
                <Trash2 size={13} />
              </button>
            </div>
          ))}
          {!filtered.length && (
            <div className="history-empty">
              <MessageSquare size={21} strokeWidth={1.3} />
              <p>{query ? "没有找到相关会话" : "还没有会话"}</p>
              <span>{query ? "换一个关键词试试" : "好的想法，从对话开始"}</span>
            </div>
          )}
        </div>
        <div className="sidebar-bottom">
          <button
            className="sidebar-settings"
            aria-label="账户与管理"
            onClick={() => setShowAccount(true)}
          >
            <Settings2 size={17} />
            <span>账户与管理</span>
            <span className={`status-dot ${connected ? "online" : ""}`} />
          </button>
          <div className="workspace-profile">
            <div className="avatar">{user.username[0].toUpperCase()}</div>
            <div>
              <strong>{user.username}</strong>
              <span>{user.role === "admin" ? "管理员" : "个人工作空间"}</span>
            </div>
            <Check size={14} />
          </div>
        </div>
      </aside>
      <main id="main-content" className="main-panel">
        <header className="topbar">
          <div className="breadcrumb">
            {!sidebar && (
              <button
                className="icon-button"
                aria-label="展开侧栏"
                onClick={() => setSidebar(true)}
              >
                <PanelLeftOpen size={19} />
              </button>
            )}
            <span className="workspace-label">工作空间</span>
            <ChevronRight size={13} />
            <span className="current-title">{active?.title || "新会话"}</span>
          </div>
          <div className="topbar-actions">
            <button
              className="connection-badge"
              onClick={() => void refreshStatus()}
              aria-label="刷新服务状态"
            >
              <span className={`status-dot ${connected ? "online" : ""}`} />
              {statusLabel}
            </button>
          </div>
        </header>
        {notice && (
          <div className="notice" role="status">
            {notice}
            <button
              className="icon-button"
              aria-label="关闭提示"
              onClick={() => setNotice("")}
            >
              <X size={14} />
            </button>
          </div>
        )}
        {status && !status.ready && (
          <div className="notice model-notice" role="status">
            模型服务尚未就绪，请联系管理员配置模型后使用。
            <button onClick={() => void refreshStatus()}>重新检查</button>
          </div>
        )}
        {loading ? (
          <div className="workspace-loading" role="status">
            正在加载会话…
          </div>
        ) : isEmpty ? (
          <div className="welcome-scroll">
            <section className="welcome">
              <div className="welcome-intro">
                <div className="welcome-label">
                  <span className="tiny-star">✳</span> 为想法，打开新的可能
                </div>
                <h1>今天，想做点什么？</h1>
                <p>与 Agent 一起，把问题聊清楚，把事情做成。</p>
              </div>
              <Composer
                value={draft}
                setValue={setDraft}
                onSend={send}
                onStop={() => void stop()}
                busy={!!running}
                stopping={stopping}
                disabled={!connected || loading || stopping}
                onAccount={() => setShowAccount(true)}
              />
              <div className="starter-heading">
                <span>从这里开始</span>
                <span>一点灵感，无限可能</span>
              </div>
              <div className="starter-grid">
                {prompts.map(({ icon: Icon, title, detail, text }) => (
                  <button
                    className="starter-card"
                    key={title}
                    onClick={() => {
                      setDraft(text);
                      document
                        .querySelector<HTMLTextAreaElement>("textarea")
                        ?.focus();
                    }}
                  >
                    <div>
                      <Icon size={20} strokeWidth={1.5} />
                      <ArrowUpRight size={15} />
                    </div>
                    <strong>{title}</strong>
                    <span>{detail}</span>
                  </button>
                ))}
              </div>
              <div className="welcome-footnote">
                <Sparkles size={13} />
                <span>每一次对话，都是一个新起点。</span>
              </div>
            </section>
          </div>
        ) : (
          <>
            <div
              className="messages-scroll"
              ref={scroll}
              onScroll={() => {
                const el = scroll.current;
                if (el) {
                  follow.current =
                    el.scrollHeight - el.scrollTop - el.clientHeight < 100;
                  setShowJump(!follow.current);
                }
              }}
            >
              <div className="messages">
                <div className="conversation-date">与 Agent 的对话</div>
                {messages.map((m) => (
                  <MessageView key={m.id} message={m} />
                ))}
              </div>
            </div>
            <div className="conversation-composer">
              {showJump && (
                <button
                  className="jump-button"
                  aria-label="回到最新消息"
                  onClick={() => {
                    follow.current = true;
                    scroll.current?.scrollTo({
                      top: scroll.current.scrollHeight,
                      behavior: "smooth",
                    });
                  }}
                >
                  <ArrowDown size={17} />
                </button>
              )}
              <Composer
                value={draft}
                setValue={setDraft}
                onSend={send}
                onStop={() => void stop()}
                busy={!!running}
                stopping={stopping}
                disabled={!connected || loading || stopping}
                onAccount={() => setShowAccount(true)}
              />
            </div>
          </>
        )}
        <footer className="main-footer">
          <span>Agent 也可能出错，请核实重要信息。</span>
          <span className="footer-right">
            SGZ Agent
            <span>·</span>Shift + Enter 换行
          </span>
        </footer>
      </main>
      {showAccount && (
        <AccountPanel
          user={user}
          suspended={suspended}
          onClose={() => setShowAccount(false)}
          onLogout={onLogout}
          onExpired={onExpired}
        />
      )}
    </div>
  );
}

import { useEffect, useRef, useState } from "react";
import {
  ArrowDown,
  ArrowUpRight,
  Asterisk,
  BookOpen,
  Check,
  ChevronRight,
  CircleHelp,
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
import { Composer, modeNames } from "./components/Composer";
import { MessageView } from "./components/MessageView";
import { SettingsDialog } from "./components/SettingsDialog";
import { loadConversations, loadSettings, saveLocal } from "./lib/storage";
import { streamSSE } from "./lib/stream";
import { streamDsh } from "./lib/dsh";
import { streamDemo } from "./lib/demo";
import type { Conversation, Message, Settings, StreamEvent } from "./lib/types";

const prompts = [
  {
    icon: Compass,
    title: "了解当前状态",
    detail: "让 Agent 帮你梳理进展",
    text: "请只读查看当前 Agent 的能力与运行状态，说明有哪些可用工具，不执行游戏操作。",
  },
  {
    icon: BookOpen,
    title: "探索一个问题",
    detail: "从一个好问题开始",
    text: "请介绍这个项目的结构，以及前端与 Agent 的通信方式。",
  },
  {
    icon: PenLine,
    title: "把想法变成计划",
    detail: "拆解目标，找到下一步",
    text: "我有一个想法，想请你帮我梳理成一个可以执行的计划。请先问我最关键的问题。",
  },
  {
    icon: FileText,
    title: "整理与总结",
    detail: "让复杂信息清晰起来",
    text: "帮我整理一份项目说明，包含用途、主要能力、启动方式和当前限制。",
  },
];
const connectionKey = (settings: Settings) =>
  JSON.stringify([
    settings.mode,
    settings.mode === "dsh"
      ? settings.cwd
      : settings.mode === "sse"
        ? [settings.endpoint, settings.model]
        : null,
  ]);

export default function App() {
  const [settings, setSettings] = useState(loadSettings);
  const [conversations, setConversations] = useState(loadConversations);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [token, setToken] = useState("");
  const [sidebar, setSidebar] = useState(() => window.innerWidth > 800);
  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [busy, setBusy] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [connected, setConnected] = useState(false);
  const [notice, setNotice] = useState("");
  const [showJump, setShowJump] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const scroll = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const active = conversations.find((c) => c.id === activeId);
  const conversationMode = active?.mode || settings.mode;
  const latestConversations = useRef(conversations);
  latestConversations.current = conversations;
  const messages = active?.messages || [];
  const isEmpty = !messages.length;
  useEffect(() => {
    const timer = setTimeout(() => {
      if (!saveLocal("agent.conversations.v1", conversations))
        setNotice("浏览器存储不可用，对话将仅保留在当前页面。");
    }, 200);
    return () => clearTimeout(timer);
  }, [conversations]);
  useEffect(() => {
    if (follow.current && scroll.current)
      scroll.current.scrollTop = scroll.current.scrollHeight;
  }, [messages]);
  useEffect(() => {
    const flush = () => {
      saveLocal("agent.conversations.v1", latestConversations.current);
    };
    const handler = (e: BeforeUnloadEvent) => {
      flush();
      if (controller.current) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", handler);
    window.addEventListener("pagehide", flush);
    return () => {
      window.removeEventListener("beforeunload", handler);
      window.removeEventListener("pagehide", flush);
    };
  }, []);
  function newChat() {
    if (controller.current) return;
    setActiveId(null);
    setDraft("");
    follow.current = true;
    setShowJump(false);
    if (window.innerWidth <= 800) setSidebar(false);
  }
  function update(
    id: string,
    updateFn: (conversation: Conversation) => Conversation,
  ) {
    setConversations((current) =>
      current.map((c) => (c.id === id ? updateFn(c) : c)),
    );
  }
  async function send() {
    const text = draft.trim();
    if (!text || controller.current) return;
    if (
      active &&
      (active.mode !== settings.mode ||
        active.connectionKey !== connectionKey(settings))
    ) {
      setNotice("当前连接配置与此会话不同，请新建会话后继续。");
      return;
    }
    const abort = new AbortController();
    controller.current = abort;
    setBusy(true);
    setStopping(false);
    follow.current = true;
    setNotice("");
    const user: Message = {
      id: crypto.randomUUID(),
      role: "user",
      content: text,
    };
    const assistant: Message = {
      id: crypto.randomUUID(),
      role: "assistant",
      content: "",
      status: "streaming",
    };
    const conversation: Conversation = active || {
      id: crypto.randomUUID(),
      title: text.slice(0, 28),
      updatedAt: Date.now(),
      messages: [],
      mode: settings.mode,
      connectionKey: connectionKey(settings),
    };
    const history = [...conversation.messages, user];
    const next = {
      ...conversation,
      updatedAt: Date.now(),
      messages: [...history, assistant],
    };
    if (active) update(active.id, () => next);
    else setConversations((current) => [next, ...current]);
    setActiveId(next.id);
    setDraft("");
    const patchMessage = (fn: (message: Message) => Message) =>
      update(next.id, (c) => ({
        ...c,
        messages: c.messages.map((m) => (m.id === assistant.id ? fn(m) : m)),
      }));
    let hasText = false;
    const emit = (event: StreamEvent) => {
      if (event.type === "session") {
        update(next.id, (c) => ({ ...c, remoteId: event.id }));
        return;
      }
      if (event.type === "delta" || event.type === "replace") {
        hasText ||= !!event.text;
        patchMessage((m) => ({
          ...m,
          content: event.type === "delta" ? m.content + event.text : event.text,
        }));
      }
      if (event.type === "status")
        patchMessage((m) => ({ ...m, activity: event.text }));
    };
    try {
      if (settings.mode === "demo") await streamDemo(text, abort.signal, emit);
      else if (settings.mode === "dsh")
        await streamDsh(
          conversation.remoteId,
          settings.cwd,
          text,
          abort.signal,
          emit,
        );
      else
        await streamSSE(settings, history, next.id, token, abort.signal, emit);
      if (!hasText)
        throw new Error("Agent 本轮没有返回文本，请检查后台会话和模型状态。");
      patchMessage((m) => ({ ...m, status: "complete", activity: "" }));
      if (settings.mode !== "demo") setConnected(true);
    } catch (error) {
      const stopped = error instanceof Error && error.name === "AbortError";
      patchMessage((m) => ({
        ...m,
        status: stopped ? "stopped" : "error",
        activity: "",
        ...(!stopped
          ? {
              error:
                error instanceof Error
                  ? error.message
                  : "连接出错，请检查后端设置。",
            }
          : {}),
      }));
      if (!stopped) setConnected(false);
    } finally {
      controller.current = null;
      setBusy(false);
      setStopping(false);
    }
  }
  function saveSettings(value: Settings, secret: string, ready: boolean) {
    setSettings(value);
    setToken(secret);
    saveLocal("agent.settings.v1", value);
    setConnected(ready);
    setShowSettings(false);
    newChat();
  }
  function deleteConversation(id: string) {
    if (busy) return;
    setConversations((current) => current.filter((c) => c.id !== id));
    if (activeId === id) newChat();
  }
  const statusLabel =
    settings.mode === "demo" ? "演示模式" : connected ? "已连接" : "待连接";
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
            disabled={busy}
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
        <button className="new-chat" onClick={newChat} disabled={busy}>
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
                disabled={busy}
                onClick={() => {
                  setActiveId(c.id);
                  setDraft("");
                  follow.current = true;
                  if (window.innerWidth <= 800) setSidebar(false);
                }}
              >
                <MessageSquare size={15} />
                <span>{c.title}</span>
              </button>
              <button
                className="delete-conversation icon-button"
                disabled={busy}
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
            onClick={() => setShowSettings(true)}
            disabled={busy}
          >
            <Settings2 size={17} />
            <span>连接设置</span>
            <span className={`status-dot ${connected ? "online" : ""}`} />
          </button>
          <div className="workspace-profile">
            <div className="avatar">A</div>
            <div>
              <strong>本地工作空间</strong>
              <span>属于你的 Agent</span>
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
              onClick={() => setShowSettings(true)}
              disabled={busy}
            >
              <span
                className={`status-dot ${connected ? "online" : settings.mode === "demo" ? "demo" : ""}`}
              />
              {statusLabel}
            </button>
            <button
              className="icon-button help-button"
              aria-label="使用与连接帮助"
              onClick={() => setShowSettings(true)}
              disabled={busy}
            >
              <CircleHelp size={19} />
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
        {isEmpty ? (
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
                onStop={() => {}}
                busy={false}
                stopping={false}
                mode={settings.mode}
                onSettings={() => setShowSettings(true)}
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
                <div className="conversation-date">
                  {conversationMode === "demo"
                    ? "本地演示会话"
                    : "与 Agent 的对话"}
                </div>
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
                onStop={() => {
                  setStopping(true);
                  controller.current?.abort();
                }}
                busy={busy}
                stopping={stopping}
                mode={settings.mode}
                onSettings={() => setShowSettings(true)}
              />
            </div>
          </>
        )}
        <footer className="main-footer">
          <span>
            {conversationMode === "demo"
              ? "演示模式 · 内容在本地生成"
              : "Agent 也可能出错，请核实重要信息。"}
          </span>
          <span className="footer-right">
            {modeNames[conversationMode]}
            <span>·</span>Shift + Enter 换行
          </span>
        </footer>
      </main>
      {showSettings && (
        <SettingsDialog
          settings={settings}
          token={token}
          onClose={() => setShowSettings(false)}
          onSave={saveSettings}
        />
      )}
    </div>
  );
}

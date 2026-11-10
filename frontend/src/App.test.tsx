// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  act,
} from "@testing-library/react";
import App from "./App";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
});
const userA = { id: "a", username: "alice", role: "admin" };
const userB = { id: "b", username: "bob", role: "user" };
const status = {
  ready: false,
  model: "",
  execution: "headless",
  capabilities: [],
};
const expired = () =>
  Response.json({ message: "Authentication required" }, { status: 401 });

it("shows administrator-created login without importing unowned local history", async () => {
  localStorage.setItem(
    "agent.conversations.v1",
    JSON.stringify([
      { id: "old", title: "private old browser history", messages: [] },
    ]),
  );
  vi.stubGlobal("fetch", async () => expired());
  render(<App />);
  expect(await screen.findByRole("button", { name: "登录" })).toBeTruthy();
  expect(screen.getByLabelText("用户名")).toBeTruthy();
  expect(screen.getByText(/由管理员创建/)).toBeTruthy();
  expect(screen.queryByText("private old browser history")).toBeNull();
});

it("disables sending when the server reports missing model configuration", async () => {
  vi.stubGlobal("fetch", async (url: string) => {
    if (url.endsWith("/auth/me")) return Response.json({ user: userB });
    if (url.endsWith("/status")) return Response.json(status);
    return Response.json({ conversations: [] });
  });
  render(<App />);
  expect(await screen.findByText(/请联系管理员配置模型/)).toBeTruthy();
  fireEvent.change(screen.getByRole("textbox", { name: "消息" }), {
    target: { value: "hello" },
  });
  expect(
    (screen.getByRole("button", { name: "发送消息" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
});

it("clears immediately on logout and ignores a previous account's late history response", async () => {
  let resolveHistory!: (response: Response) => void;
  const oldHistory = new Promise<Response>((resolve) => {
    resolveHistory = resolve;
  });
  let account = "a";
  vi.stubGlobal("fetch", async (url: string) => {
    if (url.endsWith("/auth/me")) return Response.json({ user: userA });
    if (url.endsWith("/auth/logout"))
      return new Response(null, { status: 204 });
    if (url.endsWith("/auth/login")) {
      account = "b";
      return Response.json({ user: userB });
    }
    if (url.endsWith("/status"))
      return Response.json({ ...status, ready: true });
    if (url.endsWith("/conversations"))
      return account === "a"
        ? oldHistory
        : Response.json({
            conversations: [
              {
                id: "b-conv",
                title: "Bob history",
                updatedAt: 1,
                status: "idle",
              },
            ],
          });
    if (url.endsWith("/admin/users")) return Response.json({ users: [userA] });
    throw new Error(`Unexpected ${url}`);
  });
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "账户与管理" }));
  fireEvent.click(screen.getByRole("button", { name: "退出登录" }));
  await screen.findByRole("button", { name: "登录" });
  expect(screen.queryByText("alice")).toBeNull();
  fireEvent.change(screen.getByLabelText("用户名"), {
    target: { value: "bob" },
  });
  fireEvent.change(screen.getByLabelText("密码"), {
    target: { value: "long-password" },
  });
  fireEvent.click(screen.getByRole("button", { name: "登录" }));
  await screen.findByText("Bob history");
  await act(async () => {
    resolveHistory(
      Response.json({
        conversations: [
          { id: "a-conv", title: "Alice secret", updatedAt: 1, status: "idle" },
        ],
      }),
    );
  });
  await waitFor(() => expect(screen.queryByText("Alice secret")).toBeNull());
  expect(screen.queryByRole("button", { name: "账户与管理" })).toBeTruthy();
  expect(screen.queryByText("账户管理")).toBeNull();
});

it("ignores stale conversation details when navigating within an account", async () => {
  let resolveA!: (response: Response) => void;
  const pendingA = new Promise<Response>((resolve) => {
    resolveA = resolve;
  });
  const summary = (id: string) => ({
    id,
    title: `Conversation ${id}`,
    status: "idle",
    updatedAt: 1,
  });
  const detail = (id: string) => ({
    ...summary(id),
    messages: [
      {
        id: `m-${id}`,
        role: "assistant",
        content: `private body ${id}`,
        status: "complete",
      },
    ],
  });
  vi.stubGlobal("fetch", async (url: string) => {
    if (url.endsWith("/auth/me")) return Response.json({ user: userB });
    if (url.endsWith("/status"))
      return Response.json({ ...status, ready: true });
    if (url.endsWith("/conversations"))
      return Response.json({ conversations: [summary("a"), summary("b")] });
    if (url.endsWith("/conversations/a")) return pendingA;
    if (url.endsWith("/conversations/b"))
      return Response.json({ conversation: detail("b") });
    throw new Error(`Unexpected ${url}`);
  });
  render(<App />);
  fireEvent.click(
    await screen.findByRole("button", { name: "Conversation a" }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Conversation b" }));
  await screen.findByText("private body b");
  await act(async () => {
    resolveA(Response.json({ conversation: detail("a") }));
  });
  expect(screen.queryByText("private body a")).toBeNull();
  expect(screen.getByText("private body b")).toBeTruthy();
});

it("cancels once, disconnects its SSE stream and reloads the persisted stopped reply", async () => {
  let cancelCalls = 0;
  let messageCalls = 0;
  let streamAborted = false;
  let finishCancel!: (response: Response) => void;
  const pendingCancel = new Promise<Response>((resolve) => {
    finishCancel = resolve;
  });
  const userMessage = {
    id: "u1",
    role: "user",
    content: "question",
    status: "complete",
  };
  const assistantMessage = {
    id: "a1",
    role: "assistant",
    content: "",
    status: "streaming",
  };
  const conversation = {
    id: "c1",
    title: "question",
    updatedAt: 1,
    status: "idle",
    messages: [],
  };
  const stopped = {
    ...conversation,
    messages: [
      userMessage,
      { ...assistantMessage, content: "partial reply", status: "stopped" },
    ],
  };
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    if (url.endsWith("/auth/me")) return Response.json({ user: userB });
    if (url.endsWith("/status"))
      return Response.json({ ...status, ready: true });
    if (url.endsWith("/conversations"))
      return init?.method === "POST"
        ? Response.json({ conversation })
        : Response.json({ conversations: [] });
    if (url.endsWith("/messages")) {
      messageCalls++;
      init?.signal?.addEventListener("abort", () => {
        streamAborted = true;
      });
      return new Response(
        new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(
              new TextEncoder().encode(
                [
                  { type: "run", id: "r1", userMessage, assistantMessage },
                  { type: "delta", text: "partial reply" },
                ]
                  .map((event) => `data: ${JSON.stringify(event)}\n\n`)
                  .join(""),
              ),
            );
          },
        }),
        { headers: { "Content-Type": "text/event-stream" } },
      );
    }
    if (url.endsWith("/cancel")) {
      cancelCalls++;
      return pendingCancel;
    }
    if (url.endsWith("/conversations/c1"))
      return Response.json({ conversation: stopped });
    throw new Error(`Unexpected ${url}`);
  });
  render(<App />);
  await screen.findByRole("button", { name: "账户与管理" });
  await waitFor(() => expect(screen.getByText("服务已就绪")).toBeTruthy());
  fireEvent.change(screen.getByLabelText("消息"), {
    target: { value: "question" },
  });
  fireEvent.click(screen.getByRole("button", { name: "发送消息" }));
  await screen.findByText("partial reply");
  const stop = screen.getByRole("button", { name: "停止生成" });
  fireEvent.click(stop);
  fireEvent.click(stop);
  expect(cancelCalls).toBe(1);
  await act(async () => {
    finishCancel(Response.json({ cancelled: true }));
  });
  await screen.findByText("已停止生成");
  expect(messageCalls).toBe(1);
  expect(streamAborted).toBe(true);
  expect(screen.getByText("partial reply")).toBeTruthy();
});

it("expires the full workspace when a protected history request returns 401", async () => {
  vi.stubGlobal("fetch", async (url: string) => {
    if (url.endsWith("/auth/me")) return Response.json({ user: userA });
    if (url.endsWith("/status")) return Response.json(status);
    return expired();
  });
  render(<App />);
  await screen.findByRole("button", { name: "登录" });
  expect(screen.queryByText("alice")).toBeNull();
  expect(screen.getByText("登录已过期，请重新登录。")).toBeTruthy();
});

it("lets an administrator create, disable and reset an account through protected APIs", async () => {
  const changes: unknown[] = [];
  let bob = { ...userB, disabled: false };
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    if (url.endsWith("/auth/me")) return Response.json({ user: userA });
    if (url.endsWith("/status")) return Response.json(status);
    if (url.endsWith("/conversations"))
      return Response.json({ conversations: [] });
    if (url.endsWith("/admin/users") && init?.method === "POST") {
      changes.push(JSON.parse(String(init.body)));
      return Response.json({ user: bob });
    }
    if (url.endsWith("/admin/users"))
      return Response.json({ users: [{ ...userA, disabled: false }] });
    if (url.endsWith("/admin/users/b")) {
      const patch = JSON.parse(String(init?.body));
      changes.push(patch);
      bob = {
        ...bob,
        ...(patch.disabled !== undefined ? { disabled: patch.disabled } : {}),
      };
      return Response.json({ user: bob });
    }
    throw new Error(`Unexpected ${url}`);
  });
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "账户与管理" }));
  await screen.findByRole("heading", { name: "账户管理" });
  fireEvent.change(screen.getByLabelText("新账户用户名"), {
    target: { value: "bob" },
  });
  fireEvent.change(screen.getByLabelText("初始密码"), {
    target: { value: "initial-password" },
  });
  fireEvent.click(screen.getByRole("button", { name: "创建账户" }));
  await screen.findByText("bob");
  fireEvent.click(screen.getAllByRole("button", { name: "停用" })[1]);
  await screen.findByText("成员 · 已停用");
  fireEvent.click(screen.getByRole("button", { name: "重置密码：bob" }));
  fireEvent.change(screen.getByLabelText("新密码"), {
    target: { value: "replacement-password" },
  });
  fireEvent.click(screen.getByRole("button", { name: "确认重置" }));
  await screen.findByText("密码已重置，原有登录会话已撤销。");
  expect(changes).toEqual([
    { username: "bob", password: "initial-password", role: "user" },
    { disabled: true },
    { password: "replacement-password" },
  ]);
});

// Two App roots represent tabs with a shared cookie and origin event bus.
class TabChannel {
  static channels = new Set<TabChannel>();
  static sent: unknown[] = [];
  onmessage: ((event: MessageEvent) => void) | null = null;
  constructor(readonly name: string) { TabChannel.channels.add(this); }
  postMessage(data: unknown) {
    TabChannel.sent.push(data);
    for (const peer of TabChannel.channels)
      if (peer !== this && peer.name === this.name) peer.onmessage?.(new MessageEvent("message", { data }));
  }
  close() { TabChannel.channels.delete(this); }
}

it("clears the other tab immediately on logout/login, aborts A work and rejects late A results", async () => {
  vi.stubGlobal("BroadcastChannel", TabChannel);
  TabChannel.sent = [];
  let account: typeof userA | typeof userB | null = userA;
  let finishDetail!: (r: Response) => void;
  let finishLogout!: (r: Response) => void;
  let detailSignal: AbortSignal | null | undefined;
  const detail = new Promise<Response>(r => { finishDetail = r; });
  const logout = new Promise<Response>(r => { finishLogout = r; });
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    if (url.endsWith("/auth/me")) return account ? Response.json({ user: account }) : expired();
    if (url.endsWith("/auth/logout")) { account = null; return logout; }
    if (url.endsWith("/auth/login")) { account = userB; return Response.json({ user: userB }); }
    if (url.endsWith("/status")) return Response.json(status);
    if (url.endsWith("/admin/users")) return Response.json({ users: [userA] });
    if (url.endsWith("/conversations/a")) { detailSignal = init?.signal; return detail; }
    return Response.json({ conversations: [{ id: account?.id, title: account?.id === "a" ? "Alice private history" : "Bob history", updatedAt: 1, status: "idle" }] });
  });
  const tab1 = render(<App />), tab2 = render(<App />);
  const { within } = await import("@testing-library/react");
  const first = within(tab1.container), second = within(tab2.container);
  await first.findByText("Alice private history");
  fireEvent.click(await second.findByRole("button", { name: "Alice private history" }));
  fireEvent.click(first.getByRole("button", { name: "账户与管理" }));
  fireEvent.click(first.getByRole("button", { name: "退出登录" }));
  expect(second.queryByText("Alice private history")).toBeNull();
  expect(detailSignal?.aborted).toBe(true);
  await act(async () => { finishLogout(new Response(null, { status: 204 })); });
  await first.findByRole("button", { name: "登录" });
  fireEvent.change(first.getByLabelText("用户名"), { target: { value: "bob" } });
  fireEvent.change(first.getByLabelText("密码"), { target: { value: "bob-password" } });
  fireEvent.click(first.getByRole("button", { name: "登录" }));
  await second.findByText("Bob history");
  await act(async () => { finishDetail(Response.json({ conversation: { id: "a", title: "Alice private history", status: "idle", messages: [{ id: "secret", role: "assistant", content: "Alice secret body", status: "complete" }] } })); });
  expect(second.queryByText("Alice secret body")).toBeNull();
  expect(second.queryByText("Alice private history")).toBeNull();
  const payloads = JSON.stringify(TabChannel.sent);
  expect(payloads).not.toMatch(/alice|bob|password|cookie|secret|history|user/i);
});

it("revalidates on focus and visible restoration and ignores an older identity response", async () => {
  let identity = 0;
  let finishOld!: (r: Response) => void;
  const old = new Promise<Response>(r => { finishOld = r; });
  vi.stubGlobal("fetch", async (url: string) => {
    if (url.endsWith("/auth/me")) {
      identity++;
      return identity === 1 ? Response.json({ user: userA }) : identity === 2 ? old : Response.json({ user: userB });
    }
    if (url.endsWith("/status")) return Response.json(status);
    return Response.json({ conversations: [{ id: identity === 1 ? "a" : "b", title: identity === 1 ? "Alice private history" : "Bob history", updatedAt: 1, status: "idle" }] });
  });
  render(<App />);
  await screen.findByText("Alice private history");
  fireEvent(window, new Event("focus"));
  expect(screen.getByText("Alice private history").closest("[hidden][inert]")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Alice private history" })).toBeNull();
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  fireEvent(document, new Event("visibilitychange"));
  await screen.findByText("Bob history");
  await act(async () => { finishOld(Response.json({ user: userA })); });
  expect(screen.queryByText("Alice private history")).toBeNull();
  expect(screen.getByText("Bob history")).toBeTruthy();
});

it("clears on storage-only session notification and revalidates without copying account data", async () => {
  vi.stubGlobal("BroadcastChannel", undefined);
  let active = userA;
  vi.stubGlobal("fetch", async (url: string) => {
    if (url.endsWith("/auth/me")) return Response.json({ user: active });
    if (url.endsWith("/status")) return Response.json(status);
    return Response.json({ conversations: [{ id: active.id, title: active.id === "a" ? "Alice private history" : "Bob history", status: "idle", updatedAt: 1 }] });
  });
  render(<App />);
  await screen.findByText("Alice private history");
  fireEvent(window, new StorageEvent("storage", { key: "sgz.session-change", newValue: JSON.stringify({ phase: "changing", nonce: "before" }) }));
  expect(screen.queryByText("Alice private history")).toBeNull();
  active = userB;
  fireEvent(window, new StorageEvent("storage", { key: "sgz.session-change", newValue: JSON.stringify({ phase: "changed", nonce: "after" }) }));
  await screen.findByText("Bob history");
  expect(screen.queryByText("Alice private history")).toBeNull();
});

it("same-account focus keeps a draft and live stream while hiding content until identity is confirmed", async () => {
  let finishCheck!: (response: Response) => void;
  let checking: Promise<Response> | undefined;
  let streamSignal: AbortSignal | null | undefined;
  let output!: ReadableStreamDefaultController<Uint8Array>;
  const conversation = { id: "live", title: "Live conversation", status: "idle", updatedAt: 1, messages: [] };
  const userMessage = { id: "u", role: "user", content: "question", status: "complete" };
  const assistantMessage = { id: "a", role: "assistant", content: "", status: "streaming" };
  const enqueue = (event: unknown) => output.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`));
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    if (url.endsWith("/auth/me")) return checking ?? Response.json({ user: userB });
    if (url.endsWith("/status")) return Response.json({ ...status, ready: true });
    if (url.endsWith("/conversations")) return init?.method === "POST" ? Response.json({ conversation }) : Response.json({ conversations: [] });
    if (url.endsWith("/messages")) {
      streamSignal = init?.signal;
      return new Response(new ReadableStream<Uint8Array>({ start(controller) {
        output = controller;
        enqueue({ type: "run", id: "run", userMessage, assistantMessage });
        enqueue({ type: "delta", text: "private live reply" });
      } }), { headers: { "Content-Type": "text/event-stream" } });
    }
    throw new Error(`Unexpected ${url}`);
  });
  render(<App />);
  await screen.findByText("服务已就绪");
  fireEvent.change(screen.getByLabelText("消息"), { target: { value: "question" } });
  fireEvent.click(screen.getByRole("button", { name: "发送消息" }));
  await screen.findByText("private live reply");
  fireEvent.change(screen.getByLabelText("消息"), { target: { value: "unsent draft" } });
  const draft = screen.getByLabelText("消息");
  checking = new Promise<Response>(resolve => { finishCheck = resolve; });
  fireEvent(window, new Event("focus"));
  // Preserve its actual lifetime, not merely reconstructed text after a reload.
  expect(streamSignal?.aborted).toBe(false);
  expect(draft.isConnected).toBe(true);
  expect(draft.closest("[hidden][inert]")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "停止生成" })).toBeNull();
  await act(async () => {
    enqueue({ type: "delta", text: " continued" });
    finishCheck(Response.json({ user: userB }));
  });
  expect(await screen.findByText("private live reply continued")).toBeTruthy();
  expect(screen.getByLabelText("消息")).toBe(draft);
  expect((draft as HTMLTextAreaElement).value).toBe("unsent draft");
  expect(streamSignal?.aborted).toBe(false);
});

it("keeps previous content hidden when focus identity verification fails and clears it on 401", async () => {
  let mode: "initial" | "network" | "expired" = "initial";
  vi.stubGlobal("fetch", async (url: string) => {
    if (url.endsWith("/auth/me")) {
      if (mode === "network") throw new Error("Offline");
      return mode === "expired" ? expired() : Response.json({ user: userA });
    }
    if (url.endsWith("/status")) return Response.json(status);
    return Response.json({ conversations: [{ id: "a", title: "Alice private history", status: "idle", updatedAt: 1 }] });
  });
  render(<App />);
  await screen.findByText("Alice private history");
  mode = "network";
  fireEvent(window, new Event("focus"));
  await screen.findByText(/Offline/);
  expect(screen.getByText("Alice private history").closest("[hidden][inert]")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Alice private history" })).toBeNull();
  mode = "expired";
  fireEvent(window, new Event("focus"));
  await screen.findByRole("button", { name: "登录" });
  expect(screen.queryByText("Alice private history")).toBeNull();
});

it("closes an account modal during identity checks so failure retry stays accessible, then restores its form", async () => {
  let identityCalls = 0;
  let finishCheck!: (response: Response) => void;
  const check = new Promise<Response>(resolve => { finishCheck = resolve; });
  vi.stubGlobal("fetch", async (url: string) => {
    if (url.endsWith("/auth/me")) {
      identityCalls++;
      return identityCalls === 2 ? check : Response.json({ user: userA });
    }
    if (url.endsWith("/status")) return Response.json(status);
    if (url.endsWith("/admin/users")) return Response.json({ users: [userA] });
    return Response.json({ conversations: [] });
  });
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "账户与管理" }));
  await screen.findByRole("heading", { name: "账户管理" });
  const panel = document.querySelector("dialog")!;
  const field = screen.getByLabelText("新账户用户名") as HTMLInputElement;
  fireEvent.change(field, { target: { value: "new-member-draft" } });
  expect(panel.open).toBe(true);
  fireEvent(window, new Event("focus"));
  // Ancestor hidden alone does not remove a native dialog from the modal top layer.
  expect(panel.open).toBe(false);
  expect(field.isConnected).toBe(true);
  await act(async () => { finishCheck(Response.json({ message: "Offline" }, { status: 503 })); });
  const retry = await screen.findByRole("button", { name: "重新检查" });
  expect(panel.open).toBe(false);
  expect(retry.closest("[hidden], [inert]")).toBeNull();
  fireEvent.click(retry);
  await waitFor(() => expect(panel.open).toBe(true));
  expect(document.querySelector("dialog")).toBe(panel);
  expect(screen.getByLabelText("新账户用户名")).toBe(field);
  expect(field.value).toBe("new-member-draft");
});

export type Mode = "dsh" | "sse" | "demo";
export interface Settings {
  mode: Mode;
  endpoint: string;
  model: string;
  cwd: string;
}
export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  status?: "streaming" | "complete" | "stopped" | "error";
  error?: string;
  activity?: string;
}
export interface Conversation {
  id: string;
  title: string;
  updatedAt: number;
  messages: Message[];
  remoteId?: string;
  mode: Mode;
  connectionKey?: string;
}
export type StreamEvent =
  | { type: "delta"; text: string }
  | { type: "replace"; text: string }
  | { type: "status"; text: string }
  | { type: "session"; id: string }
  | { type: "done" };
export type Emit = (event: StreamEvent) => void;

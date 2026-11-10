export type RuntimeEvent = {
  type: "delta" | "replace" | "status";
  text: string;
};
export interface RunInput {
  conversationId: string;
  userId: string;
  runId: string;
  text: string;
  signal: AbortSignal;
  onEvent: (event: RuntimeEvent) => void;
}
export interface RuntimeAdapter {
  readonly ready: boolean;
  readonly model: string;
  run(input: RunInput): Promise<{ text: string }>;
  close?(): Promise<void>;
}

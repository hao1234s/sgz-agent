import type { Emit } from "./types";
export async function streamDemo(
  prompt: string,
  signal: AbortSignal,
  emit: Emit,
) {
  const text = `你好，我是你的 Agent。\n\n这是一次**流式会话演示**，你可以看到内容逐步出现，也可以随时停止生成。\n\n你刚才说：\n\n> ${prompt.replace(/\n/g, "\n> ")}\n\n连接真实 Agent 后，我可以根据你的要求调用项目里的工具、查询资料，并持续返回处理结果。\n\n### 从这里开始\n\n1. 点击左下角的 **连接设置**。\n2. 选择已有的 **DSH Agent**，完成连接认证；也可以接入自己的 SSE 服务。\n3. 新建会话，描述你想完成的任务。\n\n\`\`\`typescript\n// 让每个想法，从一次对话开始。\nconst conversation = { stream: true };\n\`\`\`\n\n当前内容来自本地演示，没有请求模型或执行任何工具。`;
  for (let i = 0; i < text.length; i += 5) {
    signal.throwIfAborted();
    emit({ type: "delta", text: text.slice(i, i + 5) });
    await new Promise<void>((resolve, reject) => {
      const abort = () => {
        clearTimeout(timer);
        reject(signal.reason);
      };
      const timer = setTimeout(() => {
        signal.removeEventListener("abort", abort);
        resolve();
      }, 25);
      signal.addEventListener("abort", abort, { once: true });
    });
  }
  emit({ type: "done" });
}

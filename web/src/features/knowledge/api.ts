export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...options,
    cache: "no-store",
  });
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw new Error(
      typeof error?.detail === "string"
        ? error.detail
        : Array.isArray(error?.detail)
          ? error.detail
              .map(
                (item: { loc?: string[]; msg?: string }) =>
                  `${item.loc?.slice(1).join(".") || "输入"}: ${item.msg || "格式不正确"}`,
              )
              .join("；")
          : `请求失败（${response.status}）`,
    );
  }
  return response.json() as Promise<T>;
}

export async function readEvents(
  response: Response,
  onEvent: (type: string, data: Record<string, unknown>) => void,
) {
  if (!response.ok || !response.body) {
    const error = await response.json().catch(() => null);
    throw new Error(
      typeof error?.detail === "string"
        ? error.detail
        : "无法开始问答，请稍后重试。",
    );
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "",
    complete = false;
  const consume = (packet: string) => {
    const lines = packet.split(/\r?\n/);
    const kind = lines
      .find((line) => line.startsWith("event:"))
      ?.slice(6)
      .trim();
    const raw = lines
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trim())
      .join("\n");
    if (!kind || !raw) return;
    const data = JSON.parse(raw);
    if (kind === "error") throw new Error(data.message || "回答失败，请重试。");
    if (kind === "done") complete = true;
    onEvent(kind, data);
  };
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += done
        ? decoder.decode()
        : decoder.decode(value, { stream: true });
      let boundary: RegExpExecArray | null;
      while ((boundary = /\r?\n\r?\n/.exec(buffer))) {
        consume(buffer.slice(0, boundary.index));
        buffer = buffer.slice(boundary.index + boundary[0].length);
      }
      if (done) {
        if (buffer.trim()) consume(buffer);
        break;
      }
    }
    if (!complete) throw new Error("回答连接中断，请重试。");
  } finally {
    if (!complete) await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

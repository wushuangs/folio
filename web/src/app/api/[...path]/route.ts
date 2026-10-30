import { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function proxy(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  const { path } = await context.params;
  if (
    path.some(
      (part) => part === ".." || part.includes("/") || part.includes("\\"),
    )
  )
    return new Response("Invalid path", { status: 400 });
  const origin = request.headers.get("origin");
  if (
    request.method !== "GET" &&
    origin &&
    origin !== request.nextUrl.origin &&
    new URL(origin).host !== request.headers.get("host")
  )
    return new Response("Forbidden", { status: 403 });
  const headers = new Headers();
  for (const key of ["content-type", "range"]) {
    const value = request.headers.get(key);
    if (value) headers.set(key, value);
  }
  const maximum = 52 * 1024 * 1024;
  let tooLarge = false;
  if (Number(request.headers.get("content-length") || 0) > maximum)
    return Response.json(
      { detail: "上传内容不能超过 50 MB。" },
      { status: 413 },
    );
  let received = 0;
  const body = request.body?.pipeThrough(
    new TransformStream({
      transform(chunk, controller) {
        received += chunk.byteLength;
        if (received > maximum) {
          tooLarge = true;
          throw new Error("Body too large");
        }
        controller.enqueue(chunk);
      },
    }),
  );
  try {
    const upstream = await fetch(
      `${process.env.BACKEND_URL || "http://127.0.0.1:8000"}/api/${path.map(encodeURIComponent).join("/")}${request.nextUrl.search}`,
      {
        method: request.method,
        headers,
        body: request.method === "GET" ? undefined : body,
        duplex: "half",
        cache: "no-store",
        signal: request.signal,
      } as RequestInit & { duplex: string },
    );
    const responseHeaders = new Headers();
    for (const key of [
      "content-type",
      "content-disposition",
      "content-range",
      "accept-ranges",
      "cache-control",
    ]) {
      const value = upstream.headers.get(key);
      if (value) responseHeaders.set(key, value);
    }
    responseHeaders.set("X-Accel-Buffering", "no");
    return new Response(upstream.body, {
      status: upstream.status,
      headers: responseHeaders,
    });
  } catch {
    if (tooLarge)
      return Response.json(
        { detail: "上传内容不能超过 50 MB。" },
        { status: 413 },
      );
    return Response.json(
      { detail: "无法连接知识库服务，请确认后端已启动。" },
      { status: 502 },
    );
  }
}

export const GET = proxy;
export const POST = proxy;

export const DELETE = proxy;

"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  ArrowUp,
  BookOpen,
  Check,
  CheckCheck,
  ChevronDown,
  CircleHelp,
  Copy,
  ExternalLink,
  FileText,
  FolderOpen,
  Layers3,
  LoaderCircle,
  MessageSquare,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Search,
  Settings2,
  ShieldCheck,
  Upload,
  X,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useSuggestions } from "../knowledge/use-suggestions";
import { PublishedKnowledgeList } from "../quality/published-knowledge-list";
import { AnswerFeedback } from "../quality/answer-feedback";
import { api, readEvents } from "../knowledge/api";
import type {
  Citation,
  Conversation,
  Document,
  Health,
  Message,
} from "../knowledge/types";

const suggestionStyles = {
  overview: { icon: CircleHelp, label: "了解内容", color: "mint" },
  guide: { icon: Settings2, label: "操作指南", color: "blue" },
  troubleshoot: { icon: Search, label: "排查与维护", color: "peach" },
  parameter: { icon: Layers3, label: "查找参数", color: "lavender" },
};

function Brand({ small = false }: { small?: boolean }) {
  return (
    <span className={`brand-symbol ${small ? "small" : ""}`} aria-hidden="true">
      <i />
      <i />
      <i />
    </span>
  );
}
function fileSize(size: number) {
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}
function shortName(name: string) {
  return name.replace(/\.pdf$/i, "").replaceAll("_", " ");
}

export function WorkspaceShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const isChat = pathname === "/chat";
  const isKnowledge = pathname === "/knowledge";
  const pageTitle = isChat ? "知识问答" : isKnowledge ? "知识库" : "质量与反馈";
  const [documents, setDocuments] = useState<Document[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const [versions, setVersions] = useState<
    Record<string, { revision: number; chunks: number; has_vectors: boolean }[]>
  >({});
  const [localOnly, setLocalOnly] = useState(false);
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [connectionError, setConnectionError] = useState("");
  const [modal, setModal] = useState<"documents" | "settings" | "help" | null>(
    null,
  );
  const [activeCitation, setActiveCitation] = useState<Citation | null>(null);
  const [showPreview, setShowPreview] = useState(false);
  const [sidebar, setSidebar] = useState(true);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [compact, setCompact] = useState(false);
  const [conversationSearch, setConversationSearch] = useState("");
  const [scopeOpen, setScopeOpen] = useState(false);
  const scopeRef = useRef<HTMLDivElement>(null);
  const navRef = useRef<HTMLElement>(null);
  const navToggleRef = useRef<HTMLButtonElement>(null);
  const navOpen = compact ? mobileNavOpen : sidebar;
  const filteredConversations = conversations.filter((c) =>
    c.title
      .toLocaleLowerCase()
      .includes(conversationSearch.trim().toLocaleLowerCase()),
  );
  const [copied, setCopied] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const fileInput = useRef<HTMLInputElement>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const ready = documents.filter((d) => d.status === "ready");
  const scope = selected.length
    ? ready.filter((d) => selected.includes(d.id))
    : ready;
  const starters = useSuggestions(scope);
  const suggestions = starters.items.map((item) => ({
    ...item,
    ...suggestionStyles[item.kind],
  }));
  const citations =
    [...messages].reverse().find((m) => m.role === "assistant")?.citations ||
    [];

  function openPanel(panel: "documents" | "settings" | "help") {
    if (panel === "documents") {
      setModal(null);
      router.push("/knowledge");
    } else setModal(panel);
    setScopeOpen(false);
    setMobileNavOpen(false);
  }
  useEffect(() => {
    const media = window.matchMedia("(max-width: 700px)");
    const update = () => {
      setCompact(media.matches);
      setMobileNavOpen(false);
    };
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  useEffect(() => {
    if (!scopeOpen) return;
    const outside = (e: PointerEvent) => {
      if (!scopeRef.current?.contains(e.target as Node)) setScopeOpen(false);
    };
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setScopeOpen(false);
        scopeRef.current?.querySelector<HTMLButtonElement>("button")?.focus();
      }
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", key);
    };
  }, [scopeOpen]);
  useEffect(() => {
    if (!compact || !mobileNavOpen) return;
    const previous = document.activeElement as HTMLElement | null;
    navRef.current?.querySelector<HTMLButtonElement>(".nav-close")?.focus();
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMobileNavOpen(false);
      if (e.key !== "Tab") return;
      const nodes = [
        ...(navRef.current?.querySelectorAll<HTMLElement>(
          "button:not(:disabled),a[href],input",
        ) || []),
      ];
      if (e.shiftKey && document.activeElement === nodes[0]) {
        e.preventDefault();
        nodes.at(-1)?.focus();
      } else if (!e.shiftKey && document.activeElement === nodes.at(-1)) {
        e.preventDefault();
        nodes[0]?.focus();
      }
    };
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("keydown", key);
      previous?.focus();
    };
  }, [compact, mobileNavOpen]);

  const refresh = useCallback(async () => {
    try {
      const [docs, status, history] = await Promise.all([
        api<Document[]>("/documents?include_archived=true"),
        api<Health>("/health"),
        api<Conversation[]>("/conversations"),
      ]);
      setDocuments(docs);
      setHealth(status);
      setConversations(history);
      setConnectionError("");
    } catch (e) {
      setConnectionError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    const initial = setTimeout(() => void refresh(), 0);
    const focus = () => void refresh();
    window.addEventListener("focus", focus);
    return () => {
      clearTimeout(initial);
      window.removeEventListener("focus", focus);
    };
  }, [refresh]);
  const indexing = documents.some((d) =>
    ["queued", "processing"].includes(d.indexing_status || d.status),
  );
  useEffect(() => {
    if (!indexing) return;
    const interval = setInterval(() => {
      if (!document.hidden) void refresh();
    }, 2000);
    return () => clearInterval(interval);
  }, [indexing, refresh]);
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);
  useEffect(() => () => abortRef.current?.abort(), []);
  useEffect(() => {
    if (!modal) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setModal(null);
    };
    const previous = document.activeElement as HTMLElement | null;
    const timer = setTimeout(
      () =>
        document.querySelector<HTMLElement>(".modal .close-button")?.focus(),
      0,
    );
    const trap = (e: KeyboardEvent) => {
      if (e.key !== "Tab") return;
      const nodes = [
        ...document.querySelectorAll<HTMLElement>(
          ".modal button:not(:disabled), .modal a[href], .modal input:not(:disabled)",
        ),
      ];
      const first = nodes[0],
        last = nodes[nodes.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last?.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first?.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("keydown", trap);
    return () => {
      clearTimeout(timer);
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("keydown", trap);
      previous?.focus();
    };
  }, [modal]);

  async function upload(files: FileList | null) {
    if (!files?.length) return;
    setUploading(true);
    setError("");
    try {
      for (const file of Array.from(files)) {
        if (!file.name.toLowerCase().endsWith(".pdf"))
          throw new Error("请选择 PDF 文件。");
        if (file.size > 50 * 1024 * 1024)
          throw new Error("单份 PDF 不能超过 50 MB。");
        const form = new FormData();
        form.append("file", file);
        await api<Document>("/documents", { method: "POST", body: form });
      }
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "上传失败");
    } finally {
      setUploading(false);
      void refresh();
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  function newChat() {
    if (busy) return;
    router.push("/chat");
    setModal(null);
    setMobileNavOpen(false);
    setScopeOpen(false);
    setMessages([]);
    setConversationId(null);
    setQuestion("");
    setActiveCitation(null);
    setShowPreview(false);
    setError("");
    textarea.current?.focus();
  }

  async function openConversation(id: string) {
    if (busy) return;
    try {
      setMessages(await api<Message[]>(`/conversations/${id}/messages`));
      setConversationId(id);
      router.push("/chat");
      setMobileNavOpen(false);
      setScopeOpen(false);
      setSelected(conversations.find((c) => c.id === id)?.document_ids || []);
      setModal(null);
      setActiveCitation(null);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "读取失败");
    }
  }

  async function ask(text = question) {
    const input = text.trim();
    if (!input || busy || !scope.length) return;
    setQuestion("");
    setError("");
    setBusy(true);
    setActiveCitation(null);
    setShowPreview(false);
    const id = crypto.randomUUID();
    setMessages((old) => [
      ...old,
      {
        id: crypto.randomUUID(),
        role: "user",
        content: input,
        citations: [],
        mode: "",
        elapsed_ms: 0,
      },
      {
        id,
        role: "assistant",
        content: "",
        citations: [],
        mode: "",
        elapsed_ms: 0,
      },
    ]);
    const update = (patch: Partial<Message>) =>
      setMessages((old) =>
        old.map((m) => (m.id === id ? { ...m, ...patch } : m)),
      );
    let content = "";
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: input,
          conversation_id: conversationId,
          document_ids: selected,
          answer_mode: localOnly ? "extractive" : "auto",
        }),
        signal: controller.signal,
      });
      await readEvents(response, (type, data) => {
        if (type === "start") setConversationId(data.conversation_id as string);
        if (type === "sources")
          update({
            citations: data.citations as Citation[],
            approved_knowledge:
              data.approved_knowledge as Message["approved_knowledge"],
            mode: data.mode as string,
            warning: data.warning as string,
            retrieval_mode: data.retrieval_mode as string,
          });
        if (type === "replace") {
          content = data.content as string;
          update({
            content,
            citations: data.citations as Citation[],
            mode: data.mode as string,
            warning: data.warning as string,
          });
        }
        if (type === "delta") {
          content += data.text as string;
          update({ content });
        }
        if (type === "done")
          update({
            id: data.message_id as string,
            status: "complete",
            elapsed_ms: data.elapsed_ms as number,
            mode: data.mode as string,
            warning: data.warning as string,
            retrieval_mode: data.retrieval_mode as string,
          });
      });
    } catch (e) {
      update({
        content: controller.signal.aborted
          ? "生成已取消，可重新提问。"
          : e instanceof Error
            ? e.message
            : "回答失败，请重试。",
        citations: [],
        error: true,
      });
    } finally {
      setBusy(false);
      abortRef.current = null;
      void refresh();
    }
  }

  async function copy(message: Message) {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(message.id);
      setTimeout(() => setCopied(null), 1800);
    } catch {
      setError("复制失败，请手动选择文字复制。");
    }
  }
  async function loadVersions(id: string) {
    try {
      const list = await api<
        { revision: number; chunks: number; has_vectors: boolean }[]
      >(`/documents/${id}/revisions`);
      setVersions((old) => ({ ...old, [id]: list }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "读取版本失败");
    }
  }
  async function documentAction(id: string, action: string, body?: unknown) {
    try {
      await api(`/documents/${id}${action === "archive" ? "" : `/${action}`}`, {
        method: action === "archive" ? "DELETE" : "POST",
        headers: { "Content-Type": "application/json" },
        body: body ? JSON.stringify(body) : undefined,
      });
      if (action === "archive")
        setSelected((old) => old.filter((item) => item !== id));
      setActiveCitation(null);
      await refresh();
      await loadVersions(id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败");
    }
  }
  async function reindex(id: string, mode: "full" | "text" = "full") {
    try {
      await api(`/documents/${id}/reindex`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode }),
      });
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "重建失败");
    }
  }

  function renderPanel(
    panelMode: "documents" | "settings" | "help",
    embedded = false,
  ) {
    return (
      <div
        className={embedded ? "route-content knowledge-page" : "modal-backdrop"}
        role={embedded ? "main" : undefined}
        onMouseDown={(e) => {
          if (!embedded && e.target === e.currentTarget) setModal(null);
        }}
      >
        <section
          className={embedded ? "modal knowledge-page-panel" : "modal"}
          role={embedded ? undefined : "dialog"}
          aria-modal={embedded ? undefined : true}
          aria-labelledby={embedded ? "knowledge-page-title" : "modal-title"}
        >
          <header>
            <div>
              <span className="eyebrow">YOUR WORKSPACE</span>
              <h2 id={embedded ? "knowledge-page-title" : "modal-title"}>
                {panelMode === "documents"
                  ? "管理知识库"
                  : panelMode === "settings"
                    ? "模型与设置"
                    : "开始使用 Folio"}
              </h2>
            </div>
            {!embedded && (
              <button
                className="icon-button close-button"
                onClick={() => setModal(null)}
                aria-label="关闭弹窗"
              >
                <X size={19} />
              </button>
            )}
          </header>
          {panelMode === "documents" && (
            <>
              <p className="modal-description">
                管理文档、索引版本与归档。上传和向量重建会调用已配置的模型服务；文字重建在本地处理。
              </p>
              <button
                className="upload-zone"
                disabled={uploading}
                onClick={() => fileInput.current?.click()}
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => {
                  e.preventDefault();
                  void upload(e.dataTransfer.files);
                }}
              >
                <Upload size={25} />
                <strong>
                  {uploading ? "正在上传文档…" : "点击上传，或拖入 PDF 文档"}
                </strong>
                <span>文字型 PDF · 单份最大 50 MB · 自动去重</span>
              </button>
              <label className="local-mode">
                <input
                  type="checkbox"
                  checked={showArchived}
                  onChange={(e) => setShowArchived(e.target.checked)}
                />{" "}
                显示已归档文档
              </label>
              <div className="document-list">
                {documents
                  .filter((d) => showArchived || d.status !== "archived")
                  .map((d) => (
                    <div key={d.id} className="document-row">
                      <FileText size={23} />
                      <div>
                        <strong>{shortName(d.name)}</strong>
                        <small>
                          {d.status === "ready"
                            ? `${d.pages} 页 · ${d.chunks} 个片段 · ${fileSize(d.size)} · ${d.vector_ready ? "混合检索" : "关键词检索"}`
                            : d.status === "error"
                              ? "解析失败"
                              : d.status === "archived"
                                ? "已归档，可恢复"
                                : `解析入库中 · ${d.progress}%`}
                        </small>
                        {d.error && <p>{d.error}</p>}
                        {versions[d.id] && (
                          <div className="version-list">
                            {versions[d.id].map((v) => (
                              <button
                                className="text-button"
                                key={v.revision}
                                disabled={
                                  v.revision === d.active_revision ||
                                  d.status === "archived" ||
                                  ["queued", "processing"].includes(
                                    d.indexing_status,
                                  )
                                }
                                onClick={() =>
                                  void documentAction(d.id, "rollback", {
                                    revision: v.revision,
                                  })
                                }
                              >
                                v{v.revision} · {v.chunks}片段 ·{" "}
                                {v.has_vectors ? "含向量" : "文字"}
                                {v.revision === d.active_revision
                                  ? "（当前）"
                                  : " · 切换"}
                              </button>
                            ))}
                          </div>
                        )}
                        <div className="document-actions">
                          <button
                            className="text-button"
                            onClick={() => void loadVersions(d.id)}
                          >
                            索引版本
                          </button>
                          <button
                            className="text-button"
                            disabled={["queued", "processing"].includes(
                              d.indexing_status,
                            )}
                            onClick={() =>
                              void documentAction(
                                d.id,
                                d.status === "archived" ? "restore" : "archive",
                              )
                            }
                          >
                            {d.status === "archived" ? "恢复文档" : "归档文档"}
                          </button>
                        </div>
                        {["queued", "processing"].includes(
                          d.indexing_status || d.status,
                        ) && <progress value={d.progress} max={100} />}
                      </div>
                      {["ready", "error"].includes(d.status) &&
                        !["queued", "processing"].includes(
                          d.indexing_status,
                        ) && (
                          <button
                            className="text-button"
                            onClick={() => void reindex(d.id)}
                          >
                            向量重建
                          </button>
                        )}
                      {["ready", "error"].includes(d.status) &&
                        !["queued", "processing"].includes(
                          d.indexing_status,
                        ) && (
                          <button
                            className="text-button"
                            onClick={() => void reindex(d.id, "text")}
                          >
                            文字重建
                          </button>
                        )}
                    </div>
                  ))}
              </div>
              <footer className="modal-footer">
                <span className="document-total">
                  {documents.filter((d) => d.status !== "archived").length}{" "}
                  份文档
                </span>
                {!embedded && (
                  <button
                    className="button primary"
                    onClick={() => setModal(null)}
                  >
                    完成 <Check size={14} />
                  </button>
                )}
              </footer>
              {embedded && <PublishedKnowledgeList />}
            </>
          )}
          {panelMode === "settings" && (
            <div className="settings-content">
              <div className="connection-card">
                <span
                  className={`status-dot ${health?.generation_ready ? "green" : ""}`}
                />
                <div>
                  <strong>
                    {health?.generation_ready
                      ? "生成式问答已配置"
                      : "当前使用原文检索模式"}
                  </strong>
                  <p>
                    {health?.generation_ready
                      ? `回答模型：${health.model}`
                      : "无需密钥即可查找手册原文；连接模型后，可生成带引用的回答。"}
                  </p>
                </div>
              </div>
              <h3>连接你的模型</h3>
              <p>
                在项目根目录打开 <code>.env.local</code>，填入智谱 GLM
                密钥，然后重启后端。已预设官方接口，回答与向量服务可共用一个密钥。
              </p>
              <pre>
                {
                  "ZHIPU_API_KEY=你的智谱密钥\nLLM_MODEL=glm-4.7\nEMBEDDING_MODEL=embedding-3"
                }
              </pre>
              <h3>可选：启用向量检索</h3>
              <p>
                填写智谱密钥后会启用
                embedding-3。重启后端，再对已有文档执行“向量重建”；也可通过环境变量切换其他兼容服务。
              </p>
              <div className="settings-foot">
                <ShieldCheck size={16} />
                密钥仅在后端读取，不会返回到浏览器。
              </div>
            </div>
          )}
          {panelMode === "help" && (
            <div className="help-content">
              {[
                {
                  title: "01  连接你的知识",
                  text: "上传文字型 PDF，系统自动提取文本并建立检索片段。扫描件需要先进行 OCR。",
                },
                {
                  title: "02  用自然语言提问",
                  text: "描述你想了解的功能、参数或故障。可以继续追问，也可以选择文档限定范围。",
                },
                {
                  title: "03  回到原文验证",
                  text: "点击回答下方的页码引用，在右侧阅读原文，并打开 PDF 查看上下文。",
                },
              ].map((s) => (
                <div key={s.title}>
                  <h3>{s.title}</h3>
                  <p>{s.text}</p>
                </div>
              ))}
              <p className="help-note">
                当前版本面向本机单人使用。未配置模型时仅展示检索原文；扫描件
                OCR、人工客服与业务办理不在本版范围内。
              </p>
            </div>
          )}
        </section>
      </div>
    );
  }

  return (
    <div
      className={`workspace ${sidebar ? "" : "sidebar-collapsed"} ${mobileNavOpen ? "mobile-nav-open" : ""}`}
    >
      <input
        ref={fileInput}
        type="file"
        accept="application/pdf,.pdf"
        multiple
        className="sr-only"
        tabIndex={-1}
        aria-hidden="true"
        aria-label="上传 PDF 文件"
        onChange={(e) => void upload(e.target.files)}
      />
      {compact && mobileNavOpen && (
        <button
          className="nav-backdrop"
          aria-label="关闭导航遮罩"
          onClick={() => setMobileNavOpen(false)}
          tabIndex={-1}
        />
      )}
      <aside
        ref={navRef}
        id="workspace-navigation"
        className="sidebar"
        aria-label="工作空间导航"
        aria-hidden={!navOpen}
        inert={!navOpen}
        role={compact ? "dialog" : undefined}
        aria-modal={compact && mobileNavOpen ? true : undefined}
      >
        <div className="sidebar-brand-row">
          <Link className="brand" href="/chat" aria-label="Folio 首页">
            <Brand />
            <span>
              folio<span className="brand-period">.</span>
            </span>
            <span className="brand-beta">BETA</span>
          </Link>
          <button
            className="icon-button nav-close"
            onClick={() => setMobileNavOpen(false)}
            aria-label="关闭导航"
          >
            <X size={19} />
          </button>
        </div>
        <div className="workspace-identity">
          <span className="workspace-avatar">知</span>
          <div>
            <strong>我的知识空间</strong>
            <span>让回答，有据可循</span>
          </div>
        </div>
        <button className="new-chat" onClick={newChat} disabled={busy}>
          <Plus size={17} />
          新建对话
        </button>
        <nav className="main-nav" aria-label="主菜单">
          <span className="nav-section-label">工作台</span>
          {[
            { href: "/chat", label: "知识问答", icon: MessageSquare },
            { href: "/knowledge", label: "知识库", icon: FolderOpen },
            { href: "/quality", label: "质量与反馈", icon: CheckCheck },
          ].map(({ href, label, icon: Icon }) => (
            <Link
              key={href}
              href={href}
              className={pathname === href ? "active" : ""}
              aria-current={pathname === href ? "page" : undefined}
              onClick={() => {
                setModal(null);
                setMobileNavOpen(false);
                setScopeOpen(false);
              }}
            >
              <Icon size={17} />
              <span>{label}</span>
              {href === "/knowledge" && (
                <span className="count">
                  {documents.filter((d) => d.status !== "archived").length}
                </span>
              )}
            </Link>
          ))}
        </nav>
        <section className="conversation-navigation" aria-label="对话列表">
          <div className="conversation-heading">
            <h2>对话</h2>
            <span>{conversations.length}</span>
          </div>
          {!!conversations.length && (
            <label className="conversation-search">
              <Search size={14} />
              <input
                aria-label="搜索对话"
                placeholder="搜索对话…"
                value={conversationSearch}
                onChange={(e) => setConversationSearch(e.target.value)}
              />
              {conversationSearch && (
                <button
                  onClick={() => setConversationSearch("")}
                  aria-label="清除对话搜索"
                >
                  <X size={13} />
                </button>
              )}
            </label>
          )}
          <div className="recent-list">
            {filteredConversations.length ? (
              filteredConversations.map((c) => (
                <button
                  key={c.id}
                  title={c.title}
                  className={conversationId === c.id ? "selected" : ""}
                  aria-current={conversationId === c.id ? "true" : undefined}
                  disabled={busy}
                  onClick={() => void openConversation(c.id)}
                >
                  <MessageSquare size={14} />
                  <span>{c.title}</span>
                </button>
              ))
            ) : (
              <p>
                {conversationSearch
                  ? "没有找到匹配的对话。"
                  : "提出第一个问题后， 对话会保存在这里。"}
              </p>
            )}
          </div>
        </section>
        <div className="sidebar-bottom">
          <button
            className="settings-button"
            onClick={() => openPanel("settings")}
          >
            <Settings2 size={16} />
            模型与设置
            <span
              className={`status-dot ${health?.generation_ready ? "green" : ""}`}
            />
          </button>
          <button className="help-button" onClick={() => openPanel("help")}>
            <CircleHelp size={16} />
            使用帮助
          </button>
          <div className="workspace-footer">
            <span className="status-dot green" />
            本地工作空间<span>FOLIO</span>
          </div>
        </div>
      </aside>
      <div className="main-shell" inert={compact && mobileNavOpen}>
        <header className="workspace-header">
          <div className="workspace-heading">
            <button
              ref={navToggleRef}
              className="icon-button navigation-toggle"
              onClick={() =>
                compact
                  ? setMobileNavOpen(!mobileNavOpen)
                  : setSidebar(!sidebar)
              }
              aria-label={navOpen ? "收起导航" : "展开导航"}
              aria-expanded={navOpen}
              aria-controls="workspace-navigation"
            >
              {navOpen ? (
                <PanelLeftClose size={20} />
              ) : (
                <PanelLeftOpen size={20} />
              )}
            </button>
            <div>
              <p>我的知识空间</p>
              <h1>{pageTitle}</h1>
            </div>
          </div>
          <span
            className={`workspace-status ${connectionError ? "offline" : ""}`}
          >
            <span
              className={`status-dot ${!loading && !connectionError ? "green" : ""}`}
            />
            {loading ? "连接中" : connectionError ? "服务未连接" : "本地运行"}
          </span>
        </header>
        {(error || connectionError) && (
          <div className="error-banner" role="alert">
            {error || connectionError}
            <button
              aria-label="关闭错误提示"
              onClick={() => {
                setError("");
                setConnectionError("");
              }}
            >
              <X size={15} />
            </button>
          </div>
        )}
        <div className="work-area" hidden={!isChat}>
          <main className="chat-panel">
            <div className="scope-bar">
              <div ref={scopeRef} className="scope-control">
                <span className="scope-icon">
                  <BookOpen size={15} />
                </span>
                <strong>检索范围</strong>
                <button
                  aria-label="选择检索范围"
                  aria-expanded={scopeOpen}
                  aria-haspopup="dialog"
                  disabled={busy}
                  onClick={() => setScopeOpen(!scopeOpen)}
                >
                  {selected.length ? `已选 ${scope.length} 份文档` : "全部文档"}
                  <ChevronDown size={13} />
                </button>
                {scopeOpen && (
                  <section
                    className="scope-picker"
                    role="dialog"
                    aria-label="选择检索范围"
                  >
                    <header>
                      <strong>选择本次问答的文档</strong>
                      <button
                        onClick={() => setScopeOpen(false)}
                        aria-label="关闭范围选择"
                      >
                        <X size={14} />
                      </button>
                    </header>
                    <p>勾选文档以限定范围；未勾选时检索全部。</p>
                    <div className="scope-options">
                      {ready.length ? (
                        ready.map((doc) => (
                          <label key={doc.id}>
                            <input
                              type="checkbox"
                              checked={selected.includes(doc.id)}
                              onChange={() =>
                                setSelected((old) =>
                                  old.includes(doc.id)
                                    ? old.filter((id) => id !== doc.id)
                                    : [...old, doc.id],
                                )
                              }
                            />
                            <span title={doc.name}>{shortName(doc.name)}</span>
                            <small>{doc.pages}页</small>
                          </label>
                        ))
                      ) : (
                        <p>还没有就绪文档，请先在侧栏知识库中添加。</p>
                      )}
                    </div>
                    <footer>
                      <button
                        onClick={() => setSelected([])}
                        disabled={!selected.length}
                      >
                        使用全部文档
                      </button>
                      <button onClick={() => setScopeOpen(false)}>
                        完成
                        <Check size={13} />
                      </button>
                    </footer>
                  </section>
                )}
              </div>
              <span className="scope-count">{scope.length} 份文档已就绪</span>
            </div>
            <div
              className={`chat-scroll ${messages.length ? "has-messages" : ""}`}
            >
              {!messages.length ? (
                <div className="welcome">
                  <div className="welcome-visual">
                    <span className="orb orb-one" />
                    <span className="orb orb-two" />
                    <div className="document-tile tile-back">
                      <span />
                      <span />
                      <span />
                    </div>
                    <div className="document-tile tile-front">
                      <Brand />
                      <span />
                      <span />
                      <span />
                    </div>
                    <span className="sparkle-one">✦</span>
                    <span className="sparkle-two">✧</span>
                    <span className="verified-stamp">
                      <Check size={14} />
                    </span>
                  </div>
                  <div className="welcome-kicker">
                    <span /> YOUR KNOWLEDGE, CONNECTED
                  </div>
                  <h2>让知识，成为答案。</h2>
                  <p>
                    提一个问题，连接你的文档。
                    <br />
                    从产品参数到操作指南，每一个回答都有出处。
                  </p>
                  <div className="knowledge-summary">
                    <span className="summary-icon">
                      <FileText size={19} />
                    </span>
                    <div>
                      <strong>
                        {loading
                          ? "正在连接知识库…"
                          : scope.length
                            ? `${scope.length} 份文档，已准备就绪`
                            : "为你的知识空间添加第一份文档"}
                      </strong>
                      <span>
                        {scope.length
                          ? `共 ${scope.reduce((n, d) => n + d.pages, 0)} 页内容 · 随时可检索`
                          : "支持文字型 PDF · 单份最大 50 MB"}
                      </span>
                    </div>
                    {scope.length ? (
                      <CheckCheck className="summary-ready" size={20} />
                    ) : (
                      <button
                        className="onboarding-upload"
                        onClick={() => openPanel("documents")}
                      >
                        添加文档
                        <Plus size={15} />
                      </button>
                    )}
                  </div>
                  <div className="suggestions-heading">
                    <span>
                      {starters.loading
                        ? "正在整理当前文档的问题…"
                        : "试着从这里开始"}
                    </span>
                    <span className="small-line" />
                  </div>
                  {starters.error && <p role="status">{starters.error}</p>}
                  {!starters.loading &&
                    !suggestions.length &&
                    !starters.error && (
                      <p className="suggestions-empty">
                        {scope.length
                          ? "暂无合适的推荐问题，可以直接输入问题。"
                          : "添加并选择文档后，这里会展示对应的问题。"}
                      </p>
                    )}
                  <div className="suggestions">
                    {suggestions.map((s) => (
                      <button
                        key={`${s.document_id}-${s.text}`}
                        disabled={!scope.length || busy}
                        onClick={() => void ask(s.text)}
                      >
                        <span className={`suggestion-icon ${s.color}`}>
                          <s.icon size={17} />
                        </span>
                        <span>
                          <small title={s.document_name}>
                            {s.label} · {shortName(s.document_name)}
                          </small>
                          <strong>{s.text}</strong>
                        </span>
                        <ArrowUp size={14} />
                      </button>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="messages" aria-live="polite">
                  {messages.map((m) => (
                    <article
                      key={m.id}
                      className={`message ${m.role} ${m.error || m.status === "error" ? "message-error" : ""}`}
                    >
                      <div className="message-avatar">
                        {m.role === "assistant" ? <Brand small /> : "你"}
                      </div>
                      <div className="message-body">
                        <div className="message-author">
                          {m.role === "assistant" ? "Folio" : "你"}
                          {m.role === "assistant" && (
                            <span>
                              {m.mode === "error"
                                ? "回答失败"
                                : m.mode === "cancelled"
                                  ? "已取消"
                                  : m.mode === "generated"
                                    ? "知识库回答"
                                    : m.mode === "approved"
                                      ? `已发布标准答案 · v${m.approved_knowledge?.revision ?? ""}`
                                      : m.mode === "no_evidence"
                                        ? "资料不足"
                                        : "原文检索"}
                            </span>
                          )}
                        </div>
                        {m.content ? (
                          <div className="markdown">
                            <ReactMarkdown
                              remarkPlugins={[remarkGfm]}
                              components={{
                                img: () => null,
                                a: ({ href, children }) =>
                                  href?.startsWith("#source-") ? (
                                    <button
                                      className="source-ref"
                                      onClick={() => {
                                        const cite = m.citations.find(
                                          (c) => c.id === Number(href.slice(8)),
                                        );
                                        if (cite) {
                                          setActiveCitation(cite);
                                          setShowPreview(false);
                                        }
                                      }}
                                    >
                                      {children}
                                    </button>
                                  ) : (
                                    <a
                                      href={
                                        href && /^https?:\/\//.test(href)
                                          ? href
                                          : undefined
                                      }
                                      target="_blank"
                                      rel="noopener noreferrer"
                                    >
                                      {children}
                                    </a>
                                  ),
                              }}
                            >
                              {m.content.replace(
                                /\[(\d+)\](?!\()/g,
                                (original, number) =>
                                  m.citations.some(
                                    (c) => c.id === Number(number),
                                  )
                                    ? `[${number}](#source-${number})`
                                    : original,
                              )}
                            </ReactMarkdown>
                          </div>
                        ) : (
                          <div className="thinking">
                            <LoaderCircle size={15} className="spin" />
                            正在查找相关资料<span>…</span>
                          </div>
                        )}
                        {m.warning && (
                          <p className="retrieval-note" role="status">
                            {m.warning}
                          </p>
                        )}
                        {m.citations.length > 0 && (
                          <div className="citation-chips">
                            {m.citations.map((c) => (
                              <button
                                key={c.chunk_id}
                                onClick={() => {
                                  setActiveCitation(c);
                                  setShowPreview(false);
                                }}
                              >
                                <FileText size={12} />[{c.id}] 第 {c.page} 页
                              </button>
                            ))}
                          </div>
                        )}
                        {m.role === "assistant" && m.content && (
                          <div className="message-footer">
                            <span>
                              <ShieldCheck size={12} />
                              {m.error || m.status === "error"
                                ? "回答未完成，可重新提问"
                                : m.mode === "approved"
                                  ? `直接采用审核版本 v${m.approved_knowledge?.revision ?? ""} · 未重新生成`
                                  : m.mode === "generated"
                                    ? "请结合引用原文核对"
                                    : "基于知识库原文"}
                              {m.elapsed_ms > 0 &&
                                ` · ${(m.elapsed_ms / 1000).toFixed(1)}s`}
                            </span>
                            <button
                              onClick={() => void copy(m)}
                              aria-label="复制回答"
                            >
                              {copied === m.id ? (
                                <CheckCheck size={14} />
                              ) : (
                                <Copy size={14} />
                              )}
                            </button>
                          </div>
                        )}
                        {m.role === "assistant" &&
                          !busy &&
                          m.status === "complete" && (
                            <AnswerFeedback messageId={m.id} />
                          )}
                      </div>
                    </article>
                  ))}
                  <div ref={bottom} />
                </div>
              )}
            </div>
            <div className="composer-wrap">
              <div className="composer">
                <textarea
                  ref={textarea}
                  aria-label="输入问题"
                  placeholder={
                    scope.length
                      ? "向你的知识库提问，答案会附上来源…"
                      : "先上传 PDF 文档，开始知识问答…"
                  }
                  value={question}
                  maxLength={2000}
                  disabled={busy}
                  onChange={(e) => setQuestion(e.target.value)}
                  onKeyDown={(e) => {
                    if (
                      e.key === "Enter" &&
                      !e.shiftKey &&
                      !e.nativeEvent.isComposing
                    ) {
                      e.preventDefault();
                      void ask();
                    }
                  }}
                  rows={2}
                />
                <div className="composer-bottom">
                  <label className="local-mode">
                    <input
                      type="checkbox"
                      checked={localOnly}
                      disabled={busy}
                      onChange={(e) => setLocalOnly(e.target.checked)}
                    />{" "}
                    仅查原文（本地处理）
                  </label>
                  <div>
                    <span className="enter-hint">
                      Enter 发送 · Shift + Enter 换行
                    </span>
                    <button
                      className="send-button"
                      aria-label={busy ? "停止生成" : "发送问题"}
                      disabled={!busy && (!question.trim() || !scope.length)}
                      onClick={() =>
                        busy ? abortRef.current?.abort() : void ask()
                      }
                    >
                      {busy ? (
                        <LoaderCircle size={17} className="spin" />
                      ) : (
                        <ArrowUp size={19} />
                      )}
                    </button>
                  </div>
                </div>
              </div>
              <div className="composer-note">
                <ShieldCheck size={12} />
                <span>答案基于你的文档生成，请核对重要信息。</span>
              </div>
            </div>
          </main>

          <aside
            className={`sources-panel ${activeCitation ? "source-active" : ""}`}
          >
            <div className="sources-heading">
              <div>
                <Layers3 size={17} />
                <h2>参考来源</h2>
              </div>
              <span>
                {citations.length
                  ? String(citations.length).padStart(2, "0")
                  : "—"}
              </span>
              {activeCitation && (
                <button
                  className="mobile-close"
                  aria-label="关闭来源"
                  onClick={() => setActiveCitation(null)}
                >
                  <X size={16} />
                </button>
              )}
            </div>
            <div className="sources-scroll">
              {activeCitation ? (
                <div className="source-detail">
                  <button
                    className="back-link"
                    onClick={() => {
                      setActiveCitation(null);
                      setShowPreview(false);
                    }}
                  >
                    ← 全部参考来源
                  </button>
                  <span className="pdf-label">PDF DOCUMENT</span>
                  <h3>{shortName(activeCitation.document_name)}</h3>
                  <div className="source-location">
                    <span>参考 [{activeCitation.id}]</span>
                    <span>PDF 第 {activeCitation.page} 页</span>
                  </div>
                  <div className="source-excerpt">{activeCitation.content}</div>
                  <button
                    className="button secondary preview-button"
                    onClick={() => setShowPreview(!showPreview)}
                  >
                    <BookOpen size={14} />
                    {showPreview ? "收起 PDF 预览" : "查看 PDF 原页"}
                    <ChevronDown size={13} />
                  </button>
                  {showPreview && (
                    <iframe
                      title={`PDF 第 ${activeCitation.page} 页`}
                      src={`/api/documents/${activeCitation.document_id}/file#page=${activeCitation.page}`}
                      className="pdf-preview"
                    />
                  )}
                  <a
                    className="original-link"
                    href={`/api/documents/${activeCitation.document_id}/file#page=${activeCitation.page}`}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    在新窗口打开原文 <ExternalLink size={12} />
                  </a>
                </div>
              ) : citations.length ? (
                <>
                  <p className="sources-description">
                    本次回答检索到的相关片段，
                    <br />
                    点击可查看原文与对应页码。
                  </p>
                  {citations.map((c) => (
                    <button
                      className="source-card"
                      key={c.chunk_id}
                      onClick={() => {
                        setActiveCitation(c);
                        setShowPreview(false);
                      }}
                    >
                      <div>
                        <span className="source-number">
                          {String(c.id).padStart(2, "0")}
                        </span>
                        <span>PDF · 第 {c.page} 页</span>
                        <ArrowUp size={13} />
                      </div>
                      <h3>{c.heading}</h3>
                      <p>{c.content.slice(0, 135)}</p>
                      <footer>
                        <FileText size={12} />
                        <span>{shortName(c.document_name)}</span>
                      </footer>
                    </button>
                  ))}
                </>
              ) : (
                <>
                  <div className="sources-empty">
                    <div className="source-illustration">
                      <FileText size={29} strokeWidth={1.3} />
                      <span>
                        <Search size={13} />
                      </span>
                    </div>
                    <h3>答案的出处，都在这里</h3>
                    <p>
                      提问后，我们会展示相关文档片段，
                      <br />
                      帮助你核对每一个重要信息。
                    </p>
                    <div className="empty-rule" />
                    <div className="source-feature">
                      <Check size={13} />
                      精确到页的文档引用
                    </div>
                    <div className="source-feature">
                      <Check size={13} />
                      随时查看上下文原文
                    </div>
                  </div>
                </>
              )}
            </div>
            <div className="source-bottom">
              <span className="status-dot green" />
              可追溯 · 可验证 · 有依据
            </div>
          </aside>
        </div>
        {!isChat &&
          (isKnowledge ? (
            renderPanel("documents", true)
          ) : (
            <div className="route-content">{children}</div>
          ))}
      </div>

      {modal && renderPanel(modal)}
    </div>
  );
}

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { Components } from "react-markdown";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type Paper = {
  id: string;
  library_id: string;
  title: string;
  abstract: string | null;
  authors: string[];
  year: number | null;
  doi: string | null;
  journal: string | null;
  source_file: string | null;
  shortlisted: boolean;
};

type Library = {
  id: string;
  name: string;
  paper_count: number;
};

type ChatRole = "user" | "assistant";

type ChatMsg = {
  role: ChatRole;
  content: string;
  citationByNumber?: Record<string, string>;
};

type RetrievalMode = "auto" | "always" | "never";

type StreamMeta = {
  type: "meta";
  retrieved: boolean;
  context_paper_ids: string[];
  citation_map?: Record<string, string>;
  estimated_context_tokens: number;
  context_near_limit: boolean;
  context_fallback?: boolean;
};

const TOKEN_WARN = 200_000;
const LS_LIB = "bibtalk-active-library";
const LS_SPLIT = "bibtalk-panel-width";

function normalizeChatMarkdown(source: string): string {
  return source
    .replace(/\r\n/g, "\n")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trimEnd();
}

/** Keeps ``` fenced blocks unchanged so [n] inside code is not turned into links. */
function transformCitationBrackets(
  source: string,
  citationByNumber: Record<string, string> | undefined
): string {
  if (!citationByNumber || Object.keys(citationByNumber).length === 0) {
    return source;
  }
  const fence = /```[\s\S]*?```/g;
  const out: string[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = fence.exec(source)) !== null) {
    out.push(
      transformCitationSegment(
        source.slice(last, m.index),
        citationByNumber
      )
    );
    out.push(m[0]);
    last = m.index + m[0].length;
  }
  out.push(transformCitationSegment(source.slice(last), citationByNumber));
  return out.join("");
}

function transformCitationSegment(
  segment: string,
  citationByNumber: Record<string, string>
): string {
  return segment.replace(/\[(\d+)\]/g, (full, num: string) => {
    if (citationByNumber[num]) {
      return `[${num}](#cite-${num})`;
    }
    return full;
  });
}

/** Heading line must be followed by a newline; body is everything before it. */
function splitSourcesSection(text: string): {
  body: string;
  heading: string | null;
  sources: string | null;
} {
  const re =
    /(?:^|\n)((?:#+\s*)?(?:\*\*)?(?:Sources|References|Bibliography)(?:\*\*)?:?(?:\s*\([^)]*\))?)\s*\n([\s\S]*)$/i;
  const m = text.match(re);
  if (!m || m.index === undefined) {
    return { body: text, heading: null, sources: null };
  }
  const body = text.slice(0, m.index).replace(/\s+$/, "");
  const heading = m[1].trim();
  const sources = m[2];
  return { body, heading, sources };
}

/** One markdown paragraph per numbered source when the model jams them on one line. */
function formatSourcesBlock(raw: string): string {
  const trimmed = raw.trim();
  if (!trimmed) return trimmed;
  const pieces = trimmed
    .split(/(?=\[\d+\])/g)
    .map((p) => p.trim())
    .filter(Boolean);
  if (pieces.length > 1) {
    return pieces.join("\n\n");
  }
  return trimmed;
}

function approxTokens(text: string) {
  return Math.max(1, Math.floor(text.length / 4));
}

function abstractPreview(text: string | null, maxLen: number) {
  if (!text?.trim()) return "—";
  const t = text.replace(/\s+/g, " ").trim();
  if (t.length <= maxLen) return t;
  return `${t.slice(0, maxLen)}…`;
}

function authorsPreview(authors: string[], maxAuthors: number) {
  if (!authors.length) return "—";
  const shown = authors.slice(0, maxAuthors);
  const suffix = authors.length > maxAuthors ? " et al." : "";
  return `${shown.join("; ")}${suffix}`;
}

async function fetchLibraries(): Promise<Library[]> {
  const res = await fetch("/api/libraries");
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

async function fetchPapers(libraryId: string, q: string): Promise<Paper[]> {
  const params = new URLSearchParams({ library_id: libraryId });
  if (q.trim()) params.set("q", q.trim());
  const res = await fetch(`/api/papers?${params.toString()}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

async function fetchPaper(id: string, libraryId: string): Promise<Paper> {
  const params = new URLSearchParams({ library_id: libraryId });
  const res = await fetch(
    `/api/papers/${encodeURIComponent(id)}?${params.toString()}`
  );
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

async function patchShortlist(paperIds: string[], shortlisted: boolean) {
  const res = await fetch("/api/papers/shortlist", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ paper_ids: paperIds, shortlisted }),
  });
  if (!res.ok) throw new Error(await res.text());
}

function AssistantMarkdown(props: {
  text: string;
  citationByNumber: Record<string, string> | undefined;
  onOpen: (num: string) => void;
}) {
  const { text, citationByNumber, onOpen } = props;

  const { body, heading, sources } = useMemo(() => {
    const normalized = normalizeChatMarkdown(text);
    return splitSourcesSection(normalized);
  }, [text]);

  const bodyMd = useMemo(
    () => transformCitationBrackets(body, citationByNumber),
    [body, citationByNumber]
  );

  const sourcesMd = useMemo(() => {
    if (heading == null || sources == null) return null;
    const block = formatSourcesBlock(sources);
    const light = block.replace(/\r\n/g, "\n").trimEnd();
    return transformCitationBrackets(light, citationByNumber);
  }, [heading, sources, citationByNumber]);

  const components = useMemo<Components>(
    () => ({
      a({ href, children }) {
        if (href?.startsWith("#cite-")) {
          const num = href.slice("#cite-".length);
          if (citationByNumber?.[num]) {
            return (
              <button
                type="button"
                className="cite-btn"
                title="Open paper details"
                onClick={() => onOpen(num)}
              >
                [{num}]
              </button>
            );
          }
        }
        return (
          <a href={href} target="_blank" rel="noreferrer">
            {children}
          </a>
        );
      },
    }),
    [citationByNumber, onOpen]
  );

  const showBody = bodyMd.trim().length > 0;
  const showSources =
    heading != null && sourcesMd != null && sourcesMd.trim().length > 0;

  return (
    <>
      {showBody ? (
        <div className="md-chat">
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
            {bodyMd}
          </ReactMarkdown>
        </div>
      ) : null}
      {showSources ? (
        <div className="md-chat md-chat-sources">
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
            {`${heading}\n\n${sourcesMd}`}
          </ReactMarkdown>
        </div>
      ) : null}
    </>
  );
}

export default function App() {
  const [libraries, setLibraries] = useState<Library[]>([]);
  const [selectedLibraryId, setSelectedLibraryId] = useState<string>(() =>
    localStorage.getItem(LS_LIB) ?? ""
  );

  const [papers, setPapers] = useState<Paper[]>([]);
  const [query, setQuery] = useState("");
  const [loadingPapers, setLoadingPapers] = useState(false);
  const [loadingLibs, setLoadingLibs] = useState(true);

  const [uploadTargetLib, setUploadTargetLib] = useState<string>("");
  const [newLibraryName, setNewLibraryName] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadMsg, setUploadMsg] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [leftWidth, setLeftWidth] = useState(() => {
    const raw = localStorage.getItem(LS_SPLIT);
    const n = raw ? parseInt(raw, 10) : 420;
    return Number.isFinite(n) ? Math.min(Math.max(n, 280), 900) : 420;
  });
  const leftWidthRef = useRef(leftWidth);
  useEffect(() => {
    leftWidthRef.current = leftWidth;
  }, [leftWidth]);

  const [splitDragging, setSplitDragging] = useState(false);
  const splitDragRef = useRef({ startX: 0, startW: 0 });

  useEffect(() => {
    if (!splitDragging) return;
    const onMove = (e: MouseEvent) => {
      const dx = e.clientX - splitDragRef.current.startX;
      const next = Math.min(
        Math.max(splitDragRef.current.startW + dx, 280),
        window.innerWidth * 0.78
      );
      setLeftWidth(next);
    };
    const onUp = () => {
      setSplitDragging(false);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      localStorage.setItem(LS_SPLIT, String(leftWidthRef.current));
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    return () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
  }, [splitDragging]);

  const startSplitDrag = (e: React.MouseEvent) => {
    e.preventDefault();
    splitDragRef.current = { startX: e.clientX, startW: leftWidth };
    setSplitDragging(true);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  };

  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [retrievalMode, setRetrievalMode] = useState<RetrievalMode>("auto");
  const [lastContextIds, setLastContextIds] = useState<string[]>([]);
  const [lastBaselineTokens, setLastBaselineTokens] = useState(0);
  const [lastRetrieved, setLastRetrieved] = useState<boolean | null>(null);
  const [chatError, setChatError] = useState<string | null>(null);
  const [modalPaper, setModalPaper] = useState<Paper | null>(null);

  const reloadLibraries = useCallback(async (preferLibraryId?: string) => {
    setLoadingLibs(true);
    try {
      const libs = await fetchLibraries();
      setLibraries(libs);
      setSelectedLibraryId((cur) => {
        if (libs.length === 0) return "";
        if (preferLibraryId && libs.some((l) => l.id === preferLibraryId)) {
          return preferLibraryId;
        }
        if (cur && libs.some((l) => l.id === cur)) return cur;
        const saved = localStorage.getItem(LS_LIB);
        if (saved && libs.some((l) => l.id === saved)) return saved;
        return libs[0].id;
      });
    } finally {
      setLoadingLibs(false);
    }
  }, []);

  const deleteLibraryById = useCallback(
    async (libraryId: string) => {
      const lib = libraries.find((l) => l.id === libraryId);
      const label = lib?.name ?? "this library";
      const n = lib?.paper_count ?? 0;
      if (
        !window.confirm(
          `Delete library "${label}" and all ${n} paper(s), including embeddings? This cannot be undone.`
        )
      ) {
        return;
      }
      setChatError(null);
      try {
        const res = await fetch(`/api/libraries/${encodeURIComponent(libraryId)}`, {
          method: "DELETE",
        });
        if (!res.ok) throw new Error(await res.text());
        setMessages([]);
        setLastContextIds([]);
        setLastBaselineTokens(0);
        setLastRetrieved(null);
        setDraft("");
        setModalPaper(null);
        setPapers([]);
        setUploadMsg(null);
        await reloadLibraries();
      } catch (e) {
        setChatError((e as Error).message);
      }
    },
    [libraries, reloadLibraries]
  );

  useEffect(() => {
    void reloadLibraries();
  }, [reloadLibraries]);

  useEffect(() => {
    if (selectedLibraryId) localStorage.setItem(LS_LIB, selectedLibraryId);
    else localStorage.removeItem(LS_LIB);
  }, [selectedLibraryId]);

  const shortlistedIds = useMemo(
    () => papers.filter((p) => p.shortlisted).map((p) => p.id),
    [papers]
  );

  const reloadPapers = useCallback(async () => {
    if (!selectedLibraryId) {
      setPapers([]);
      return;
    }
    setLoadingPapers(true);
    try {
      setPapers(await fetchPapers(selectedLibraryId, query));
    } finally {
      setLoadingPapers(false);
    }
  }, [selectedLibraryId, query]);

  useEffect(() => {
    const t = setTimeout(() => {
      void reloadPapers();
    }, 200);
    return () => clearTimeout(t);
  }, [reloadPapers]);

  useEffect(() => {
    if (!modalPaper) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setModalPaper(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [modalPaper]);

  const openCitation = useCallback(
    async (num: string, map: Record<string, string> | undefined) => {
      const id = map?.[num];
      if (!id || !selectedLibraryId) return;
      setChatError(null);
      try {
        setModalPaper(await fetchPaper(id, selectedLibraryId));
      } catch (e) {
        setChatError((e as Error).message);
      }
    },
    [selectedLibraryId]
  );

  const runUpload = async (files: FileList | null) => {
    if (!files?.length) return;
    setUploading(true);
    setUploadMsg(null);
    try {
      const fd = new FormData();
      for (const f of Array.from(files)) fd.append("files", f);
      if (uploadTargetLib) fd.append("library_id", uploadTargetLib);
      else if (newLibraryName.trim()) fd.append("new_library_name", newLibraryName.trim());

      const res = await fetch("/api/upload", { method: "POST", body: fd });
      if (!res.ok) throw new Error(await res.text());
      const data = (await res.json()) as {
        imported: number;
        skipped_duplicates: number;
        library_id: string;
        library_ids?: string[];
      };
      const libCount = data.library_ids?.length ?? (data.library_id ? 1 : 0);
      setUploadMsg(
        `Imported ${data.imported} new records (${data.skipped_duplicates} duplicates skipped)` +
          (libCount > 1 ? ` into ${libCount} libraries.` : ".")
      );
      setNewLibraryName("");
      await reloadLibraries(data.library_id);
      await fetchPapers(data.library_id, "").then(setPapers);
    } catch (e) {
      setUploadMsg((e as Error).message);
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const selectedLibraryName = useMemo(
    () => libraries.find((l) => l.id === selectedLibraryId)?.name ?? "",
    [libraries, selectedLibraryId]
  );

  const toggleShortlist = async (paper: Paper) => {
    const next = !paper.shortlisted;
    await patchShortlist([paper.id], next);
    setPapers((prev) =>
      prev.map((p) => (p.id === paper.id ? { ...p, shortlisted: next } : p))
    );
  };

  const streamingAssistantTokens = useMemo(() => {
    const last = messages[messages.length - 1];
    if (last?.role === "assistant") return approxTokens(last.content);
    return 0;
  }, [messages]);

  const totalApproxTokens = lastBaselineTokens + streamingAssistantTokens;

  const sendMessage = async () => {
    const text = draft.trim();
    if (!text || sending || !selectedLibraryId) return;

    setChatError(null);
    const snapshot = messages;
    const nextMessages: ChatMsg[] = [...messages, { role: "user", content: text }];
    const selectedIds =
      snapshot.length > 0 && lastContextIds.length > 0
        ? lastContextIds
        : shortlistedIds.length > 0
          ? shortlistedIds
          : [];

    setMessages([...nextMessages, { role: "assistant", content: "" }]);
    setDraft("");
    setSending(true);

    let assistant = "";

    try {
      const res = await fetch("/api/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          messages: nextMessages,
          library_id: selectedLibraryId,
          selected_paper_ids: selectedIds,
          retrieval_mode: retrievalMode,
        }),
      });
      if (!res.ok || !res.body) {
        throw new Error(await res.text());
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n");
        buffer = parts.pop() ?? "";
        for (const line of parts) {
          if (!line.trim()) continue;
          const evt = JSON.parse(line) as
            | StreamMeta
            | { type: "token"; text: string }
            | { type: "done" }
            | { type: "error"; message: string };

          if (evt.type === "meta") {
            const map =
              evt.citation_map ??
              Object.fromEntries(evt.context_paper_ids.map((id, i) => [String(i + 1), id]));
            setLastContextIds(evt.context_paper_ids);
            setLastBaselineTokens(evt.estimated_context_tokens);
            setLastRetrieved(evt.retrieved);
            setMessages((prev) => {
              const copy = [...prev];
              const li = copy.length - 1;
              if (li >= 0 && copy[li]?.role === "assistant") {
                copy[li] = { ...copy[li], citationByNumber: map };
              }
              return copy;
            });
          } else if (evt.type === "token") {
            assistant += evt.text;
            setMessages((prev) => {
              const copy = [...prev];
              const idx = copy.length - 1;
              if (idx >= 0 && copy[idx]?.role === "assistant") {
                copy[idx] = { ...copy[idx], content: assistant };
              }
              return copy;
            });
          } else if (evt.type === "error") {
            throw new Error(evt.message);
          }
        }
      }
    } catch (e) {
      setChatError((e as Error).message);
      setMessages(snapshot);
    } finally {
      setSending(false);
    }
  };

  const newChat = () => {
    setMessages([]);
    setLastContextIds([]);
    setLastBaselineTokens(0);
    setLastRetrieved(null);
    setChatError(null);
    setDraft("");
    setModalPaper(null);
  };

  const nearLimit =
    lastBaselineTokens >= TOKEN_WARN || totalApproxTokens >= TOKEN_WARN;

  const uploadCardDrop = (e: React.DragEvent) => {
    e.preventDefault();
    void runUpload(e.dataTransfer.files);
  };

  return (
    <div className="layout-flex">
      <div className="left-panel" style={{ width: leftWidth, maxWidth: "78vw" }}>
        <h1>BibTalk</h1>
        <p className="meta-line">
          Organise uploads into separate libraries. Chat and the paper list are scoped to the
          library you select.
        </p>

        <div className="library-toolbar library-toolbar-row">
          <label className="library-select-grow">
            Active library
            <select
              value={selectedLibraryId}
              onChange={(e) => setSelectedLibraryId(e.target.value)}
              disabled={loadingLibs || libraries.length === 0}
            >
              {libraries.length === 0 ? (
                <option value="">No libraries yet</option>
              ) : (
                libraries.map((lib) => (
                  <option key={lib.id} value={lib.id}>
                    {lib.name} ({lib.paper_count})
                  </option>
                ))
              )}
            </select>
          </label>
          <button
            type="button"
            className="btn-danger"
            disabled={!selectedLibraryId || loadingLibs}
            title="Delete this library and all its papers and embeddings"
            onClick={() => void deleteLibraryById(selectedLibraryId)}
          >
            Delete library
          </button>
        </div>

        <section
          className="upload-card"
          onDragOver={(e) => e.preventDefault()}
          onDrop={uploadCardDrop}
        >
          <div className="upload-card-head">
            <span className="upload-title">Import references</span>
            <span className="upload-sub">
              RIS · PubMed (.nbib) · Zotero JSON · Zotero SQLite
            </span>
          </div>
          <div className="upload-controls">
            <label>
              Merge into existing library (optional)
              <select
                value={uploadTargetLib}
                onChange={(e) => {
                  setUploadTargetLib(e.target.value);
                  if (e.target.value) setNewLibraryName("");
                }}
                disabled={libraries.length === 0}
              >
                <option value="">— Leave empty for separate libraries per file —</option>
                {libraries.map((lib) => (
                  <option key={lib.id} value={lib.id}>
                    {lib.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Or one new library for this whole batch
              <input
                type="text"
                placeholder="e.g. HPV screening review"
                value={newLibraryName}
                onChange={(e) => {
                  setNewLibraryName(e.target.value);
                  if (e.target.value.trim()) setUploadTargetLib("");
                }}
              />
            </label>
          </div>
          <div className="upload-actions">
            <button
              type="button"
              className="upload-btn"
              disabled={uploading}
              onClick={() => fileInputRef.current?.click()}
            >
              Choose files
            </button>
            <span className="upload-hint-inline">
              Multiple files with no target below → each file becomes its own library (named from the
              filename). Pick an existing library or enter one new name to merge a batch into a
              single library.
            </span>
            <input
              ref={fileInputRef}
              type="file"
              className="sr-only"
              multiple
              accept=".nbib,.ris,.txt,.json,.sqlite,.db,.wos,.medline"
              onChange={(e) => void runUpload(e.target.files)}
              disabled={uploading}
            />
          </div>
          {uploading ? (
            <p className="meta-line" style={{ marginTop: "0.5rem" }}>
              Uploading and embedding…
            </p>
          ) : null}
          {uploadMsg ? (
            <p className="meta-line" style={{ marginTop: "0.45rem" }}>
              {uploadMsg}
            </p>
          ) : null}
        </section>

        <h2>Papers in “{selectedLibraryName || "—"}”</h2>
        <div className="toolbar">
          <input
            type="search"
            placeholder="Search title, abstract, authors…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            disabled={!selectedLibraryId}
          />
          <button
            type="button"
            onClick={() => void reloadPapers()}
            disabled={loadingPapers || !selectedLibraryId}
          >
            Refresh
          </button>
          <span className="badge">{papers.length} in view</span>
        </div>

        <div className="table-wrap">
          {!selectedLibraryId ? (
            <div className="empty-state">Upload a file to create your first library.</div>
          ) : papers.length === 0 && !loadingPapers ? (
            <div className="empty-state">No papers match this filter.</div>
          ) : (
            <table className="papers-table">
              <thead>
                <tr>
                  <th className="col-short">★</th>
                  <th className="col-title">Title</th>
                  <th className="col-authors">Authors</th>
                  <th className="col-year">Year</th>
                  <th className="col-abstract">Abstract</th>
                </tr>
              </thead>
              <tbody>
                {papers.map((p) => (
                  <tr key={p.id}>
                    <td className="col-short">
                      <input
                        type="checkbox"
                        checked={p.shortlisted}
                        onChange={() => void toggleShortlist(p)}
                        aria-label="Shortlist for chat focus"
                      />
                    </td>
                    <td className="col-title">
                      <div className="cell-title">{p.title}</div>
                    </td>
                    <td className="col-authors">
                      <div className="cell-muted">{authorsPreview(p.authors, 4)}</div>
                    </td>
                    <td className="col-year">{p.year ?? "—"}</td>
                    <td className="col-abstract">
                      <div className="cell-muted">{abstractPreview(p.abstract, 160)}</div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <div
        className={`split-handle ${splitDragging ? "dragging" : ""}`}
        onMouseDown={startSplitDrag}
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize panels"
      />

      <div className="chat-panel">
        <h1>Chat</h1>
        <p className="meta-line">Scoped to library: {selectedLibraryName || "—"}.</p>

        <div className="toolbar">
          <label className="meta-line" style={{ display: "flex", gap: 8, alignItems: "center" }}>
            Retrieval
            <select
              value={retrievalMode}
              onChange={(e) => setRetrievalMode(e.target.value as RetrievalMode)}
              disabled={!selectedLibraryId}
            >
              <option value="auto">Auto (reuse last context when appropriate)</option>
              <option value="always">Always re-query embeddings</option>
              <option value="never">Never re-query (shortlist required)</option>
            </select>
          </label>
          <button type="button" onClick={newChat}>
            New conversation
          </button>
        </div>

        {nearLimit ? (
          <div className="banner warn">
            Estimated context is around {totalApproxTokens.toLocaleString()} tokens (threshold{" "}
            {TOKEN_WARN.toLocaleString()}). Start a new conversation to reset history and reduce
            cost or drift.
          </div>
        ) : (
          <div className="banner info">
            Context estimate after last model turn: ~{totalApproxTokens.toLocaleString()} tokens
            (very rough). Server flags near {TOKEN_WARN.toLocaleString()} tokens.
          </div>
        )}

        {lastRetrieved !== null ? (
          <div className="meta-line">
            Last turn: {lastRetrieved ? "vector search ran" : "reused prior paper set"} · Active
            papers in context: {lastContextIds.length}. Citations like [3] remain clickable.
          </div>
        ) : null}

        {chatError ? (
          <div className="banner warn" style={{ borderColor: "var(--danger)", color: "var(--danger)" }}>
            {chatError}
          </div>
        ) : null}

        <div className="messages">
          {!selectedLibraryId ? (
            <div className="meta-line">Select or create a library to start chatting.</div>
          ) : messages.length === 0 ? (
            <div className="meta-line">
              Ask about this library’s literature. Shortlist papers to steer the first retrieval;
              follow-ups reuse that set unless you ask to search again.
            </div>
          ) : null}
          {messages.map((m, idx) => (
            <div key={idx} className={`msg ${m.role}`}>
              {m.role === "assistant" ? (
                <AssistantMarkdown
                  text={m.content}
                  citationByNumber={m.citationByNumber}
                  onOpen={(n) => void openCitation(n, m.citationByNumber)}
                />
              ) : (
                m.content
              )}
            </div>
          ))}
        </div>

        <div className="composer">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder={
              selectedLibraryId
                ? "Ask about this library…"
                : "Create a library before chatting…"
            }
            disabled={sending || !selectedLibraryId}
          />
          <div className="toolbar">
            <button
              className="primary"
              type="button"
              disabled={sending || !selectedLibraryId}
              onClick={() => void sendMessage()}
            >
              Send
            </button>
          </div>
        </div>
      </div>

      {modalPaper ? (
        <div
          className="modal-backdrop"
          role="presentation"
          onClick={() => setModalPaper(null)}
        >
          <div
            className="modal-card"
            role="dialog"
            aria-modal="true"
            aria-labelledby="paper-modal-title"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              type="button"
              className="modal-close"
              aria-label="Close"
              onClick={() => setModalPaper(null)}
            >
              ×
            </button>
            <h3 id="paper-modal-title">{modalPaper.title}</h3>
            <p className="meta-line">
              {(modalPaper.authors || []).join("; ") || "Authors unknown"}{" "}
              {modalPaper.year != null ? `(${modalPaper.year})` : ""}
              {modalPaper.journal ? ` · ${modalPaper.journal}` : ""}
            </p>
            {modalPaper.doi ? (
              <p className="meta-line">
                <a href={`https://doi.org/${encodeURIComponent(modalPaper.doi)}`} target="_blank" rel="noreferrer">
                  DOI: {modalPaper.doi}
                </a>
              </p>
            ) : null}
            <div className="abstract-body">
              {modalPaper.abstract?.trim()
                ? modalPaper.abstract
                : "No abstract stored for this record."}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

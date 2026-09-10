"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { Message, Source } from "@/lib/types";

/**
 * Several retrieved chunks often come from one section — a long drug table, for
 * instance, is split across chunks that all sit under the same heading. Listing
 * them as identical rows reads like a bug, so group them while keeping every
 * citation number, since the answer's [n] markers point at individual chunks.
 */
function groupSources(sources: Source[]): { source: Source; indices: number[] }[] {
  const groups = new Map<string, { source: Source; indices: number[] }>();
  sources.forEach((source, i) => {
    const key = `${source.source_document}||${source.section_title}||${source.collection}`;
    const existing = groups.get(key);
    if (existing) {
      existing.indices.push(i + 1);
    } else {
      groups.set(key, { source, indices: [i + 1] });
    }
  });
  return Array.from(groups.values());
}

const RETRIEVAL_LABELS: Record<string, { text: string; className: string }> = {
  hybrid_rag: { text: "Hybrid RAG", className: "bg-teal-100 text-teal-800" },
  sql_rag: { text: "SQL RAG", className: "bg-violet-100 text-violet-800" },
  rbac_denied: { text: "Access restricted", className: "bg-amber-100 text-amber-800" },
};

export function MessageBubble({ message }: { message: Message }) {
  if (message.author === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] rounded-2xl rounded-br-sm bg-clinical-600 px-4 py-2.5 text-sm text-white">
          {message.text}
        </div>
      </div>
    );
  }

  const badge = message.retrievalType ? RETRIEVAL_LABELS[message.retrievalType] : null;

  return (
    <div className="flex justify-start">
      <div
        className={`max-w-[85%] rounded-2xl rounded-bl-sm border px-4 py-3 ${
          message.accessDenied
            ? "border-amber-200 bg-amber-50"
            : "border-slate-200 bg-white"
        }`}
      >
        {message.pending ? (
          <div className="flex items-center gap-2 text-sm text-slate-500">
            <span className="h-2 w-2 animate-pulse rounded-full bg-slate-400" />
            Searching your permitted documents…
          </div>
        ) : (
          <>
            <div className="text-sm leading-relaxed text-slate-800">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
                  strong: ({ children }) => (
                    <strong className="font-semibold text-slate-900">{children}</strong>
                  ),
                  ul: ({ children }) => (
                    <ul className="mb-2 list-disc space-y-1 pl-5 last:mb-0">{children}</ul>
                  ),
                  ol: ({ children }) => (
                    <ol className="mb-2 list-decimal space-y-1 pl-5 last:mb-0">{children}</ol>
                  ),
                  code: ({ children }) => (
                    <code className="rounded bg-slate-100 px-1 py-0.5 text-[12px]">
                      {children}
                    </code>
                  ),
                  // Clinical answers often cite dosage or tariff tables, so keep
                  // them readable and horizontally scrollable on narrow screens.
                  table: ({ children }) => (
                    <div className="my-2 overflow-x-auto">
                      <table className="w-full border-collapse text-xs">{children}</table>
                    </div>
                  ),
                  th: ({ children }) => (
                    <th className="border border-slate-200 bg-slate-50 px-2 py-1 text-left font-semibold">
                      {children}
                    </th>
                  ),
                  td: ({ children }) => (
                    <td className="border border-slate-200 px-2 py-1 align-top">{children}</td>
                  ),
                }}
              >
                {message.text}
              </ReactMarkdown>
            </div>

            {badge && (
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${badge.className}`}>
                  {badge.text}
                </span>
              </div>
            )}

            {message.sources && message.sources.length > 0 && (
              <div className="mt-3 border-t border-slate-100 pt-3">
                <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                  Sources
                </div>
                <ol className="space-y-1">
                  {groupSources(message.sources).map(({ source, indices }, index) => (
                    <li key={index} className="text-xs text-slate-600">
                      <span className="mr-1 font-medium text-slate-400">
                        [{indices.join(", ")}]
                      </span>
                      <span className="font-medium">{source.source_document}</span>
                      {source.section_title && <span> — {source.section_title}</span>}
                      <span className="ml-1.5 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-600">
                        {source.collection}
                      </span>
                    </li>
                  ))}
                </ol>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

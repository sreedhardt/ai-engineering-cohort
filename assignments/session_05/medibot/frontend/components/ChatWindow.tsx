"use client";

import { useEffect, useRef, useState } from "react";
import { askQuestion } from "@/lib/api";
import type { Message, Session } from "@/lib/types";
import { MessageBubble } from "./MessageBubble";
import { RoleSidebar } from "./RoleSidebar";

const SUGGESTIONS: Record<string, string[]> = {
  doctor: [
    "What is the standard adult dose of Amoxicillin?",
    "Summarise the sepsis treatment protocol.",
    "Show me the insurance billing codes.",
  ],
  nurse: [
    "What is the hand hygiene procedure?",
    "How do I manage an ICU central line?",
    "Show me all insurance billing codes.",
  ],
  billing_executive: [
    "How many claims are currently escalated?",
    "What are the pre-authorisation deadlines?",
    "What is the amoxicillin dosage?",
  ],
  technician: [
    "What does fault code F-05 mean?",
    "How often is the Bowie-Dick test run?",
    "List every ICD-10 code in the billing reference.",
  ],
  admin: [
    "Which equipment category has the most open tickets?",
    "What is the staff leave policy?",
    "Summarise the sepsis treatment protocol.",
  ],
};

let counter = 0;
const nextId = () => `m${counter++}`;

export function ChatWindow({ session, onSignOut }: { session: Session; onSignOut: () => void }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function send(question: string) {
    const trimmed = question.trim();
    if (!trimmed || busy) return;

    const pendingId = nextId();
    setMessages((prev) => [
      ...prev,
      { id: nextId(), author: "user", text: trimmed },
      { id: pendingId, author: "bot", text: "", pending: true },
    ]);
    setDraft("");
    setBusy(true);

    try {
      const response = await askQuestion(session.token, trimmed);
      setMessages((prev) =>
        prev.map((message) =>
          message.id === pendingId
            ? {
                ...message,
                pending: false,
                text: response.answer,
                sources: response.sources,
                retrievalType: response.retrieval_type,
                accessDenied: response.access_denied,
              }
            : message
        )
      );
    } catch (error) {
      setMessages((prev) =>
        prev.map((message) =>
          message.id === pendingId
            ? {
                ...message,
                pending: false,
                text: error instanceof Error ? error.message : "Something went wrong.",
                accessDenied: true,
              }
            : message
        )
      );
    } finally {
      setBusy(false);
    }
  }

  const suggestions = SUGGESTIONS[session.role] ?? [];

  return (
    <div className="flex min-h-screen flex-col md:flex-row">
      <RoleSidebar session={session} onSignOut={onSignOut} />

      <main className="flex min-h-screen flex-1 flex-col">
        <div className="flex-1 space-y-4 overflow-y-auto p-6">
          {messages.length === 0 && (
            <div className="mx-auto max-w-2xl pt-10 text-center">
              <h1 className="text-xl font-semibold">
                Hello, {session.displayName.split(" ")[0]}.
              </h1>
              <p className="mt-1 text-sm text-slate-600">
                Ask about the documents your role can access. Every answer cites
                its sources.
              </p>
              <div className="mt-6 grid gap-2 text-left sm:grid-cols-3">
                {suggestions.map((suggestion) => (
                  <button
                    key={suggestion}
                    onClick={() => send(suggestion)}
                    className="rounded-lg border border-slate-200 bg-white p-3 text-xs text-slate-700 transition hover:border-clinical-500 hover:bg-clinical-50"
                  >
                    {suggestion}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((message) => (
            <MessageBubble key={message.id} message={message} />
          ))}
          <div ref={endRef} />
        </div>

        <form
          onSubmit={(event) => {
            event.preventDefault();
            send(draft);
          }}
          className="border-t border-slate-200 bg-white p-4"
        >
          <div className="mx-auto flex max-w-3xl gap-2">
            <input
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder="Ask MediBot a question…"
              className="flex-1 rounded-lg border border-slate-300 px-4 py-2.5 text-sm outline-none focus:border-clinical-500 focus:ring-2 focus:ring-clinical-100"
            />
            <button
              type="submit"
              disabled={busy || !draft.trim()}
              className="rounded-lg bg-clinical-600 px-5 py-2.5 text-sm font-medium text-white transition hover:bg-clinical-700 disabled:opacity-40"
            >
              Send
            </button>
          </div>
        </form>
      </main>
    </div>
  );
}

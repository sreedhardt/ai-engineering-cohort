"use client";

import { useState } from "react";
import { login } from "@/lib/api";
import type { Session } from "@/lib/types";

const DEMO_ACCOUNTS = [
  { username: "dr.mehta", password: "doctor123", label: "Dr. Anjali Mehta", role: "doctor" },
  { username: "nurse.priya", password: "nurse123", label: "Priya Nair", role: "nurse" },
  { username: "billing.ravi", password: "billing123", label: "Ravi Kumar", role: "billing_executive" },
  { username: "tech.anand", password: "tech123", label: "Anand Rao", role: "technician" },
  { username: "admin.sys", password: "admin123", label: "System Admin", role: "admin" },
];

export function LoginForm({ onSignedIn }: { onSignedIn: (session: Session) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(user: string, pass: string) {
    setBusy(true);
    setError(null);
    try {
      onSignedIn(await login(user, pass));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed.");
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-5xl items-center justify-center px-6 py-12">
      <div className="grid w-full gap-8 md:grid-cols-2">
        <div className="flex flex-col justify-center">
          <div className="mb-2 text-3xl font-semibold tracking-tight">MediBot</div>
          <p className="mb-6 text-sm text-slate-600">
            MediAssist Health Network — internal assistant. Answers are drawn only
            from the documents your role is authorised to see.
          </p>

          <form
            onSubmit={(e) => {
              e.preventDefault();
              submit(username, password);
            }}
            className="space-y-3 rounded-xl border border-slate-200 bg-white p-5 shadow-sm"
          >
            <div>
              <label htmlFor="username" className="mb-1 block text-xs font-medium text-slate-600">
                Username
              </label>
              <input
                id="username"
                name="username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-clinical-500 focus:ring-2 focus:ring-clinical-100"
              />
            </div>
            <div>
              <label htmlFor="password" className="mb-1 block text-xs font-medium text-slate-600">
                Password
              </label>
              <input
                id="password"
                name="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-clinical-500 focus:ring-2 focus:ring-clinical-100"
              />
            </div>

            {error && (
              <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={busy || !username || !password}
              className="w-full rounded-lg bg-clinical-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-clinical-700 disabled:opacity-40"
            >
              {busy ? "Signing in…" : "Sign in"}
            </button>
          </form>
        </div>

        <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="mb-1 text-sm font-medium">Demo accounts</div>
          <p className="mb-4 text-xs text-slate-500">
            Each role sees a different slice of the knowledge base. Click to sign in.
          </p>
          <ul className="space-y-2">
            {DEMO_ACCOUNTS.map((account) => (
              <li key={account.username}>
                <button
                  onClick={() => submit(account.username, account.password)}
                  disabled={busy}
                  className="flex w-full items-center justify-between rounded-lg border border-slate-200 px-3 py-2 text-left transition hover:border-clinical-500 hover:bg-clinical-50 disabled:opacity-40"
                >
                  <span>
                    <span className="block text-sm font-medium">{account.label}</span>
                    <span className="block text-xs text-slate-500">{account.username}</span>
                  </span>
                  <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-600">
                    {account.role}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </main>
  );
}

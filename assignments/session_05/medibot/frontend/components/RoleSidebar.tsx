"use client";

import type { Session } from "@/lib/types";

const COLLECTION_LABELS: Record<string, string> = {
  general: "General & HR policy",
  clinical: "Clinical protocols",
  nursing: "Nursing procedures",
  billing: "Billing & insurance",
  equipment: "Equipment manuals",
};

const ALL_COLLECTIONS = ["general", "clinical", "nursing", "billing", "equipment"];
const SQL_ROLES = ["billing_executive", "admin"];

export function RoleSidebar({ session, onSignOut }: { session: Session; onSignOut: () => void }) {
  const permitted = new Set(session.collections);

  return (
    <aside className="flex w-full shrink-0 flex-col gap-5 border-b border-slate-200 bg-white p-5 md:h-screen md:w-72 md:border-b-0 md:border-r">
      <div>
        <div className="text-lg font-semibold tracking-tight">MediBot</div>
        <div className="text-xs text-slate-500">MediAssist Health Network</div>
      </div>

      <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
        <div className="text-sm font-medium">{session.displayName}</div>
        <div className="text-xs text-slate-500">{session.department}</div>
        <span className="mt-2 inline-block rounded-full bg-clinical-100 px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-clinical-700">
          {session.role.replace("_", " ")}
        </span>
      </div>

      <div>
        <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
          Document access
        </div>
        <ul className="space-y-1.5">
          {ALL_COLLECTIONS.map((collection) => {
            const allowed = permitted.has(collection);
            return (
              <li
                key={collection}
                className={`flex items-center gap-2 text-sm ${
                  allowed ? "text-slate-700" : "text-slate-400"
                }`}
              >
                <span
                  aria-hidden
                  className={`flex h-4 w-4 items-center justify-center rounded-full text-[10px] font-bold text-white ${
                    allowed ? "bg-emerald-500" : "bg-slate-300"
                  }`}
                >
                  {allowed ? "✓" : "✕"}
                </span>
                <span className={allowed ? "" : "line-through decoration-slate-300"}>
                  {COLLECTION_LABELS[collection]}
                </span>
              </li>
            );
          })}
        </ul>
      </div>

      <div>
        <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
          Analytics
        </div>
        <p className="text-sm text-slate-600">
          {SQL_ROLES.includes(session.role)
            ? "SQL queries over claims and maintenance data are enabled."
            : "SQL queries over operational data are not available to this role."}
        </p>
      </div>

      <button
        onClick={onSignOut}
        className="mt-auto rounded-lg border border-slate-300 px-3 py-2 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
      >
        Sign out
      </button>
    </aside>
  );
}

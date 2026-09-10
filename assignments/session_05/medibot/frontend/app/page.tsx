"use client";

import { useState } from "react";
import { ChatWindow } from "@/components/ChatWindow";
import { LoginForm } from "@/components/LoginForm";
import type { Session } from "@/lib/types";

export default function Home() {
  // Held in memory only: a page reload requires signing in again, and the token
  // never reaches localStorage where any script on the page could read it.
  const [session, setSession] = useState<Session | null>(null);

  if (!session) {
    return <LoginForm onSignedIn={setSession} />;
  }
  return <ChatWindow session={session} onSignOut={() => setSession(null)} />;
}

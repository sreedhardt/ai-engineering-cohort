import type { ChatResponse, Session } from "./types";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

class ApiError extends Error {}

async function parseError(response: Response, fallback: string): Promise<string> {
  try {
    const body = await response.json();
    return typeof body.detail === "string" ? body.detail : fallback;
  } catch {
    return fallback;
  }
}

export async function login(username: string, password: string): Promise<Session> {
  const response = await fetch(`${API}/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });

  if (!response.ok) {
    throw new ApiError(await parseError(response, "Sign-in failed."));
  }

  const data = await response.json();
  return {
    token: data.access_token,
    role: data.role,
    displayName: data.display_name,
    department: data.department,
    collections: data.collections,
  };
}

/** The request carries only a question — the role travels inside the token. */
export async function askQuestion(token: string, question: string): Promise<ChatResponse> {
  const response = await fetch(`${API}/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ question }),
  });

  if (response.status === 401) {
    throw new ApiError("Your session has expired. Please sign in again.");
  }
  if (!response.ok) {
    throw new ApiError(await parseError(response, "MediBot could not answer that."));
  }
  return response.json();
}

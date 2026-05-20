// shared/api.js
// Shared helpers for talking to the local MemOS server.
// All functions fail silently — if the server is offline, they return null/undefined
// and the calling content script carries on without doing anything.

const MEMOS_SERVER = "http://localhost:8765";

async function memosRecall(query, platform = "unknown") {
  try {
    const res = await fetch(`${MEMOS_SERVER}/recall`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, platform, budget: "medium" }),
    });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null; // server offline — fail silently
  }
}

async function memosRemember(userMessage, assistantResponse, platform = "unknown") {
  try {
    await fetch(`${MEMOS_SERVER}/remember`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_message: userMessage,
        assistant_response: assistantResponse,
        platform,
      }),
    });
  } catch {
    // fail silently
  }
}

async function memosHealth() {
  try {
    const res = await fetch(`${MEMOS_SERVER}/health`);
    return res.ok;
  } catch {
    return false;
  }
}

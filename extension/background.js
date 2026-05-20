// background.js — MemOS service worker
// Polls the local server every 30 s and stores the status so the popup can read it.

const SERVER_URL = "http://localhost:8765";

async function checkHealth() {
  try {
    const res = await fetch(`${SERVER_URL}/health`);
    if (!res.ok) throw new Error("not ok");
    const data = await res.json();
    chrome.storage.local.set({
      serverStatus: "connected",
      memoryCount: data.memories ?? 0,
    });
  } catch {
    chrome.storage.local.set({ serverStatus: "disconnected", memoryCount: 0 });
  }
}

// Check immediately on startup, then every 30 s
checkHealth();
setInterval(checkHealth, 30_000);

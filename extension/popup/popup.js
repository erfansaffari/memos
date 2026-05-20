// popup.js — reads server status from chrome.storage and renders the popup UI

chrome.storage.local.get(["serverStatus", "memoryCount"], ({ serverStatus, memoryCount }) => {
  const dot = document.getElementById("dot");
  const label = document.getElementById("status-label");
  const connectedView = document.getElementById("connected-view");
  const offlineView = document.getElementById("offline-view");
  const countEl = document.getElementById("memory-count");

  if (serverStatus === "connected") {
    dot.className = "dot connected";
    label.textContent = "Connected";
    connectedView.style.display = "block";
    countEl.textContent = memoryCount ?? 0;
  } else {
    dot.className = "dot disconnected";
    label.textContent = "Disconnected";
    offlineView.style.display = "block";
  }
});

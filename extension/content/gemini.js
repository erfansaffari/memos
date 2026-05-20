// content/gemini.js — MemOS memory injection for gemini.google.com
// See content/claude.js for full architecture notes.
// To update selectors: open gemini.google.com → F12 → Inspector → update below.

(function () {
  "use strict";

  const PLATFORM = "gemini";

  // ----- Selectors (May 2026) -----
  const INPUT_SELECTORS = [
    'div.ql-editor[contenteditable="true"]',
    'rich-textarea div[contenteditable="true"]',
    'div[contenteditable="true"][aria-label*="message" i]',
    'div[contenteditable="true"]',
  ];
  const SEND_SELECTORS = [
    "button.send-button",
    'button[aria-label="Send message"]',
    'button[aria-label*="send" i]',
    "button.submit",
  ];
  const RESPONSE_SELECTORS = [
    "model-response",
    ".model-response-text",
    '[data-message-author-role="assistant"]',
    ".response-content",
  ];

  function getInputBox() {
    for (const sel of INPUT_SELECTORS) {
      const el = document.querySelector(sel);
      if (el) return el;
    }
    return null;
  }

  function getSendButton() {
    for (const sel of SEND_SELECTORS) {
      const el = document.querySelector(sel);
      if (el) return el;
    }
    return null;
  }

  function getLastResponse() {
    for (const sel of RESPONSE_SELECTORS) {
      const els = document.querySelectorAll(sel);
      if (els.length) return els[els.length - 1];
    }
    return null;
  }

  let _cachedQuery = null;
  let _cachedRecall = null;
  let _prefetchTimer = null;
  let lastUserMessage = "";

  function schedulePrefetch() {
    clearTimeout(_prefetchTimer);
    _prefetchTimer = setTimeout(async () => {
      try {
        const input = getInputBox();
        if (!input) return;
        const query = input.innerText.trim();
        if (!query || query === _cachedQuery) return;
        const result = await memosRecall(query, PLATFORM);
        _cachedQuery = query;
        _cachedRecall = result;
      } catch { /* server offline */ }
    }, 300);
  }

  function attachInputListener() {
    const input = getInputBox();
    if (!input || input._memosListening) return;
    input._memosListening = true;
    input.addEventListener("input", schedulePrefetch);
  }

  async function dispatchContext() {
    const input = getInputBox();
    if (!input) return;
    const userMessage = input.innerText.trim();
    if (!userMessage) return;

    lastUserMessage = userMessage;

    try {
      let recall = (_cachedQuery === userMessage) ? _cachedRecall : null;
      if (!recall) {
        recall = await memosRecall(userMessage, PLATFORM);
        _cachedQuery = userMessage;
        _cachedRecall = recall;
      }
      if (recall && recall.context && recall.context.trim().length > 0) {
        document.dispatchEvent(
          new CustomEvent("__memos_inject_context", {
            detail: `[context]\n${recall.context}\n[/context]`,
          })
        );
      }
    } catch { /* server offline — send without context */ }
  }

  let _bypassClick = false;
  let _bypassEnter = false;

  document.addEventListener(
    "click",
    async (e) => {
      if (_bypassClick) return;
      const sendBtn = getSendButton();
      if (!sendBtn) return;
      if (sendBtn !== e.target && !sendBtn.contains(e.target)) return;
      const input = getInputBox();
      if (!input || !input.innerText.trim()) return;

      e.preventDefault();
      e.stopImmediatePropagation();
      attachInputListener();

      await dispatchContext();

      _bypassClick = true;
      sendBtn.click();
      _bypassClick = false;
    },
    true
  );

  document.addEventListener(
    "keydown",
    async (e) => {
      if (e.key !== "Enter" || e.shiftKey || _bypassEnter) return;
      const input = getInputBox();
      if (!input) return;
      if (!input.contains(document.activeElement) && document.activeElement !== input) return;
      if (!input.innerText.trim()) return;

      e.preventDefault();
      e.stopImmediatePropagation();

      await dispatchContext();

      _bypassEnter = true;
      input.dispatchEvent(
        new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true })
      );
      _bypassEnter = false;
    },
    true
  );

  function waitForResponseToFinish(container, callback) {
    let timer = null;
    const obs = new MutationObserver(() => {
      clearTimeout(timer);
      timer = setTimeout(() => { obs.disconnect(); callback(); }, 1500);
    });
    obs.observe(container, { childList: true, subtree: true, characterData: true });
    timer = setTimeout(() => { obs.disconnect(); callback(); }, 1500);
  }

  const pageObserver = new MutationObserver(() => {
    try {
      const lastResponse = getLastResponse();
      if (!lastResponse || lastResponse.dataset.memosProcessed) return;
      lastResponse.dataset.memosProcessed = "true";
      waitForResponseToFinish(lastResponse, () => {
        try {
          const assistantText = lastResponse.innerText.trim();
          if (assistantText && lastUserMessage) {
            memosRemember(lastUserMessage, assistantText, PLATFORM);
            lastUserMessage = "";
          }
        } catch { /* fail silently */ }
      });
    } catch { /* fail silently */ }
  });

  pageObserver.observe(document.body, { childList: true, subtree: true });

  attachInputListener();
  setTimeout(attachInputListener, 2000);
})();

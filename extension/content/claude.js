// content/claude.js — MemOS memory injection for claude.ai
//
// How context gets injected (invisible to user):
//   shared/interceptor.js (MAIN world) overrides window.fetch.
//   This script sends context to it via window.postMessage (guaranteed
//   cross-world because postMessage uses structured cloning).
//   The context is injected directly into Claude's API request body
//   so it never appears in the input box or conversation history UI.
//
// Fallback: if fetch interception didn't consume the context within 2 s
//   (body format mismatch), we fall back to prepending it in the input box.
//   In that case a DOM cleanup observer hides the [context] block from the
//   chat history immediately after Claude renders the user's message bubble.
//
// To update selectors: open claude.ai → F12 → Inspector → update below.

(function () {
  "use strict";

  const PLATFORM = "claude";

  // ----- DOM Selectors (May 2026) -----
  const INPUT_SELECTORS = [
    'div[contenteditable="true"][data-testid="message-input"]',
    'div.ProseMirror[contenteditable="true"]',
    'div[contenteditable="true"][role="textbox"]',
    'div[contenteditable="true"]',
  ];
  const SEND_SELECTORS = [
    'button[data-testid="send-button"]',
    'button[aria-label="Send message"]',
    'button[aria-label*="send" i]',
  ];
  const RESPONSE_SELECTORS = [
    '[data-testid="assistant-message"]',
    '.font-claude-message',
    '[data-message-author-role="assistant"]',
  ];
  const USER_MSG_SELECTORS = [
    '[data-testid="user-message"]',
    '[data-message-author-role="user"]',
    '.font-human-message',
    '.human-turn',
  ];

  function getInputBox() {
    for (const s of INPUT_SELECTORS) { const e = document.querySelector(s); if (e) return e; }
    return null;
  }
  function getSendButton() {
    for (const s of SEND_SELECTORS) { const e = document.querySelector(s); if (e) return e; }
    return null;
  }
  function getLastResponse() {
    for (const s of RESPONSE_SELECTORS) { const els = document.querySelectorAll(s); if (els.length) return els[els.length - 1]; }
    return null;
  }

  // ---------------------------------------------------------------------------
  // Prefetch cache — fills while the user is still typing so there's
  // zero wait when they hit send.
  // ---------------------------------------------------------------------------
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
        const q = input.innerText.trim();
        if (!q || q === _cachedQuery) return;
        const r = await memosRecall(q, PLATFORM);
        _cachedQuery = q;
        _cachedRecall = r;
        console.log("[MemOS] prefetched context for query:", q.slice(0, 40));
      } catch { /* server offline */ }
    }, 300);
  }

  function attachInputListener() {
    const input = getInputBox();
    if (!input || input._memosListening) return;
    input._memosListening = true;
    input.addEventListener("input", schedulePrefetch);
  }

  // ---------------------------------------------------------------------------
  // Set content in the React-controlled contenteditable
  // ---------------------------------------------------------------------------
  function setInputContent(input, text) {
    input.focus();
    const sel = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(input);
    sel.removeAllRanges();
    sel.addRange(range);
    const ok = document.execCommand("insertText", false, text);
    if (!ok) {
      input.innerText = text;
      input.dispatchEvent(new Event("input", { bubbles: true }));
    }
  }

  // ---------------------------------------------------------------------------
  // On send: get (or fetch) recall context, tell the interceptor, then
  // if the interceptor didn't consume it within 2 s fall back to injecting
  // the context directly into the input box.
  // ---------------------------------------------------------------------------
  let _contextSentAt = null;

  async function prepareContext(input) {
    const userMessage = input.innerText.trim();
    if (!userMessage) return;
    lastUserMessage = userMessage;

    try {
      let recall = (_cachedQuery === userMessage) ? _cachedRecall : null;
      if (!recall) recall = await memosRecall(userMessage, PLATFORM);
      _cachedQuery = userMessage;
      _cachedRecall = recall;

      if (recall && recall.context && recall.context.trim()) {
        const ctxBlock = `[context]\n${recall.context}\n[/context]`;

        // Primary: send to fetch interceptor via postMessage (cross-world safe)
        window.postMessage({ __memos_type: "inject_context", context: ctxBlock }, "*");
        _contextSentAt = Date.now();
        console.log("[MemOS] context dispatched to interceptor");

        // Fallback: if interceptor didn't consume within 2 s, inject into input
        setTimeout(() => {
          if (_contextSentAt && Date.now() - _contextSentAt >= 1900) {
            // Interceptor didn't fire — inject into input as fallback
            const inp = getInputBox();
            const currentMsg = inp ? inp.innerText.trim() : "";
            if (inp && currentMsg && !currentMsg.startsWith("[context]")) {
              console.log("[MemOS] fallback: injecting into input box");
              setInputContent(inp, `${ctxBlock}\n\n${currentMsg}`);
            }
            _contextSentAt = null;
          }
        }, 2000);
      }
    } catch { /* server offline */ }
  }

  // Called by interceptor.js (MAIN world) after successful injection
  window.addEventListener("message", (e) => {
    if (e.source !== window) return;
    if (e.data?.__memos_type === "context_injected") {
      _contextSentAt = null; // cancel fallback
    }
  });

  // ---------------------------------------------------------------------------
  // Intercept send button click and Enter key
  // ---------------------------------------------------------------------------
  let _bypassClick = false;
  let _bypassEnter = false;

  document.addEventListener("click", async (e) => {
    if (_bypassClick) return;
    const sendBtn = getSendButton();
    if (!sendBtn || (sendBtn !== e.target && !sendBtn.contains(e.target))) return;
    const input = getInputBox();
    if (!input || !input.innerText.trim()) return;

    e.preventDefault();
    e.stopImmediatePropagation();
    attachInputListener();

    await prepareContext(input);

    _bypassClick = true;
    sendBtn.click();
    _bypassClick = false;
  }, true);

  document.addEventListener("keydown", async (e) => {
    if (e.key !== "Enter" || e.shiftKey || _bypassEnter) return;
    const input = getInputBox();
    if (!input) return;
    if (!input.contains(document.activeElement) && document.activeElement !== input) return;
    if (!input.innerText.trim()) return;

    e.preventDefault();
    e.stopImmediatePropagation();

    await prepareContext(input);

    _bypassEnter = true;
    input.dispatchEvent(new KeyboardEvent("keydown", {
      key: "Enter", code: "Enter", keyCode: 13, which: 13,
      bubbles: true, cancelable: true
    }));
    _bypassEnter = false;
  }, true);

  // ---------------------------------------------------------------------------
  // DOM cleanup: if fallback ran and context block ended up in the chat
  // history, strip it from the rendered user message bubble immediately.
  // ---------------------------------------------------------------------------
  function cleanUserMessages() {
    for (const sel of USER_MSG_SELECTORS) {
      const msgs = document.querySelectorAll(sel);
      msgs.forEach((msg) => {
        if (msg.dataset.memosClean) return;
        if (!msg.innerText.includes("[context]")) return;
        msg.dataset.memosClean = "true";
        try {
          msg.innerHTML = msg.innerHTML.replace(
            /\[context\][\s\S]*?\[\/context\]\n?\n?/g, ""
          );
          console.log("[MemOS] stripped context block from chat bubble");
        } catch { /* fail silently */ }
      });
    }
  }

  // ---------------------------------------------------------------------------
  // Response observer: store memories after the AI finishes responding
  // ---------------------------------------------------------------------------
  function waitForStreamEnd(container, callback) {
    let timer = null;
    const obs = new MutationObserver(() => {
      clearTimeout(timer);
      timer = setTimeout(() => { obs.disconnect(); callback(); }, 1500);
    });
    obs.observe(container, { childList: true, subtree: true, characterData: true });
    timer = setTimeout(() => { obs.disconnect(); callback(); }, 1500);
  }

  const pageObserver = new MutationObserver(() => {
    // Clean up any context blocks that appeared in user message bubbles
    try { cleanUserMessages(); } catch { /* */ }

    // Watch for new assistant responses
    try {
      const last = getLastResponse();
      if (!last || last.dataset.memosProcessed) return;
      last.dataset.memosProcessed = "true";
      waitForStreamEnd(last, () => {
        try {
          const text = last.innerText.trim();
          if (text && lastUserMessage) {
            memosRemember(lastUserMessage, text, PLATFORM);
            lastUserMessage = "";
          }
        } catch { /* */ }
      });
    } catch { /* */ }
  });

  pageObserver.observe(document.body, { childList: true, subtree: true });
  attachInputListener();
  setTimeout(attachInputListener, 2000);
})();

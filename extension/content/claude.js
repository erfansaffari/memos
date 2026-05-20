// content/claude.js — MemOS memory injection for claude.ai
//
// Architecture:
//   - shared/interceptor.js (main world) overrides window.fetch and waits for
//     a "__memos_inject_context" CustomEvent. When the platform sends an API
//     request, interceptor.js injects the context at the network level.
//     The user's input box is NEVER modified — context is invisible in the UI.
//   - This script (isolated world) does two things:
//       1. Prefetch: call memosRecall while the user is still typing (debounced
//          300ms) so the result is cached before they hit send.
//       2. On send: dispatch the cached context to the interceptor, then let the
//          original click/keydown proceed normally (no blocking).
//
// To update selectors when Claude changes its UI:
//   Open claude.ai → F12 → Inspector → find the element → update below.

(function () {
  "use strict";

  const PLATFORM = "claude";

  // ----- Selectors (May 2026) -----
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
    '.assistant-message',
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

  // ---------------------------------------------------------------------------
  // Prefetch cache — populated while the user is still typing
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
        const query = input.innerText.trim();
        if (!query || query === _cachedQuery) return;
        const result = await memosRecall(query, PLATFORM);
        _cachedQuery = query;
        _cachedRecall = result;
      } catch {
        // server offline — ignore
      }
    }, 300);
  }

  // Attach prefetch listener once the input appears
  function attachInputListener() {
    const input = getInputBox();
    if (!input || input._memosListening) return;
    input._memosListening = true;
    input.addEventListener("input", schedulePrefetch);
  }

  // ---------------------------------------------------------------------------
  // Dispatch context to the main-world fetch interceptor
  // ---------------------------------------------------------------------------

  async function dispatchContext() {
    const input = getInputBox();
    if (!input) return;
    const userMessage = input.innerText.trim();
    if (!userMessage) return;

    lastUserMessage = userMessage;

    try {
      // Use cached result if the query matches; otherwise fetch now (cold send)
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
    } catch {
      // server offline — send without context
    }
  }

  // ---------------------------------------------------------------------------
  // Send interception — dispatch context then let the original event through
  // The send button click and Enter key are NOT blocked; we just fire-and-forget
  // the context dispatch and let the platform's own handler run normally.
  // Since the interceptor operates at the fetch level, timing is fine as long
  // as dispatchContext() resolves before the platform's fetch call is made.
  // For cached results this is synchronous; for cold sends, we still block
  // briefly (< 200ms over localhost).
  // ---------------------------------------------------------------------------

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

  // ---------------------------------------------------------------------------
  // Response observer — store memories after AI finishes responding
  // ---------------------------------------------------------------------------

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

  // Attach prefetch listener as soon as the page is usable
  attachInputListener();
  // Retry in case the input renders after script execution
  setTimeout(attachInputListener, 2000);
})();

// content/claude.js — MemOS memory injection for claude.ai
//
// To update selectors when Claude changes its UI:
//   1. Open claude.ai, press F12 → Inspector
//   2. Click on the input box / send button / response div
//   3. Copy a stable selector and update the constants below
//   4. Reload the extension at chrome://extensions

(function () {
  "use strict";

  const PLATFORM = "claude";

  // ----- Selectors (May 2026) -----
  // INPUT: Claude uses a ProseMirror contenteditable div.
  // Try multiple in order — first match wins.
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
  // Inject text into a React-controlled contenteditable
  // execCommand goes through the browser's native editing pipeline, which
  // React hooks into via its synthetic event system.
  // ---------------------------------------------------------------------------
  function setInputContent(input, text) {
    input.focus();
    // Select all existing content
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(input);
    selection.removeAllRanges();
    selection.addRange(range);
    // insertText fires a proper InputEvent that React picks up
    const ok = document.execCommand("insertText", false, text);
    if (!ok) {
      // Fallback for environments where execCommand is restricted
      input.innerText = text;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
    }
  }

  // ---------------------------------------------------------------------------
  // Step 1 — Intercept send
  //
  // Critical design: we MUST call e.preventDefault() + e.stopImmediatePropagation()
  // to block the original send, await the recall, inject the context, THEN
  // re-fire the event with a bypass flag so our own listener ignores it.
  // Without this, the message is sent before memosRecall even returns.
  // ---------------------------------------------------------------------------

  let _bypassClick = false;
  let _bypassEnter = false;
  let lastUserMessage = "";

  async function handleSend(input) {
    const userMessage = input.innerText.trim();
    if (!userMessage) return;

    lastUserMessage = userMessage;

    try {
      const recall = await memosRecall(userMessage, PLATFORM);
      if (recall && recall.context && recall.context.trim().length > 0) {
        const withContext = `[context]\n${recall.context}\n[/context]\n\n${userMessage}`;
        setInputContent(input, withContext);
      }
    } catch {
      // server offline or error — leave input unchanged, send as-is
    }
  }

  // Click interception
  document.addEventListener(
    "click",
    async (e) => {
      if (_bypassClick) return; // our own re-triggered click — let it through

      const sendBtn = getSendButton();
      if (!sendBtn) return;
      if (sendBtn !== e.target && !sendBtn.contains(e.target)) return;

      const input = getInputBox();
      if (!input || !input.innerText.trim()) return;

      e.preventDefault();
      e.stopImmediatePropagation();

      await handleSend(input);

      // Re-fire the click so Claude's handler actually sends the message
      _bypassClick = true;
      sendBtn.click();
      _bypassClick = false;
    },
    true
  );

  // Enter key interception
  document.addEventListener(
    "keydown",
    async (e) => {
      if (e.key !== "Enter" || e.shiftKey) return;
      if (_bypassEnter) return; // our own re-triggered keydown

      const input = getInputBox();
      if (!input) return;
      // Only intercept when focus is inside the input
      if (!input.contains(document.activeElement) && document.activeElement !== input) return;
      if (!input.innerText.trim()) return;

      e.preventDefault();
      e.stopImmediatePropagation();

      await handleSend(input);

      // Re-fire Enter so Claude's handler sends
      _bypassEnter = true;
      input.dispatchEvent(
        new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true })
      );
      _bypassEnter = false;
    },
    true
  );

  // ---------------------------------------------------------------------------
  // Step 2 — Wait for AI response to finish streaming
  // No new DOM mutations for 1.5 s = streaming is done.
  // ---------------------------------------------------------------------------

  function waitForResponseToFinish(container, callback) {
    let debounceTimer = null;
    const observer = new MutationObserver(() => {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        observer.disconnect();
        callback();
      }, 1500);
    });
    observer.observe(container, {
      childList: true,
      subtree: true,
      characterData: true,
    });
    // Kick-start the timer in case the response is already done
    debounceTimer = setTimeout(() => {
      observer.disconnect();
      callback();
    }, 1500);
  }

  // ---------------------------------------------------------------------------
  // Step 3 — Watch for new assistant messages and store them
  // ---------------------------------------------------------------------------

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
        } catch {
          // fail silently
        }
      });
    } catch {
      // fail silently
    }
  });

  pageObserver.observe(document.body, { childList: true, subtree: true });
})();

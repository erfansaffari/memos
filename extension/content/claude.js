// content/claude.js — MemOS memory injection for claude.ai
//
// DOM selectors (correct as of May 2026 — update here if Claude changes its UI):
//   INPUT_SELECTOR  — the contenteditable message input box
//   SEND_SELECTOR   — the send button
//   RESPONSE_SELECTOR — each assistant message container
//
// To update selectors: open DevTools on claude.ai → Inspector → find the element.

(function () {
  "use strict";

  const PLATFORM = "claude";

  // ----- Selectors -----
  // Update these if Claude changes its UI.
  const INPUT_SELECTOR = 'div[contenteditable="true"][data-testid="message-input"]';
  const SEND_SELECTOR = 'button[data-testid="send-button"]';
  const RESPONSE_SELECTOR = '[data-testid="assistant-message"]';

  let lastUserMessage = ""; // clean user message, without the prepended context
  let isProcessing = false;

  // ---------------------------------------------------------------------------
  // Step 1 — Intercept send
  // ---------------------------------------------------------------------------

  function getInputBox() {
    return document.querySelector(INPUT_SELECTOR);
  }

  function getSendButton() {
    return document.querySelector(SEND_SELECTOR);
  }

  async function handleSend() {
    if (isProcessing) return;
    const input = getInputBox();
    if (!input) return;

    const userMessage = input.innerText.trim();
    if (!userMessage) return;

    isProcessing = true;
    lastUserMessage = userMessage;

    try {
      const recall = await memosRecall(userMessage, PLATFORM);

      if (recall && recall.context && recall.context.trim().length > 0) {
        const withContext =
          `[context]\n${recall.context}\n[/context]\n\n${userMessage}`;
        input.innerText = withContext;
        // Notify React of the change so it enables the send button
        input.dispatchEvent(new Event("input", { bubbles: true }));
      }
    } catch {
      // fail silently — user message unchanged
    }

    isProcessing = false;
  }

  // Intercept send button click (capture phase so we run before Claude's handlers)
  document.addEventListener(
    "click",
    (e) => {
      const sendBtn = getSendButton();
      if (sendBtn && (sendBtn === e.target || sendBtn.contains(e.target))) {
        handleSend();
      }
    },
    true
  );

  // Intercept Enter key (Shift+Enter = newline, plain Enter = send)
  document.addEventListener(
    "keydown",
    (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        const input = getInputBox();
        if (input && document.activeElement === input) {
          handleSend();
        }
      }
    },
    true
  );

  // ---------------------------------------------------------------------------
  // Step 2 — Wait for AI response to finish streaming
  // ---------------------------------------------------------------------------

  function waitForResponseToFinish(container, callback) {
    let debounceTimer = null;
    const observer = new MutationObserver(() => {
      clearTimeout(debounceTimer);
      // 1.5 s of no mutations = streaming is done
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
  }

  // ---------------------------------------------------------------------------
  // Step 3 — Watch for new assistant messages and store them
  // ---------------------------------------------------------------------------

  const pageObserver = new MutationObserver(() => {
    try {
      const responses = document.querySelectorAll(RESPONSE_SELECTOR);
      const lastResponse = responses[responses.length - 1];
      if (!lastResponse || lastResponse.dataset.memosProcessed) return;

      // Mark immediately so we don't process the same element twice
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

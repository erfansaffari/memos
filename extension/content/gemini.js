// content/gemini.js — MemOS memory injection for gemini.google.com
//
// To update selectors when Gemini changes its UI:
//   1. Open gemini.google.com, press F12 → Inspector
//   2. Click on the input box / send button / response div
//   3. Copy a stable selector and update the constants below
//   4. Reload the extension at chrome://extensions

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

  function setInputContent(input, text) {
    input.focus();
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(input);
    selection.removeAllRanges();
    selection.addRange(range);
    const ok = document.execCommand("insertText", false, text);
    if (!ok) {
      input.innerText = text;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
    }
  }

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
      // server offline — leave input unchanged
    }
  }

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

      await handleSend(input);

      _bypassClick = true;
      sendBtn.click();
      _bypassClick = false;
    },
    true
  );

  document.addEventListener(
    "keydown",
    async (e) => {
      if (e.key !== "Enter" || e.shiftKey) return;
      if (_bypassEnter) return;

      const input = getInputBox();
      if (!input) return;
      if (!input.contains(document.activeElement) && document.activeElement !== input) return;
      if (!input.innerText.trim()) return;

      e.preventDefault();
      e.stopImmediatePropagation();

      await handleSend(input);

      _bypassEnter = true;
      input.dispatchEvent(
        new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true })
      );
      _bypassEnter = false;
    },
    true
  );

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
    debounceTimer = setTimeout(() => {
      observer.disconnect();
      callback();
    }, 1500);
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

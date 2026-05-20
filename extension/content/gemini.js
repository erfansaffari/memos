// content/gemini.js — MemOS memory injection for gemini.google.com
// See content/claude.js for architecture notes.
// Update selectors: gemini.google.com → F12 → Inspector.
//
// IMPORTANT: Gemini's web app uses an internal Google endpoint (not the public
// generativelanguage.googleapis.com) with a non-JSON body format (protobuf-JSON
// or form-encoded). The fetch interceptor cannot reliably parse this body, so we
// use DIRECT INPUT MODIFICATION instead. A MutationObserver immediately strips
// the [context] block from the rendered user bubble so it stays invisible.

(function () {
  "use strict";

  const PLATFORM = "gemini";

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
    ".response-container",
    '[data-message-author-role="assistant"]',
  ];
  const USER_MSG_SELECTORS = [
    ".user-query-text",
    ".user-query-text-container",
    ".human-turn",
    '[data-message-author-role="user"]',
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
  // Prefetch while typing (150ms debounce)
  // ---------------------------------------------------------------------------
  let _cachedQuery = null, _cachedRecall = null, _prefetchTimer = null;
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
        _cachedQuery = q; _cachedRecall = r;
      } catch { /* server offline */ }
    }, 150);
  }

  function attachInputListener() {
    const input = getInputBox();
    if (!input || input._memosListening) return;
    input._memosListening = true;
    input.addEventListener("input", schedulePrefetch);
  }

  // ---------------------------------------------------------------------------
  // Input content injection (Quill editor compatible via execCommand)
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
  // prepareContext — inject context directly into the Quill input box.
  // Gemini's internal API uses a non-JSON body format, so fetch interception
  // is not viable. We prepend a [context] block, send the message, then strip
  // it from the rendered user bubble via cleanUserMessages().
  // ---------------------------------------------------------------------------
  async function prepareContext(input) {
    const userMessage = input.innerText.trim();
    if (!userMessage) return;
    lastUserMessage = userMessage;
    try {
      let recall = (_cachedQuery === userMessage) ? _cachedRecall : null;
      if (!recall) recall = await memosRecall(userMessage, PLATFORM);
      _cachedQuery = userMessage; _cachedRecall = recall;
      if (recall && recall.context && recall.context.trim()) {
        const ctxBlock = `[context]\n${recall.context}\n[/context]`;
        setInputContent(input, `${ctxBlock}\n\n${userMessage}`);
      }
    } catch { /* server offline */ }
  }

  // ---------------------------------------------------------------------------
  // Send interception — block the original event, inject context, re-send
  //
  // For BOTH click and Enter: always trigger the final send via sendBtn.click().
  // Gemini also ignores synthetic keyboard events in some builds, so we avoid
  // re-firing KeyboardEvents entirely.
  // ---------------------------------------------------------------------------
  let _bypassClick = false;

  async function handleSend(e) {
    if (_bypassClick) return;
    const input = getInputBox();
    if (!input || !input.innerText.trim()) return;

    e.preventDefault();
    e.stopImmediatePropagation();
    attachInputListener();

    await prepareContext(input);

    const sendBtn = getSendButton();
    if (sendBtn) {
      _bypassClick = true;
      sendBtn.click();
      _bypassClick = false;
    }
  }

  // Click on send button
  document.addEventListener("click", async (e) => {
    if (_bypassClick) return;
    const sendBtn = getSendButton();
    if (!sendBtn || (sendBtn !== e.target && !sendBtn.contains(e.target))) return;
    await handleSend(e);
  }, true);

  // Enter key (Gemini's Quill editor submits on plain Enter, not Ctrl+Enter)
  document.addEventListener("keydown", async (e) => {
    if (e.key !== "Enter" || e.shiftKey) return;
    const input = getInputBox();
    if (!input) return;
    if (!input.contains(document.activeElement) && document.activeElement !== input) return;
    if (!input.innerText.trim()) return;
    await handleSend(e);
  }, true);

  // ---------------------------------------------------------------------------
  // DOM cleanup: remove [context] block from rendered user message bubbles
  // ---------------------------------------------------------------------------
  function cleanUserMessages() {
    for (const sel of USER_MSG_SELECTORS) {
      document.querySelectorAll(sel).forEach((msg) => {
        if (msg.dataset.memosClean || !msg.innerText.includes("[context]")) return;
        msg.dataset.memosClean = "true";
        try { msg.innerHTML = msg.innerHTML.replace(/\[context\][\s\S]*?\[\/context\]\n?\n?/g, ""); } catch { /* */ }
      });
    }
  }

  // ---------------------------------------------------------------------------
  // Response observer — store memories after AI finishes responding
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
    try { cleanUserMessages(); } catch { /* */ }
    try {
      const last = getLastResponse();
      if (!last || last.dataset.memosProcessed) return;
      last.dataset.memosProcessed = "true";
      waitForStreamEnd(last, () => {
        try {
          const t = last.innerText.trim();
          if (t && lastUserMessage) { memosRemember(lastUserMessage, t, PLATFORM); lastUserMessage = ""; }
        } catch { /* */ }
      });
    } catch { /* */ }
  });

  pageObserver.observe(document.body, { childList: true, subtree: true });
  attachInputListener();
  setTimeout(attachInputListener, 2000);
})();

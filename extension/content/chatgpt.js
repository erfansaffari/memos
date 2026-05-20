// content/chatgpt.js — MemOS memory injection for chatgpt.com
// See content/claude.js for full architecture notes.
// Update selectors: chatgpt.com → F12 → Inspector.

(function () {
  "use strict";

  const PLATFORM = "chatgpt";

  const INPUT_SELECTORS = [
    'div#prompt-textarea[contenteditable="true"]',
    'div[contenteditable="true"][id="prompt-textarea"]',
    'div.ProseMirror[contenteditable="true"]',
    'div[contenteditable="true"][role="textbox"]',
  ];
  const SEND_SELECTORS = [
    'button[data-testid="send-button"]',
    'button[aria-label="Send message"]',
    'button[aria-label*="send" i]',
  ];
  const RESPONSE_SELECTORS = [
    '[data-message-author-role="assistant"]',
    '[data-testid="assistant-message"]',
  ];
  const USER_MSG_SELECTORS = [
    '[data-message-author-role="user"]',
    '[data-testid="user-message"]',
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
    }, 300);
  }

  function attachInputListener() {
    const input = getInputBox();
    if (!input || input._memosListening) return;
    input._memosListening = true;
    input.addEventListener("input", schedulePrefetch);
  }

  function setInputContent(input, text) {
    input.focus();
    const sel = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(input);
    sel.removeAllRanges(); sel.addRange(range);
    const ok = document.execCommand("insertText", false, text);
    if (!ok) { input.innerText = text; input.dispatchEvent(new Event("input", { bubbles: true })); }
  }

  function getOrCreateCtxEl() {
    let el = document.getElementById("__memos_ctx");
    if (!el) { el = document.createElement("meta"); el.id = "__memos_ctx"; document.head.appendChild(el); }
    return el;
  }

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
        getOrCreateCtxEl().setAttribute("data-ctx", ctxBlock);
        document.dispatchEvent(new Event("__memos_set_context"));
      }
    } catch { /* */ }
  }

  let _bypassClick = false, _bypassEnter = false;

  document.addEventListener("click", async (e) => {
    if (_bypassClick) return;
    const sendBtn = getSendButton();
    if (!sendBtn || (sendBtn !== e.target && !sendBtn.contains(e.target))) return;
    const input = getInputBox();
    if (!input || !input.innerText.trim()) return;
    e.preventDefault(); e.stopImmediatePropagation(); attachInputListener();
    await prepareContext(input);
    _bypassClick = true; sendBtn.click(); _bypassClick = false;
  }, true);

  document.addEventListener("keydown", async (e) => {
    if (e.key !== "Enter" || e.shiftKey || _bypassEnter) return;
    const input = getInputBox();
    if (!input) return;
    if (!input.contains(document.activeElement) && document.activeElement !== input) return;
    if (!input.innerText.trim()) return;
    e.preventDefault(); e.stopImmediatePropagation();
    await prepareContext(input);
    _bypassEnter = true;
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true, cancelable: true }));
    _bypassEnter = false;
  }, true);

  function cleanUserMessages() {
    for (const sel of USER_MSG_SELECTORS) {
      document.querySelectorAll(sel).forEach((msg) => {
        if (msg.dataset.memosClean || !msg.innerText.includes("[context]")) return;
        msg.dataset.memosClean = "true";
        try { msg.innerHTML = msg.innerHTML.replace(/\[context\][\s\S]*?\[\/context\]\n?\n?/g, ""); } catch { /* */ }
      });
    }
  }

  function waitForStreamEnd(container, callback) {
    let timer = null;
    const obs = new MutationObserver(() => { clearTimeout(timer); timer = setTimeout(() => { obs.disconnect(); callback(); }, 1500); });
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
        try { const t = last.innerText.trim(); if (t && lastUserMessage) { memosRemember(lastUserMessage, t, PLATFORM); lastUserMessage = ""; } } catch { /* */ }
      });
    } catch { /* */ }
  });

  pageObserver.observe(document.body, { childList: true, subtree: true });
  attachInputListener();
  setTimeout(attachInputListener, 2000);
})();

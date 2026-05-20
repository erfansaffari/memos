// shared/interceptor.js — runs in the PAGE's MAIN JavaScript world
// (manifest.json: world:"MAIN", run_at:"document_start")
//
// Communication from isolated-world content scripts:
//   We need SYNCHRONOUS delivery so _pendingContext is set BEFORE
//   sendBtn.click() triggers Claude's fetch.
//
//   window.postMessage is ASYNC (next event-loop task) — too late.
//   document.dispatchEvent(CustomEvent) is SYNC for same-world scripts,
//   but detail may not cross worlds.
//
//   Solution: content script writes context to a DOM attribute on a
//   shared <meta> element, then dispatches a plain Event (no detail needed).
//   DOM attributes are strings and are readable from both worlds.
//   Plain Event dispatch IS synchronous — listener runs before dispatch returns.

(function () {
  "use strict";

  let _pendingContext = null;

  // Sync receive — content script writes to #__memos_ctx, fires this event
  document.addEventListener("__memos_set_context", () => {
    const el = document.getElementById("__memos_ctx");
    _pendingContext = el ? el.getAttribute("data-ctx") : null;
    console.log("[MemOS interceptor] context ready, length:", _pendingContext?.length ?? 0);
  });

  console.log("[MemOS interceptor] loaded");

  const _origFetch = window.fetch.bind(window);

  window.fetch = async function (resource, init, ...rest) {
    const url =
      typeof resource === "string" ? resource
      : resource?.url ?? "";

    // Match any POST request on the current AI site.
    // Claude uses relative URLs like /api/... so we must also accept them.
    const isRelative = !url.startsWith("http") && !url.startsWith("//");
    const isAiCall =
      init?.method === "POST" && (
        isRelative ||
        url.includes("claude.ai") ||
        url.includes("chatgpt.com") ||
        url.includes("generativelanguage.googleapis.com")
      );

    if (_pendingContext && isAiCall && init?.body) {
      const ctx = _pendingContext;
      _pendingContext = null;

      console.log("[MemOS interceptor] intercepting:", url.split("?")[0].slice(-60));

      try {
        const bodyStr = typeof init.body === "string" ? init.body : null;
        if (!bodyStr) throw new Error("body is not a plain string");

        const body = JSON.parse(bodyStr);
        let injected = false;

        // ── Claude legacy: { prompt: "\n\nHuman: <msg>\n\nAssistant:" } ──
        if (typeof body.prompt === "string" && body.prompt.trim()) {
          const marker = "\n\nHuman:";
          const idx = body.prompt.lastIndexOf(marker);
          if (idx !== -1) {
            const split = idx + marker.length + 1; // +1 for the space after "Human:"
            body.prompt =
              body.prompt.slice(0, split) +
              ctx + "\n\n" +
              body.prompt.slice(split);
          } else {
            body.prompt = ctx + "\n\n" + body.prompt;
          }
          injected = true;
          console.log("[MemOS interceptor] injected into prompt (legacy)");
        }

        // ── OpenAI / Claude messages: { messages: [{role, content}] } ──
        else if (Array.isArray(body.messages)) {
          for (let i = body.messages.length - 1; i >= 0; i--) {
            const msg = body.messages[i];
            if (msg.role === "user" || msg.role === "human") {
              if (typeof msg.content === "string") {
                msg.content = ctx + "\n\n" + msg.content;
              } else if (Array.isArray(msg.content)) {
                const tp = msg.content.find((p) => p.type === "text");
                if (tp) tp.text = ctx + "\n\n" + tp.text;
                else msg.content.unshift({ type: "text", text: ctx });
              }
              injected = true;
              console.log("[MemOS interceptor] injected into messages[]");
              break;
            }
          }
        }

        // ── Gemini: { contents: [{role, parts: [{text}]}] } ──
        else if (Array.isArray(body.contents)) {
          for (let i = body.contents.length - 1; i >= 0; i--) {
            const item = body.contents[i];
            if (item.role === "user") {
              const tp = (item.parts || []).find((p) => typeof p.text === "string");
              if (tp) { tp.text = ctx + "\n\n" + tp.text; injected = true; }
              break;
            }
          }
          if (injected) console.log("[MemOS interceptor] injected into contents[]");
        }

        if (!injected) {
          console.warn("[MemOS interceptor] body format not recognised — top-level keys:", Object.keys(body).join(", "));
          _pendingContext = ctx; // restore so next matching request can try
        } else {
          init = { ...init, body: JSON.stringify(body) };
        }
      } catch (err) {
        console.warn("[MemOS interceptor] parse error:", err.message);
        _pendingContext = ctx; // restore
      }
    }

    return _origFetch(resource, init, ...rest);
  };
})();

// shared/interceptor.js — runs in the PAGE's MAIN JavaScript world
// (declared in manifest.json with world:"MAIN", run_at:"document_start")
//
// Overrides window.fetch to inject MemOS context into AI API requests.
// Receives context from the isolated-world content scripts via window.postMessage.
// CustomEvent.detail does NOT reliably cross the isolated→MAIN world boundary,
// so we use postMessage (structured-clone, guaranteed to work).

(function () {
  "use strict";

  let _pendingContext = null;

  // Receive context from isolated-world content script
  window.addEventListener("message", (e) => {
    if (e.source !== window) return;
    if (!e.data || e.data.__memos_type !== "inject_context") return;
    _pendingContext = e.data.context || null;
    console.log("[MemOS interceptor] context ready, length:", _pendingContext?.length);
  });

  console.log("[MemOS interceptor] fetch override installed");

  const _originalFetch = window.fetch.bind(window);

  window.fetch = async function (resource, init, ...rest) {
    const url =
      typeof resource === "string"
        ? resource
        : resource && resource.url
        ? resource.url
        : "";

    // Match AI conversation POST requests broadly by domain
    const isAiCall =
      (url.includes("claude.ai") && init?.method === "POST") ||
      (url.includes("chatgpt.com") && url.includes("/conversation")) ||
      (url.includes("generativelanguage.googleapis.com") && init?.method === "POST");

    if (_pendingContext && isAiCall && init?.body) {
      const ctx = _pendingContext;
      _pendingContext = null;

      console.log("[MemOS interceptor] intercepting:", url.split("?")[0]);

      try {
        const bodyStr = typeof init.body === "string" ? init.body : null;
        if (!bodyStr) throw new Error("body is not a string");

        const body = JSON.parse(bodyStr);
        let injected = false;

        // Claude format 1: { prompt: "...Human: <msg>..." }
        if (typeof body.prompt === "string" && body.prompt.trim()) {
          const humanIdx = body.prompt.lastIndexOf("\n\nHuman:");
          if (humanIdx !== -1) {
            const before = body.prompt.slice(0, humanIdx + "\n\nHuman: ".length);
            const after = body.prompt.slice(humanIdx + "\n\nHuman: ".length);
            body.prompt = before + ctx + "\n\n" + after;
          } else {
            body.prompt = ctx + "\n\n" + body.prompt;
          }
          injected = true;
          console.log("[MemOS interceptor] injected into prompt field");
        }

        // Claude / OpenAI format: { messages: [{role, content}] }
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
              console.log("[MemOS interceptor] injected into messages field");
              break;
            }
          }
        }

        // Gemini format: { contents: [{role, parts: [{text}]}] }
        else if (Array.isArray(body.contents)) {
          for (let i = body.contents.length - 1; i >= 0; i--) {
            const item = body.contents[i];
            if (item.role === "user") {
              const parts = item.parts || [];
              const tp = parts.find((p) => typeof p.text === "string");
              if (tp) { tp.text = ctx + "\n\n" + tp.text; injected = true; }
              break;
            }
          }
          if (injected) console.log("[MemOS interceptor] injected into contents field");
        }

        if (!injected) {
          console.warn("[MemOS interceptor] body format not recognised — keys:", Object.keys(body).join(", "));
          _pendingContext = ctx; // restore: try next request
        } else {
          init = { ...init, body: JSON.stringify(body) };
        }
      } catch (err) {
        console.warn("[MemOS interceptor] body parse failed:", err.message);
        _pendingContext = ctx; // restore
      }
    }

    return _originalFetch(resource, init, ...rest);
  };
})();

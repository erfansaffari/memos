// shared/interceptor.js
// Runs in the PAGE's main JavaScript world (see manifest.json world: "MAIN").
// Overrides window.fetch so context can be injected at the network level —
// the user's visible input is never touched and the context never appears
// in the conversation history UI.
//
// Communication: content scripts dispatch a "__memos_inject_context" CustomEvent
// on `document` with the context string as `detail`. This script listens for it
// and injects the context into the very next matching AI API request.
//
// If the request body can't be parsed (non-JSON, unexpected format), the request
// is forwarded unchanged — fail silently, never break the platform.

(function () {
  "use strict";

  let _pendingContext = null;

  // Receive context from the isolated-world content script
  document.addEventListener("__memos_inject_context", (e) => {
    _pendingContext = e.detail || null;
  });

  const _originalFetch = window.fetch.bind(window);

  window.fetch = async function (resource, init, ...rest) {
    const url =
      typeof resource === "string"
        ? resource
        : resource && resource.url
        ? resource.url
        : "";

    // Only attempt injection on POST requests that look like AI API calls
    const isAiCall =
      (url.includes("claude.ai") &&
        (url.includes("/completion") ||
          url.includes("/messages") ||
          url.includes("/append_message"))) ||
      (url.includes("chatgpt.com") && url.includes("/conversation")) ||
      url.includes("generativelanguage.googleapis.com");

    if (_pendingContext && isAiCall && init?.method === "POST" && init?.body) {
      const ctx = _pendingContext;
      _pendingContext = null; // consume immediately so we don't double-inject

      try {
        const body = JSON.parse(init.body);
        let injected = false;

        // ── Claude format: { prompt: "...\n\nHuman: <msg>\n\nAssistant:" } ──
        if (typeof body.prompt === "string" && body.prompt.trim()) {
          // Insert context right before the last Human: turn
          const humanIdx = body.prompt.lastIndexOf("\n\nHuman:");
          if (humanIdx !== -1) {
            body.prompt =
              body.prompt.slice(0, humanIdx) +
              "\n\nHuman: " +
              ctx +
              "\n\n" +
              body.prompt.slice(humanIdx + "\n\nHuman: ".length);
          } else {
            body.prompt = ctx + "\n\n" + body.prompt;
          }
          injected = true;
        }

        // ── OpenAI / ChatGPT format: { messages: [{role, content}] } ──
        else if (Array.isArray(body.messages)) {
          for (let i = body.messages.length - 1; i >= 0; i--) {
            const msg = body.messages[i];
            if (msg.role === "user" || msg.role === "human") {
              if (typeof msg.content === "string") {
                msg.content = ctx + "\n\n" + msg.content;
                injected = true;
              } else if (Array.isArray(msg.content)) {
                // Vision/multi-modal format: [{type: "text", text: "..."}]
                const textPart = msg.content.find(
                  (p) => p.type === "text" && typeof p.text === "string"
                );
                if (textPart) {
                  textPart.text = ctx + "\n\n" + textPart.text;
                  injected = true;
                } else {
                  msg.content.unshift({ type: "text", text: ctx });
                  injected = true;
                }
              }
              break;
            }
          }
        }

        // ── Gemini format: { contents: [{role, parts: [{text}]}] } ──
        else if (Array.isArray(body.contents)) {
          for (let i = body.contents.length - 1; i >= 0; i--) {
            const item = body.contents[i];
            if (item.role === "user") {
              const parts = item.parts || [];
              const tp = parts.find((p) => typeof p.text === "string");
              if (tp) {
                tp.text = ctx + "\n\n" + tp.text;
                injected = true;
              }
              break;
            }
          }
        }

        if (injected) {
          init = { ...init, body: JSON.stringify(body) };
        }
        // If not injected (unknown format): request goes through unchanged
      } catch {
        // Parsing failed — forward original request, don't swallow the context
        // (another request may match shortly)
        _pendingContext = ctx;
      }
    }

    return _originalFetch(resource, init, ...rest);
  };
})();

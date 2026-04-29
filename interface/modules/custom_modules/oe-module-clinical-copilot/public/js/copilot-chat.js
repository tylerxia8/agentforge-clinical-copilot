/**
 * Clinical Co-Pilot — chat panel UI.
 *
 * Vanilla JS (no jQuery dependency, even though OpenEMR ships it). The
 * panel root in the DOM carries the endpoint URL, CSRF token, and
 * patient pid as data-attributes — set in PatientViewedListener.php.
 */

(() => {
    "use strict";

    const panel = document.getElementById("copilot-panel");
    if (!panel) return;

    const endpoint = panel.dataset.endpoint;
    const csrf = panel.dataset.csrf;
    const messagesEl = document.getElementById("copilot-messages");
    const form = document.getElementById("copilot-form");
    const input = document.getElementById("copilot-input");
    const closeBtn = panel.querySelector(".copilot-close");

    // Conversation history sent to the agent service. We keep this
    // client-side for v1; persistence to oe_copilot_messages is a
    // Thursday/Sunday TODO (see Http/CopilotController.php).
    const history = [];
    let conversationId = null;
    let pending = false;

    closeBtn?.addEventListener("click", () => {
        panel.classList.toggle("copilot-collapsed");
    });

    form.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        if (pending) return;

        const text = input.value.trim();
        if (!text) return;

        appendMessage("user", text);
        input.value = "";
        pending = true;

        const thinkingEl = appendMessage("assistant", "Thinking…", { ephemeral: true });

        try {
            const response = await fetch(endpoint, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRF-Token": csrf,
                },
                body: JSON.stringify({
                    message: text,
                    conversation_id: conversationId,
                    history: history,
                }),
            });

            thinkingEl.remove();

            if (!response.ok) {
                appendMessage(
                    "assistant",
                    `Error: server returned ${response.status}.`,
                    { error: true },
                );
                return;
            }

            const body = await response.json();

            if (body.refused) {
                appendMessage(
                    "assistant",
                    body.text || body.refusal_reason || "Co-pilot refused the request.",
                    { error: true },
                );
                return;
            }

            const replyText = body.text ?? "(empty response)";
            const sources = Array.isArray(body.sources) ? body.sources : [];
            appendMessage("assistant", replyText, { sources });

            // Roll the user/assistant turn into history for the next turn.
            history.push({ role: "user", content: text });
            history.push({ role: "assistant", content: replyText });
            if (body.conversation_id) conversationId = body.conversation_id;
        } catch (err) {
            thinkingEl.remove();
            appendMessage(
                "assistant",
                "Network error talking to the co-pilot service.",
                { error: true },
            );
            console.error("[copilot]", err);
        } finally {
            pending = false;
            input.focus();
        }
    });

    /**
     * @param {"user"|"assistant"} role
     * @param {string} text
     * @param {{ ephemeral?: boolean, error?: boolean, sources?: string[] }} [opts]
     */
    function appendMessage(role, text, opts = {}) {
        const el = document.createElement("div");
        el.className = `copilot-msg copilot-msg-${role}`;
        if (opts.error) el.classList.add("copilot-msg-error");
        if (opts.ephemeral) el.classList.add("copilot-msg-ephemeral");

        const body = document.createElement("div");
        body.className = "copilot-msg-body";
        body.textContent = text;
        el.appendChild(body);

        if (opts.sources?.length) {
            const sources = document.createElement("div");
            sources.className = "copilot-msg-sources";
            sources.textContent = "Sources: " + opts.sources.join(", ");
            el.appendChild(sources);
        }

        messagesEl.appendChild(el);
        messagesEl.scrollTop = messagesEl.scrollHeight;
        return el;
    }
})();

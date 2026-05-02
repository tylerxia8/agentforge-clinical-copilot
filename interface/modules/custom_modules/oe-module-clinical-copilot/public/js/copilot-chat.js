/**
 * Clinical Co-Pilot — chat panel UI.
 *
 * Vanilla JS (no jQuery dependency, even though OpenEMR ships it). The
 * panel root in the DOM carries the endpoint URL, CSRF token, patient
 * pid, and patient name as data-attributes — set in
 * PatientViewedListener.php.
 */

(() => {
    "use strict";

    const panel = document.getElementById("copilot-panel");
    if (!panel) return;

    const endpoint = panel.dataset.endpoint;
    const csrf = panel.dataset.csrf;
    const patientPid = panel.dataset.patientPid;
    const messagesEl = document.getElementById("copilot-messages");
    const form = document.getElementById("copilot-form");
    const input = document.getElementById("copilot-input");
    const closeBtn = panel.querySelector(".copilot-close");
    const suggestionsEl = document.getElementById("copilot-suggestions");

    // Patient-scoped history. If the user navigates to a different chart
    // and the panel is re-rendered for a new pid, the old in-memory
    // history is irrelevant — we key on pid to be sure.
    const history = [];
    const historyForPid = patientPid;
    let conversationId = null;
    let pending = false;

    closeBtn?.addEventListener("click", () => {
        panel.classList.toggle("copilot-collapsed");
    });

    suggestionsEl?.querySelectorAll("button[data-q]").forEach((btn) => {
        btn.addEventListener("click", () => {
            input.value = btn.dataset.q;
            form.requestSubmit();
        });
    });

    form.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        if (pending) return;

        const text = input.value.trim();
        if (!text) return;

        // Hide suggestions once the conversation starts.
        if (suggestionsEl && !suggestionsEl.hidden) suggestionsEl.hidden = true;

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
                    pid: historyForPid,
                }),
            });

            thinkingEl.remove();

            if (!response.ok) {
                appendMessage(
                    "assistant",
                    `Server returned ${response.status}. Try again in a moment.`,
                    { error: true },
                );
                return;
            }

            const body = await response.json();

            if (body.refused) {
                appendMessage(
                    "assistant",
                    body.text || body.refusal_reason || "Co-pilot declined the request.",
                    { refusal: true },
                );
                return;
            }

            const replyText = body.text ?? "(empty response)";
            const sources = Array.isArray(body.sources) ? body.sources : [];
            appendMessage("assistant", replyText, { sources });

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
     * @param {{ ephemeral?: boolean, error?: boolean, refusal?: boolean, sources?: string[] }} [opts]
     */
    function appendMessage(role, text, opts = {}) {
        const el = document.createElement("div");
        el.className = `copilot-msg copilot-msg-${role}`;
        if (opts.error) el.classList.add("copilot-msg-error");
        if (opts.refusal) el.classList.add("copilot-msg-refusal");
        if (opts.ephemeral) el.classList.add("copilot-msg-ephemeral");

        const body = document.createElement("div");
        body.className = "copilot-msg-body";

        // For assistant messages, strip inline citations from the prose
        // (we render them as chips below) and parse the remaining markdown.
        // User messages and ephemerals are plain text.
        if (role === "assistant" && !opts.ephemeral && !opts.error && !opts.refusal) {
            const inlineCitations = extractCitations(text);
            const cleanText = stripCitations(text);
            body.innerHTML = renderMarkdown(cleanText);
            const merged = mergeCitations(opts.sources || [], inlineCitations);
            if (merged.length) {
                el.appendChild(body);
                el.appendChild(renderCitationChips(merged));
                messagesEl.appendChild(el);
                messagesEl.scrollTop = messagesEl.scrollHeight;
                return el;
            }
        } else {
            body.textContent = text;
        }

        el.appendChild(body);
        messagesEl.appendChild(el);
        messagesEl.scrollTop = messagesEl.scrollHeight;
        return el;
    }

    // Match FHIR citations like "[MedicationRequest#a1ab5c8a-4811-...]"
    // and legacy "[prescriptions#244]" tool-row ids.
    const CITATION_RE = /\[([A-Za-z_][A-Za-z0-9_]*)#([A-Za-z0-9._-]+)\]/g;

    function extractCitations(text) {
        const out = [];
        let m;
        CITATION_RE.lastIndex = 0;
        while ((m = CITATION_RE.exec(text)) !== null) {
            out.push(`${m[1]}#${m[2]}`);
        }
        return out;
    }

    function stripCitations(text) {
        return text
            .replace(CITATION_RE, "")
            // collapse the " ." or "  ," that's left after stripping a
            // trailing citation, and shrink runs of spaces.
            .replace(/[ \t]+([.,;:!?])/g, "$1")
            .replace(/[ \t]{2,}/g, " ")
            .replace(/\n{3,}/g, "\n\n")
            .trim();
    }

    function mergeCitations(sources, inline) {
        const seen = new Set();
        const merged = [];
        for (const c of [...sources, ...inline]) {
            if (typeof c !== "string" || !c.includes("#")) continue;
            if (seen.has(c)) continue;
            seen.add(c);
            merged.push(c);
        }
        return merged;
    }

    function renderCitationChips(citations) {
        const wrap = document.createElement("div");
        wrap.className = "copilot-msg-sources";
        const counts = {};
        for (const c of citations) {
            const type = c.split("#")[0];
            counts[type] = (counts[type] || 0) + 1;
        }
        const summary = Object.entries(counts)
            .map(([type, n]) => `${n} ${humanType(type, n)}`)
            .join(" · ");
        const label = document.createElement("button");
        label.type = "button";
        label.className = "copilot-sources-toggle";
        label.textContent = `Sources: ${summary}`;
        label.setAttribute("aria-expanded", "false");

        const detail = document.createElement("ul");
        detail.className = "copilot-sources-detail";
        detail.hidden = true;
        for (const c of citations) {
            const li = document.createElement("li");
            li.textContent = c;
            detail.appendChild(li);
        }

        label.addEventListener("click", () => {
            const open = !detail.hidden;
            detail.hidden = open;
            label.setAttribute("aria-expanded", String(!open));
        });

        wrap.appendChild(label);
        wrap.appendChild(detail);
        return wrap;
    }

    function humanType(type, n) {
        const map = {
            MedicationRequest: ["med", "meds"],
            Condition: ["problem", "problems"],
            AllergyIntolerance: ["allergy", "allergies"],
            Encounter: ["encounter", "encounters"],
            Observation: ["observation", "observations"],
            Patient: ["patient", "patients"],
            Immunization: ["immunization", "immunizations"],
        };
        const pair = map[type];
        if (pair) return n === 1 ? pair[0] : pair[1];
        return type.toLowerCase();
    }

    /**
     * Minimal Markdown → HTML renderer. Supports:
     *   - paragraphs separated by blank lines
     *   - `- ` / `* ` bullet lists (single level)
     *   - `**bold**` and `*italic*` (and `_italic_`)
     *   - `# / ## / ###` headings (rendered as small bold lines)
     *   - inline `code`
     * Escapes everything else. Deliberately tiny — chat content from the
     * agent is bounded by the system prompt, no need for a real parser.
     */
    function renderMarkdown(text) {
        const lines = text.split("\n");
        const out = [];
        let para = [];
        let list = null;

        const flushPara = () => {
            if (!para.length) return;
            out.push(`<p>${formatInline(para.join(" "))}</p>`);
            para = [];
        };
        const flushList = () => {
            if (!list) return;
            out.push(`<ul>${list.map((li) => `<li>${formatInline(li)}</li>`).join("")}</ul>`);
            list = null;
        };

        for (const raw of lines) {
            const line = raw.replace(/\s+$/, "");
            if (line === "") {
                flushPara();
                flushList();
                continue;
            }
            const headingMatch = /^(#{1,3})\s+(.+)$/.exec(line);
            if (headingMatch) {
                flushPara();
                flushList();
                const level = headingMatch[1].length;
                out.push(
                    `<div class="copilot-md-h copilot-md-h${level}">${formatInline(headingMatch[2])}</div>`,
                );
                continue;
            }
            const bullet = /^\s*[-*]\s+(.+)$/.exec(line);
            if (bullet) {
                flushPara();
                if (!list) list = [];
                list.push(bullet[1]);
                continue;
            }
            flushList();
            para.push(line);
        }
        flushPara();
        flushList();
        return out.join("");
    }

    function formatInline(s) {
        // Escape HTML first so user-controlled content can't inject tags.
        let safe = s
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;");
        // Inline code
        safe = safe.replace(/`([^`]+)`/g, "<code>$1</code>");
        // Bold then italic. Order matters: handle ** before *.
        safe = safe.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
        safe = safe.replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>");
        safe = safe.replace(/(^|[\s(])_([^_\n]+)_(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>");
        return safe;
    }
})();

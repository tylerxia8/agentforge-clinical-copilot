"""Tokenize PHI before sending to the LLM; rehydrate before returning to the UI.

The LLM never sees real names, MRNs, SSNs, full DOBs, emails, or phone
numbers. It sees stable per-turn tokens (`[PT_NAME_1]`, age buckets,
relative dates) and the prompt is built around those.

The token map is request-scoped: it lives only for the duration of one
turn. We do not persist it, and it does not leave this process.

See ARCHITECTURE.md §2.5 / §5.4.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

# PHI fields we know about by name from the OpenEMR schema. Free-text
# fields (note bodies, lab text) need a generic PII pass — TODO(thursday).
KNOWN_PHI_FIELDS: frozenset[str] = frozenset({
    "fname", "lname", "mname", "fullname", "name",
    "ss", "ssn",
    "DOB", "dob", "date_of_birth",
    "phone_home", "phone_cell", "phone_biz", "phone_contact",
    "email", "email_direct",
    "street", "street2", "city", "postal_code", "country_code",
    "mrn", "pubpid",
})


@dataclass
class TokenMap:
    """Bidirectional map: real PHI value ⇆ stable placeholder.

    Tokens are stable within one turn — the same name maps to the same
    placeholder every time it appears, so the LLM can refer back to "the
    patient" coherently.
    """

    _to_token: dict[str, str] = field(default_factory=dict)
    _from_token: dict[str, str] = field(default_factory=dict)
    _counters: dict[str, int] = field(default_factory=dict)

    def _next(self, kind: str) -> str:
        n = self._counters.get(kind, 0) + 1
        self._counters[kind] = n
        return f"[{kind}_{n}]"

    def _intern(self, value: str, kind: str) -> str:
        if value in self._to_token:
            return self._to_token[value]
        token = self._next(kind)
        self._to_token[value] = token
        self._from_token[token] = value
        return token

    # --- field-level transforms ---

    def _redact_dob(self, dob: str) -> str:
        try:
            d = datetime.strptime(dob, "%Y-%m-%d").date()
        except ValueError:
            return self._intern(dob, "DATE")
        age = (date.today() - d).days // 365
        bucket = (age // 5) * 5
        return f"[AGE_{bucket}-{bucket+4}]"

    def _redact_field(self, field_name: str, value: Any) -> Any:
        if value is None or value == "":
            return value
        s = str(value)
        if field_name in {"fname", "lname", "mname", "fullname", "name"}:
            return self._intern(s, "PT_NAME")
        if field_name in {"ss", "ssn"}:
            return self._intern(s, "SSN")
        if field_name in {"DOB", "dob", "date_of_birth"}:
            return self._redact_dob(s)
        if field_name in {"phone_home", "phone_cell", "phone_biz", "phone_contact"}:
            return self._intern(s, "PHONE")
        if field_name in {"email", "email_direct"}:
            return self._intern(s, "EMAIL")
        if field_name in {"street", "street2", "city", "postal_code", "country_code"}:
            return self._intern(s, "ADDR")
        if field_name in {"mrn", "pubpid"}:
            return self._intern(s, "MRN")
        return value

    # --- bulk transforms ---

    def tokenize_dict(self, obj: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if isinstance(v, dict):
                out[k] = self.tokenize_dict(v)
            elif isinstance(v, list):
                out[k] = [self.tokenize_dict(x) if isinstance(x, dict) else x for x in v]
            elif k in KNOWN_PHI_FIELDS:
                out[k] = self._redact_field(k, v)
            else:
                out[k] = v
        return out

    def tokenize_text(self, text: str) -> str:
        # Free-text needs a real PII pass (Presidio / regexes). For now,
        # only replace literal occurrences of values we've already tokenized.
        for value, token in self._to_token.items():
            if value:
                text = text.replace(value, token)
        return text

    # --- response-side ---

    _TOKEN_RE = re.compile(r"\[(PT_NAME|SSN|PHONE|EMAIL|ADDR|MRN|DATE)_\d+\]")

    def rehydrate(self, text: str) -> str:
        def _swap(m: re.Match[str]) -> str:
            return self._from_token.get(m.group(0), m.group(0))
        return self._TOKEN_RE.sub(_swap, text)

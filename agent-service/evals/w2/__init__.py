"""W2 eval gate — boolean rubrics, category-grouped pass rates,
PR-blocking CI hook.

Layout:

- :mod:`copilot.evals.w2.rubric`     — the 5 boolean checkers required
                                       by the PRD: schema_valid,
                                       citation_present,
                                       factually_consistent,
                                       safe_refusal, no_phi_in_logs
- :mod:`copilot.evals.w2.cases`      — the case definitions
- :mod:`copilot.evals.w2.runner`     — fire + grade + report
- ``baseline.json``                  — last-good per-category pass
                                       rates the CI gate compares
                                       against

Run:

::

    cd agent-service
    AGENT_URL=https://copilot-agent-production-ba87.up.railway.app \\
    AGENT_SHARED_SECRET=... \\
    python -m evals.w2.runner --json > results.json
"""

# Vulnerability Disclosure SOP — internal triage flow

**Audience:** GhostPort maintainers (currently the operator, plus any AI agents
working under their direction). The external-facing equivalent is
`SECURITY.md` at the repo root and the public bounty page at
`https://blog.ghostporttechnologies.com/bug-bounty.html`. This document covers
what happens *after* a report arrives, end to end.

**Rule origin:** 2026-05-03 — T-0091 audit found that SECURITY.md promised a
response timeline (48h ack, 7–30d patch) without an internal flow documenting
who does what when. This SOP closes that gap.

---

## 1. Where reports arrive

| Channel | Owner | What it is |
|---|---|---|
| GitHub private vulnerability advisories on `GhostPortTechnologies/Ghostport-Phantom-OS` | maintainer (operator) | Preferred channel — researcher uses GitHub's "Report a vulnerability" UI; advisory is private to maintainers until published |
| `support@ghostporttechnologies.com` | maintainer (operator) | Email fallback for researchers without a GitHub account or for non-code findings (infra, policy, fleet) |
| Blog `/bug-bounty.html` form / `security@ghostporttechnologies.com` (public bounty page) | maintainer (operator) | Public bounty intake — see §6 inconsistency note |
| Bridge / Chamber alerts from Pi or EC2 Claude | AI agent finding | Internal sweeps that surface a vulnerability route through the bridge to the operator before any public disclosure |

If a report arrives via a less-canonical channel (Twitter DM, Discord,
in-person), the maintainer should ask the researcher to re-submit through one
of the four canonical channels above — gives the report a paper trail and
preserves the embargo clock.

## 2. Response timeline (the SECURITY.md promise)

| Milestone | Target | Owner | What "done" looks like |
|---|---|---|---|
| Acknowledgment | within 48 hours of report receipt | maintainer | Human reply confirming the report was received and is being looked at; sets the embargo clock |
| Initial triage / confirm-or-deny | within 7 days | maintainer (+ AI agents under direction) | Severity classified per blog `/bug-bounty.html` rubric (CRITICAL / HIGH / MEDIUM / LOW); reproduction confirmed or rejected with reason |
| Fix shipped | 7–30 days, severity-dependent | maintainer + assigned AI builder | Patch merged to main, OTA push staged, fleet rolled forward, advisory drafted |
| Coordinated disclosure | default 90 days from initial report (sooner if jointly agreed) | maintainer | Public advisory published with researcher credit and Hall of Fame entry on `/bug-bounty.html` |
| Bounty paid | on confirmed fix | maintainer | Amount negotiated per §3 of `/bug-bounty.html`; paid via PayPal/Venmo/crypto per researcher preference |

These targets are aspirational maxima, not floors. Most reports should be
acknowledged the same day. Critical findings (active exploitation, data
exposure, key extraction) trigger the §SEV-1 path in
`compliance/incident-response.md` — not this SOP — which is built for
non-active routine vulnerability intake.

## 3. Triage roles

| Role | Who | Responsibility |
|---|---|---|
| Incident Commander | operator | Final authority on classification, fix timeline, disclosure timing, bounty amount |
| Pi-side AI agent | whichever Claude window is on duty | Reproduction, code reading, patch drafting on Pi-resident code (server.js, gp-* scripts, nft profiles) |
| EC2-side AI agent | EC2 Claude | Reproduction + patch on fleet API, blog, billing, public web properties |
| Researcher | external | Provides reproduction steps, validates fix against their POC, agrees to embargo |

The Incident Commander row is the same person as the SEV-1 commander in
`compliance/incident-response.md` (currently Thomas Estrada). When AI agents
are off-shift or the operator is mid-flight, the GitHub advisory creates a
durable inbox — the embargo clock starts at the timestamp on the advisory,
not at the moment the operator next reads it.

## 4. Severity classification (matches blog rubric)

The public bounty page at `/bug-bounty.html` defines four severities. Use the
same rubric here so internal triage matches what the researcher was told:

| Severity | Examples (from the blog rubric — keep aligned) |
|---|---|
| CRITICAL | Remote code execution, tunnel compromise, key extraction, auth bypass granting admin access, customer-data exfiltration |
| HIGH | Privilege escalation, persistent XSS on authenticated pages, HMAC/signature bypass, WireGuard config manipulation, DNS leak under any privacy mode |
| MEDIUM | Reflected XSS, CSRF on state-changing actions, information disclosure (internal IPs, versions, stack traces), rate-limit bypass |
| LOW | Missing security headers, cookie flags, clickjacking on non-sensitive pages, verbose error messages, theoretical issues with no demonstrated impact |

When the public rubric updates, this SOP gets a sweep too — the two must not
drift.

## 5. Out-of-scope reports — what to send back

The blog `/bug-bounty.html` lists scanner output, social engineering, physical
access, theoretical issues, and known limitations as out of scope. When a
report falls in those buckets:

1. Acknowledge anyway (48h target still applies — researcher took the time).
2. Reply with the specific reason from the blog page so the researcher can
   pivot if they have an in-scope finding adjacent to it.
3. No bounty, no Hall of Fame entry, no public advisory.

Don't ghost — the worst outcome is a researcher who feels ignored writing a
public thread about it. Decline politely with the reason from the rubric.

## 6. Known intake-channel inconsistency (flagged 2026-05-03)

`SECURITY.md` lists `support@ghostporttechnologies.com` as the email channel.
The public bounty page at `/bug-bounty.html` lists
`security@ghostporttechnologies.com`. All other repo references (README,
NOTICES, guide.html, compliance/* docs, gp-features) use `support@`.

The domain MX is privateemail.com (Namecheap), which supports aliases — both
addresses can route to the same inbox if configured. Until that's confirmed
or normalized, treat `support@` as the canonical address (it's what's
documented in `compliance/incident-response.md` for the Incident Commander)
and direct researchers there if they ask for clarification.

**Operator action item:** decide which is canonical, confirm/configure the
alias, and update either SECURITY.md or the blog `/bug-bounty.html` page so
both surfaces match. (Out of T-0091's edit scope because the blog is
EC2-side.)

## 7. Hall of Fame

The public bounty page maintains a Hall of Fame section with researcher
credit (most recent: Michael F / NullFox / Vulpine Security, April 10–18,
2026 engagement, 13 findings). Every confirmed external finding earns a
spot — researcher's choice to be named or anonymous. ROADMAP.md still has a
separate "Security Hall of Fame page" line item unchecked; that is a stale
roadmap entry, not a missing capability — the public page already surfaces
the same content. (Sweepable in T-0098 blog backlog, not here.)

## 8. Related documents

- `SECURITY.md` (repo root) — external-facing policy and intake channels
- `compliance/incident-response.md` — operational SEV-1/2/3/4 runbooks for
  active incidents (compromise, leaks, infrastructure compromise); this SOP
  is for routine inbound vulnerability reports, not active-incident triage
- `/opt/ghostport/docs/SECRET-SAFETY-SOP.md` §5 — rotation procedure to
  follow when a vuln report involves an exposed credential
- `https://blog.ghostporttechnologies.com/bug-bounty.html` — public bounty
  terms, scope, severity rubric, Hall of Fame
- `compliance/communication-plan.md` — how to communicate disclosure to the
  fleet / customers when a vuln has user impact

## 9. Change log

- 2026-05-03 — initial draft (T-0091, opus-disclose). Closes the
  "documented response timeline + handler" gap noted in the ticket body.

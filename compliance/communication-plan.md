# GhostPort — Incident Communication & Escalation Plan
**Version:** 1.0 | **Date:** 2026-03-24

## Escalation Path

```
Detection (automated)
    ↓
Bridge Alert → Pi Claude / EC2 Claude
    ↓
SEV-3/4: Claude fixes autonomously, logs action
SEV-1/2: Claude alerts Thomas immediately
    ↓
Thomas Estrada (Incident Commander)
    ↓
If customer-impacting: support@ghostporttechnologies.com notice
If Stripe-related: Stripe support escalation
If AWS-related: AWS support case
```

## Notification Matrix

| Severity | Thomas | Pi Claude | EC2 Claude | Customers |
|---|---|---|---|---|
| SEV-1 Critical | Email + bridge IMMEDIATELY | Auto-contain if possible | Auto-contain if possible | If data breach: notify within 72h |
| SEV-2 High | Bridge alert, email if no response in 1h | Diagnose + fix if safe | Diagnose + fix if safe | Only if service disruption > 1h |
| SEV-3 Medium | Bridge message (non-urgent) | Fix autonomously | Fix autonomously | No notification |
| SEV-4 Low | Daily digest or next session | Log only | Log only | No notification |

## Contact Methods (by priority)

1. **Claude Bridge** — fastest, checked every session (both Claudes)
2. **Email** — support@ghostporttechnologies.com
3. **X/Twitter** — @ghostporttech (DM)
4. **UptimeRobot** — auto-emails on downtime

## External Escalation

| Situation | Contact | Method |
|---|---|---|
| Stripe fraud/unauthorized charges | Stripe Support | Dashboard → Help |
| AWS account compromise | AWS Support | Console → Support Center |
| Domain/DNS hijack | Registrar support | Registrar portal |
| SSL cert issues | Let's Encrypt | Community forum (free tier) |
| Law enforcement request | Thomas Estrada | Direct contact required |

## Customer Notification Template (if ever needed)

```
Subject: GhostPort Security Notice

We identified [brief description] on [date].

What happened: [1-2 sentences]
What we did: [containment + fix]
What you should do: [any customer action needed]
What we're doing to prevent recurrence: [improvements]

Questions? Contact support@ghostporttechnologies.com
```

## GDPR Notification (if EU customers in future)

- Data breach notification to supervisory authority: within 72 hours
- Data breach notification to affected users: "without undue delay" if high risk
- Document: what data, how many affected, likely consequences, measures taken

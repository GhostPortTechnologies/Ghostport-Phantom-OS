# Observability Patterns SOP

**Rule above all rules:** when adding a counter, metric, or anomaly detector, pick the data source that preserves privacy by default. Per-event logging is the fallback, not the first choice.

**Rule origin:** 2026-04-24 Palantir-countermeasures sprint. The Enemy List feature started as a Pi-hole query-log consumer, then discovered Pi-hole is locked at privacy level 3 (no per-query retention — by GhostPort's own design). Pivoted mid-feature to an nftables-counter architecture. The lessons below codify the pivot so future features don't retrace the same mistakes.

## 1. The three data-source patterns

### 1.1 nftables named counters (PREFERRED for aggregate metrics)

Kernel-maintained counters on a rule match. Zero userspace overhead per packet. No per-event data retained — only a running `packets` + `bytes` tally per named counter.

**Use when:**
- You want "how much traffic went to X" aggregated over time.
- You do NOT need per-event metadata (who sent it, when, what payload).
- The target set of IPs/MACs is bounded and knowable.

**Example shipped implementations:**
- `inet phantom_brokers` — per-broker counters for the Enemy List
- `inet phantom_scrub` — TCP TTL + MSS normalization counters (not currently read, but same pattern)
- `inet phantom_blocklist` — blocked-MAC drop chain (forward-hook)

**Skeleton:**
```nft
table <family> <name> {
  set target_<slug>_ips { type ipv4_addr; flags interval; elements = { ... }; }
  counter ctr_<slug> { packets 0 bytes 0 }
  chain count {
    type filter hook postrouting priority 300; policy accept;
    ip daddr @target_<slug>_ips counter name ctr_<slug>
  }
}
```

**Reading:** `nft -j list counters table <family> <name>` → parse JSON, each counter has `packets` + `bytes`.

**Persistence:** counters reset on table reload or reboot. That is usually acceptable for dashboard-resolution data. If you need persistence, snapshot periodically via a timer.

### 1.2 conntrack flow aggregates

Per-flow byte/packet counters inside the kernel's conntrack table. Requires `net.netfilter.nf_conntrack_acct=1` (already enabled on GhostPort via `/etc/sysctl.d/99-phantom-conntrack-acct.conf`).

**Use when:**
- You need per-client or per-flow breakdown.
- The set of clients/flows is dynamic (client IPs come and go).
- Nftables per-client rule enumeration would be too much state.

**Example shipped implementation:**
- `gp-device-anomaly` — per-LAN-IP outbound rate baseline + anomaly detection.

**Read pattern:**
```bash
sudo conntrack -L | grep 'src=<ip>' | awk '... sum bytes ...'
```

Or in Python — parse `src=<ip>` + `bytes=<N>` regex. Only original-direction (first) `src=` and `bytes=` matter.

**Gotchas:**
- Flows only have byte counts if they've been active since `conntrack_acct` was enabled. Older flows show no `bytes=`.
- A flow that closed isn't in the table anymore. Sample frequently (every 5 min) and accumulate deltas.
- Conntrack table has a size limit. On a busy network this matters; monitor `nf_conntrack_count` vs `nf_conntrack_max`.

### 1.3 DNS query-log aggregation (LAST RESORT)

Pi-hole or dnsmasq can log every DNS query. Aggregating by domain + status gives you "top domains by hit count."

**GhostPort-specific constraint:** Pi-hole runs at **privacy level 3** by default (`/etc/pihole/pihole.toml`). At level 3:
- `/api/stats/top_domains` returns empty
- `/api/queries` returns empty
- Only the global summary counters (total queries, total blocked) are populated

**Do NOT lower Pi-hole privacy level to make a feature work.** That regresses the product's privacy stance. Use pattern 1.1 or 1.2 instead.

**When DNS-log aggregation IS acceptable:**
- As a FALLBACK when pattern 1.1 isn't available (operator may have manually lowered privacy level).
- For read-only presence checks (e.g., "was domain X queried in this session?") that tolerate "unknown" as a valid answer.

**Never:**
- Assume `/api/queries` or `/api/stats/top_domains` has data.
- Require operators to lower privacy level in onboarding docs.
- Store per-query records with timestamps to disk.

## 2. Choosing the right pattern

Decision tree:

```
Do you need per-client / per-flow breakdown?
├── YES → conntrack aggregates (pattern 1.2)
│   Require nf_conntrack_acct=1, sample every 5 min, accumulate deltas.
│
└── NO → nftables named counters (pattern 1.1)
    Define a target set + one rule per category.
```

If neither works (edge case: you need per-query data without per-event retention), escalate to the operator. Do not quietly flip privacy levels or add per-query logging.

## 3. Privacy review checklist (run before shipping)

For any new counter / metric / anomaly detector, answer in the integration plan doc:

1. **What's stored on disk, with what retention?** (Per-event is red flag. Aggregates are fine.)
2. **What's in memory?** (In-process state that can be dumped is as exposed as disk.)
3. **Does it work at Pi-hole privacy level 3?** (If no, you're building on sand — pivot.)
4. **Can the operator turn it off?** (Must be yes, with a clear toggle.)
5. **Does the SOP §5 "Privacy stance" still hold?** (No per-query data, no per-client destination history, no plaintext logs of sensitive flows.)

If any answer is wrong, redesign before shipping.

## 4. Known gotchas from 2026-04-24 Palantir sprint

### 4.1 `getaddrinfo` doesn't honor `socket.setdefaulttimeout` reliably

Python's `socket.setdefaulttimeout(T)` ignores T for `getaddrinfo` on most Linux libc configurations. Set timeout → call getaddrinfo → hangs for 30+ seconds on unresolvable hosts.

**Fix:** use `subprocess.run(["dig", "+short", f"+time={T}", "+tries=1", ...])`. Pair with a `concurrent.futures.ThreadPoolExecutor` for parallel resolution.

Caught tonight: first implementation of `gp-broker-counters` used `getaddrinfo` and hung indefinitely on broker domains with no public A record. Fix dropped init time from >2 min to 1.3 s.

### 4.2 nftables ether_addr sets don't support `flags interval`

`type ether_addr; flags interval;` is a syntax error — MAC addresses are point values, not ranges.

**Fix:** `type ether_addr;` alone (no flags needed).

Caught tonight in `gp-mac-block` first build; fixed in same commit.

### 4.3 `flush table` fails if table doesn't exist

In an nft script fed via `nft -f -`, a leading `flush table <name>` fails atomically if the table isn't present yet — and rollback cancels everything after it.

**Fix:** `subprocess.run(["nft", "delete", "table", ...])` separately *before* the script runs. Swallow its error if the table was already gone.

### 4.4 `/etc/phantom/*.json` files need ghostport-admin ownership

Some config files (arsenal.json specifically) can end up root-owned after manual edits or from the factory image. The Node service runs as `ghostport-admin` and silently fails to persist toggle changes.

**Fix on encounter:** `sudo chown ghostport-admin:ghostport-admin /etc/phantom/<file>.json`.

**Long-term fix (todo):** boot-time ownership sweep that fixes ownership on every known `/etc/phantom/*.json` file before the service starts.

### 4.5 `git filter-repo` strips remote by default

After running `git filter-repo`, `git remote` returns empty. Re-add with `git remote add origin <url>`. **Do not** include a token in the URL — rely on `credential.helper=store`.

Documented in SECRET-SAFETY-SOP §11.2.12 but worth repeating here because rewriting history is a valid response to a privacy-pattern mistake (accidental per-query data written into a committed sample file, etc.).

## 5. Reusable scaffolds (shipped 2026-04-24)

| Need | Scaffold |
|---|---|
| "Which broker got hit, how many times" | `scripts/gp-broker-counters` + `inet phantom_brokers` table |
| "Per-device anomaly on outbound bytes" | `scripts/gp-device-anomaly` (conntrack pattern) |
| "Toggle a nftables rule via Arsenal UI" | Copy the QUIC block pattern (ghostport-server.js:3207) |
| "Sticky mode-level checkbox" | Clone the Dreadnought row (public/index.html:1535) |
| "Append a Crow's Nest alert" | `/etc/phantom/ids-events.json` JSON array append, `type` field distinguishes class |
| "Block a MAC persistently" | `sudo gp-mac-block add <mac>` + phantom-mac-blocklist.service for boot restore |

## 6. Related SOPs

- `FEATURE-INTEGRATION-SOP.md` — where to put the feature once you've picked the data pattern
- `SECRET-SAFETY-SOP.md` §11.2.11 — "detection rules catch by shape, not literal value" (related observability principle)
- `INVENTORY-BEFORE-BUILD-SOP.md` — grep first, build second
- `PYTHON-QA-SOP.md` — the gauntlet to run on every Python helper before shipping

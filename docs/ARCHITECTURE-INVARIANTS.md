# Phantom OS — Architecture Invariants

**Last verified:** 2026-05-08

Load-bearing rules. Violating any of these has caused (or would cause) a multi-hour incident. New sessions read this on kickoff before touching firewall, tunnel, AP, or boot paths.

Format per invariant: **Rule.** *Why it's there.* **Consequence if violated.**

---

## Tunnel & routing

1. **wg1 endpoint must have a host-route pinned to eth0 via the WAN gateway whenever wg1 is up.**
   *Default route in tunnel modes is `dev wg1`; without a pin, the kernel routes the encrypted handshake init back into wg1 itself, recursively.*
   **No handshake leaves the Pi → tunnel never establishes.** (T-0173)

2. **Both wg0 (control plane) and wg1 (data plane) MTU is 1380.**
   *Encapsulation overhead pushes MTU below 1500; mismatched MTU surfaces as kernel UAF + frozen TX ~5 minutes after handshake.*
   **Slow corruption that looks like a fleet outage.** (T-0170)

3. **`gp-region` must call `pin_endpoint_route()` BEFORE `wg setconf` AND on every rollback path.**
   *`wg setconf` on an unpinned endpoint sends the very first handshake out the wrong interface; rollback without pin leaves traffic stranded.*
   **Region switch silently fails dual-check timeout.** (T-0173)

4. **No PersistentKeepalive on the EC2-side wg1 peers.**
   *Server-side keepalive recreates stale peer→IP bindings the Pi can't update through NAT rebind, locking us to dead ports for hours.*
   **East/west asymmetric reply IPs, dropped handshakes, customer-visible outage.** (T-0169)

5. **`/etc/ghostport/active-region.json` `id` field is the source of truth for current region.**
   *The wg1 `Endpoint=` line is downstream — written from the id at boot. Reading the conf file lies if the state file disagrees.*
   **Operator commits to a region the system isn't actually using.** (T-0169 root cause)

6. **`gp-mode-boot` delegates MTU enforcement to `gp-mode`.** Single source of truth.
   *Two scripts setting MTU drift; one wins and the other's value persists in journal but not in kernel.*
   **Latent MTU drift from forgotten boot path.**

---

## Firewall (nftables)

7. **Killswitch chain hooks `forward priority -10`; main forward chain is `priority 0`.**
   *Killswitch must run BEFORE the accept-policy of the main chain so non-wg1 forward traffic is dropped even if forward is mis-configured.*
   **LAN→eth0 leaks during a partial mode failure.** (Verified live in DoubleHop, `project_killswitch_verified.md`)

8. **`common.nft` MUST always allow: tcp/4200 (dashboard), udp/41641 (Tailscale), iif tailscale0, ssh.**
   *Any of these closing locks the operator out of the device with no recovery path short of physical access.*
   **Lockout, customer image bricks.** (CLAUDE.md core safety)

9. **Every `.nft` profile gets `nft -c -f` validated before applying; rollback timer is 60s for non-ISP modes.**
   *A bad ruleset takes the dashboard with it; ISP is the safe fallback (no rollback, accept-by-default forward).*
   **Operator confirms or 60s later we're back in ISP — no manual recovery needed.**

10. **`conntrack -F` after every mode switch.**
    *Old flows route through the old data path; without flush, AP clients keep their ISP-direct conntrack until idle timeout.*
    **Mode switch appears not to take effect for established connections.**

11. **`/etc/ghostport/custom-rules.nft` is sourced by `gp-mode`'s `apply_custom_rules` AFTER every mode switch.**
    *This is how the device-allowlist (passthrough) survives mode swaps. Editing this file by hand is wrong — regenerate via `gp-allow`.*
    **Allowlist evaporates on mode switch; PS5 / TV drops offline.** (T-0177)

---

## AP / Radio

12. **`hostapd.interface = wlan0` resolves to phy#1 (mt7921e PCIe), not the built-in Pi WiFi.**
    *wlan_built (brcmfmac, phy#0) is unused for AP — it's there as a fallback radio. Confusing the two breaks `iw set txpower` and country-code handling.*
    **Operator runs `iw dev wlan0 set txpower` against the wrong assumption and sees no change.**

13. **MT7921 firmware-managed SKU table caps txpower; `iw set txpower` calls may not stick.**
    *The driver enforces a regulatory table built into firmware; CRDA `country_code=US` interacts but doesn't override.*
    **Power tuning attempts look successful in logs, no observable change in the air.**

14. **DHCPOFFER must use broadcast (not unicast) for clients that set the broadcast flag.**
    *Game consoles (PS5 verified) discard unicast OFFERs sent before the lease is bound. Pi-hole v6 default was unicast; T-0176 flipped it.*
    **Game console gets no IP — cannot self-diagnose.** (T-0176)

15. **Pi emits its own randomized-MAC probe requests.** Local-administered MACs at close range in Sonar capture are us, not strangers.
    *Modern Linux randomizes probe-source MAC by default; Sonar's heuristics had to learn this.*
    **False alarms in Sonar; operator chases a ghost.** (`reference_pi_self_probes.md`)

---

## Services & state

16. **Tailscale is never stopped.** It's the always-on dev-image management plane.
    *Customer images strip it (per `project_no_remote_to_customer.md`); dev image keeps it for remote rescue.*
    **Lose remote access to the device.** (CLAUDE.md core safety)

17. **Live system files are NOT auto-synced to `/opt/ghostport/`.** Operator copies + commits manually.
    *Past auto-sync attempts pushed half-tested changes to the public repo; operator now reviews every diff before push.*
    **Half-baked code lands on `main`.** (`feedback_no_repo_sync`)

18. **`ghostport.service` and the labwc compositor are not restarted without explicit operator approval.**
    *Restart drops the operator's session, breaks any in-flight UI work, and can mask the bug we're trying to debug.*
    **Lost desktop session, debug context destroyed.** (`feedback_no_reboot`)

19. **`/opt/ghostport/.dev-image` marker controls the right-click "Development" submenu visibility.**
    *Customer images strip this so the menu doesn't surface dev-only tools to end users.*
    **Customer sees Claude bridge / fleet auth toggles in their menu.**

20. **Plain `gp-qa` is the default.** Only the designated security sweeper runs `--security` / `--paranoid`.
    *Concurrent paranoid runs from multiple AIs hammer the system; one assigned sweeper avoids the dogpile.*
    **System grinds while five Claudes scan in parallel.** (`feedback_qa_concurrency`)

---

## Boot order & state files

21. **`gp-mode-boot` reapplies the saved mode from `/etc/ghostport/current-mode` at startup.**
    *Without this, the Pi comes up in a default state that contradicts the operator's last setting.*
    **Mode reverts after every reboot.** (CLAUDE.md services list)

22. **Pi-hole v6 + dnsmasq DHCP lease file is `/etc/pihole/dhcp.leases`.**
    *Use the `gp-leases` helper, not a hardcoded path. Pi-hole v6 changed the path from earlier versions.*
    **Stonefish/Crew Manifest read empty lease file, show no clients.** (Memory: filesystem layout)

23. **`/etc/ghostport/known-wg-peers.json` is the world-readable WG peer cache used by Crow's Nest classify_drop.**
    *Adding a region = append a line, no code change. The classifier uses this to distinguish fleet drops from genuinely-suspect drops.*
    **New region added = false-positive RATE_ANOMALY alarms.**

---

## Refresh policy

This doc gets a `Last verified` bump every time an invariant is added, removed, or re-validated. If you read this six months from now and any rule looks stale, validate it before relying on it. Stale invariants are worse than no doc — they teach the wrong thing.

Add a new invariant when (and only when) an incident shows that violating an unwritten assumption caused real damage. The bar is high on purpose. Anything you can rediscover from `nft list ruleset` or `cat /etc/gpmodes/*` doesn't belong here — it belongs in the code.

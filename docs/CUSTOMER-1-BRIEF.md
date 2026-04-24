# GhostPort — Customer #1 Brief

*Working document. Edit freely — goal is that we both agree on expectations before the device ships.*

**Last updated:** 2026-04-24
**Status:** draft for customer review

---

## Who you are
- Technical comfort: engineer + civilian hybrid. Can read logs, prefers not to.
- What you'll use this for: streaming (Netflix/YouTube/etc), kids' devices with parental controls, work-from-home including VoIP/video calls.

## What you should expect GhostPort to do
- Block ads + trackers on every device in the house.
- Give you mode-level privacy (ISP / Zero Trust / DoubleHop / ZHop) with clear tradeoffs.
- Block the smart TV + data broker categories if you opt in.
- Show you what it's doing (Enemy List, Family Shield stats, Privacy Score).
- Update itself over the air when we ship fixes.

## What you should NOT expect (yet)
- Ghost Mode exit-IP rotation — infrastructure pending, toggle shows "needs ≥2 endpoints."
- Rate-anomaly alerts in the first week — baseline learning period.
- Zero smart TV weirdness if you enable the ACR blocker — some TVs lose content recommendations.

## What you'll test for (your job)
- DNS / WebRTC / IP leaks on every mode (report results per mode).
- UX friction: anything that made you say "what?" (even if it worked).
- Features that look like they should exist but don't.
- Anything that broke or needed a restart.

## Support contract (my job)
- **Response within 24 hours** on any reported issue.
- **OTA fix deployment within 72 hours** for anything that affects privacy or uptime.
- Discord channel + email for async; Tailscale for live debug if we both agree.
- Full changelog of everything we push to your device.

## Week 1 success = all of these
- Device online and serving WiFi within 30 min of unboxing.
- At least 3 days of use with no manual recovery needed.
- No DNS / WebRTC / IP leak detected in any mode.
- You write 5+ notes on UX rough edges (the FIELD-NOTES file).

## Week 1 failure (we pull back and iterate) = any of these
- Privacy leak detected in a named mode (catastrophic — this is the product).
- Device lockout requiring console recovery.
- OTA pipeline can't reach the device.
- You tell me "I wouldn't give this to a friend yet."

## What we credit you for
- Named testimonial on the website if you want it (opt-in).
- Hall of Fame entry for each bug you find, with severity rating.
- **Free lifetime subscription** for as long as GhostPort operates fleet services. This is our thank-you for being the first real human on the line with us.

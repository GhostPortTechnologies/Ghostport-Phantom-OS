# 632 Uninvited Packets in 10 Hours — What Your Router Isn't Telling You

*April 15, 2026*

Yesterday afternoon, a GhostPort user checked their firewall logs for the first time. Nothing unusual had happened. No strange behavior, no slow internet, no alerts on any device. Just a quiet day at home.

The logs told a different story.

In 10 hours — a single, unremarkable afternoon — GhostPort's nftables firewall had silently dropped **632 unsolicited packets**. Roughly one per minute. Every single one was an uninvited connection attempt reaching into the network from the WAN side.

The user's reaction: *"I had so many of those threats?"*

They had no idea.

---

## What Was Knocking

Three categories stood out in the logs.

**An unknown device at 192.168.0.174, broadcasting on port 9999 — 12 times.**

Port 9999 is used by a grab bag of services. Some legitimate. Some not. It is a known vector for malware command-and-control traffic. This device — sitting on the ISP's local network, not belonging to the user — was actively probing. Twelve times in ten hours, it sent unsolicited packets toward the user's network segment. On a normal router, every device in the house would have received them.

**The same device, broadcasting LIFX smart bulb discovery on UDP port 56700 — 6 times.**

LIFX is a smart lighting protocol. Its discovery mechanism is completely unauthenticated. Any device on the network can send a broadcast and every LIFX bulb will respond with its identity, status, and control interface. No password. No handshake. If you have smart bulbs, anyone on your subnet can find them and control them. This unknown device was actively looking.

**The ISP router itself (192.168.0.1), sending IGMP multicast queries to 224.0.0.1 — twice.**

IGMP queries are how routers manage multicast group memberships. They are also a textbook reconnaissance technique. A device responding to these queries reveals its presence, IP address, and the multicast groups it belongs to. Your ISP's router was, by design, asking every device to announce itself.

The remaining **612 packets** were more of the same. Broadcast probes, multicast chatter, and unsolicited connection attempts — the constant background noise of a shared network that most people never see.

---

## Why This Matters

Most home networks are flat. Your laptop, your phone, your kid's tablet, your smart TV, your neighbor's compromised IoT camera — if they are on the same ISP subnet, they can all talk to each other. There is no boundary. There is no inspection. Traffic flows freely in every direction.

Your consumer router is not a firewall. It does NAT. It hands out IP addresses. It does not examine unsolicited inbound traffic from the local network and decide whether your devices should see it. Everything on the WAN side has a direct path to everything on your side.

This is what "flat network" means in practice: 632 packets from devices you do not own, running protocols you did not authorize, reaching toward hardware you paid for. On a quiet Tuesday.

Now think about an apartment building with 40 units on the same ISP subnet. A coffee shop. A hotel. The number is not 632. It is thousands.

---

## What GhostPort Does Differently

GhostPort creates a hard network boundary. Your devices live on a private subnet (192.168.50.x) behind the wireless access point. The ISP's network sits on the other side (eth0/WAN). Between them: nftables with a default-deny policy.

The rule is simple. If your device requested it, the response comes through. If nobody on your network asked for it, it gets dropped and logged. No exceptions.

That unknown device on 192.168.0.174 broadcasting on port 9999? Dropped. The LIFX discovery probes trying to find your smart bulbs? Dropped. The IGMP reconnaissance from the ISP router? Dropped. All 632 of them. Silently, automatically, without the user needing to know or care.

This is not deep packet inspection. It is not AI-powered threat detection. It is a fundamental architectural decision: trust no network. Your devices talk to the internet through an encrypted tunnel or a filtered gateway. Nothing else gets in.

---

## The Real Problem

The discovery here is not that 632 packets were blocked. The discovery is that on every other router, those 632 packets were **delivered**. To every device. Every day. And nobody told you.

Your router is not lying to you. It just has nothing to say, because it was never watching in the first place.

GhostPort watches.

---

*Phantom OS is a privacy router built on Raspberry Pi 5. Learn more at [ghostporttechnologies.com](https://ghostporttechnologies.com).*

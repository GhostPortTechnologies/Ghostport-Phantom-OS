#!/usr/bin/env python3
"""GhostPort Discord Support Bot — monitors trouble-tickets forum and posts AI triage responses."""

import asyncio
import logging
import os
from logging.handlers import RotatingFileHandler

import anthropic
import discord

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log = logging.getLogger("ghostport-discord")
log.setLevel(logging.INFO)

_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

_stdout = logging.StreamHandler()
_stdout.setFormatter(_fmt)
log.addHandler(_stdout)

_file = RotatingFileHandler(
    os.path.expanduser("~/ghostport-discord.log"),
    maxBytes=5 * 1024 * 1024,
    backupCount=3,
)
_file.setFormatter(_fmt)
log.addHandler(_file)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
FORUM_CHANNEL_NAME = os.environ.get("FORUM_CHANNEL_NAME", "trouble-tickets")

SYSTEM_PROMPT = """\
You are GhostPort Support, an AI assistant for the GhostPort privacy router.

## Architecture
- Raspberry Pi 5 running Raspberry Pi OS (arm64)
- WiFi AP: hostapd on wlan0 (5 GHz 802.11ax, SSID "GhostPortRouter")
- DHCP/DNS: dnsmasq (192.168.50.x) + Pi-hole for DNS blocking
- Firewall: nftables with mode profiles in /etc/gpmodes/
- Tunnels: WireGuard (wg0) and Tailscale (tailscale0, always-on management)
- Web UI: Node.js Express on port 4200 (HTTPS)

## Modes
- **ISP**: LAN traffic passes through eth0 directly. No VPN, no filtering.
- **ZeroTrust**: LAN → eth0, DNS locked down (DoT/DoH blocked), Tailscale for management.
- **DoubleHop**: LAN → wg0 (WireGuard VPN only), DNS → Pi-hole, Tailscale for management.
- **ZHop**: LAN → wg0, DNS locked (Pi-hole + MagicDNS only), Tailscale for management.

## Common issues & troubleshooting
- If WireGuard shows "inactive" in DoubleHop/ZHop → check /etc/wireguard/wg0.conf, run `sudo wg-quick up wg0`
- If DNS fails in ZeroTrust/ZHop → verify Pi-hole is running (`pihole status`), check nftables DNS prerouting rules
- If no internet after mode switch → mode switch may have failed; check `/tmp/gp-current-mode`, try ISP mode as fallback
- Mode switches have a 60-second rollback timer; user must confirm or it auto-reverts
- Tailscale is always on for management; if Tailscale is down, the device may be unreachable remotely
- Public IP showing VPN provider IP = WireGuard tunnel is active; showing ISP IP = direct connection

## Your role
1. Analyse the ticket description and embedded diagnostics (Mode, WireGuard, Tailscale, Public IP, Uptime).
2. Identify the most likely root cause.
3. Suggest concrete next steps the user can take (CLI commands, UI actions, config checks).
4. Be concise and friendly. Use bullet points. If unsure, say so and recommend the user wait for a human.
"""

# ---------------------------------------------------------------------------
# Anthropic client (sync SDK, called via asyncio.to_thread)
# ---------------------------------------------------------------------------
anth = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def call_claude(user_message: str) -> str:
    """Synchronous Anthropic API call — run in a thread."""
    resp = anth.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    return resp.content[0].text


# ---------------------------------------------------------------------------
# Discord bot
# ---------------------------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

bot = discord.Client(intents=intents)

_seen_threads: set[int] = set()
_forum_channel_id: int | None = None


@bot.event
async def on_ready():
    global _forum_channel_id
    log.info("Bot connected as %s", bot.user)

    # Find the forum channel
    for guild in bot.guilds:
        for channel in guild.channels:
            if (
                isinstance(channel, discord.ForumChannel)
                and channel.name == FORUM_CHANNEL_NAME
            ):
                _forum_channel_id = channel.id
                log.info(
                    "Watching forum channel: #%s (ID %s) in %s",
                    channel.name,
                    channel.id,
                    guild.name,
                )
                return

    log.warning("Forum channel '%s' not found in any guild!", FORUM_CHANNEL_NAME)


@bot.event
async def on_thread_create(thread: discord.Thread):
    if _forum_channel_id is None:
        return
    if thread.parent_id != _forum_channel_id:
        return
    if thread.id in _seen_threads:
        return

    _seen_threads.add(thread.id)
    log.info("New ticket thread: %s (ID %s)", thread.name, thread.id)

    # Brief delay — the starter message may not be available immediately
    await asyncio.sleep(2)

    try:
        starter = thread.starter_message
        if starter is None:
            starter = await thread.fetch_message(thread.id)
    except Exception:
        log.exception("Could not fetch starter message for thread %s", thread.id)
        return

    # Build the prompt from message content + embeds
    parts: list[str] = []

    if starter.content:
        parts.append(f"**Message:**\n{starter.content}")

    for embed in starter.embeds:
        if embed.title:
            parts.append(f"**Title:** {embed.title}")
        if embed.description:
            parts.append(f"**Description:** {embed.description}")
        for field in embed.fields:
            parts.append(f"**{field.name}:** {field.value}")

    if not parts:
        log.warning("Thread %s has no parseable content, skipping.", thread.id)
        return

    ticket_text = "\n".join(parts)
    log.info("Sending ticket to Claude (%d chars)", len(ticket_text))

    try:
        response = await asyncio.to_thread(call_claude, ticket_text)
    except Exception:
        log.exception("Anthropic API call failed for thread %s", thread.id)
        await thread.send(
            "Automated analysis unavailable \u2014 a human will review shortly."
        )
        return

    # Post response, chunking at 2000-char Discord limit on newline boundaries
    await _send_chunked(thread, response)
    log.info("Posted AI response to thread %s", thread.id)


async def _send_chunked(channel, text: str):
    """Send text in <=2000-char chunks, splitting on newline boundaries."""
    while text:
        if len(text) <= 2000:
            await channel.send(text)
            break

        # Find the last newline within the 2000-char window
        cut = text.rfind("\n", 0, 2000)
        if cut <= 0:
            cut = 2000  # no newline found, hard cut

        await channel.send(text[:cut])
        text = text[cut:].lstrip("\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    log.info("Starting GhostPort Discord Support Bot...")
    bot.run(DISCORD_TOKEN, log_handler=None)

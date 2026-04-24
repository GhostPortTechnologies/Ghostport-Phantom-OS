# gp-tor — Tor Network Launcher

Route traffic through the Tor network for anonymous browsing.

## Usage
```
gp-tor              Interactive dashboard
gp-tor start        Start Tor SOCKS proxy (127.0.0.1:9050)
gp-tor stop         Stop Tor
gp-tor status       Connection status and circuit info
gp-tor newid        Request new identity (fresh circuit)
gp-tor browser      Launch Chromium through Tor
```

## How It Works
Traffic bounces through 3 encrypted relays (entry → middle → exit). Each relay only knows the previous and next hop. Works alongside all GhostPort modes — in DoubleHop/ZHop, traffic goes VPN → Tor.

## nftables Interaction
Tor uses SOCKS proxy (port 9050), not transparent routing. No firewall changes needed.

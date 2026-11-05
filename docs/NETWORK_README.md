# Network and SDN implementation guide

## Topology

`network/topology.py` creates two hosts and three OpenFlow 1.3 switches:

- h1 (10.0.0.1) connects to s1 port 1.
- Primary path: s1 port 2 to s3 port 2.
- Backup path: s1 port 3 to s2 port 1, then s2 port 2 to s3 port 3.
- h2 (10.0.0.2) connects to s3 port 1.

The h2-facing link can be configured with packet loss for robustness experiments.

## Controller

`network/controller.py` installs forwarding rules and polls the UDP measurement flow on s1. It writes current switch counters and controller events to the JSON telemetry file selected by `SDN_LIVE_FILE`.

The measurement flow is identified by ingress port 1, IPv4, UDP and destination 10.0.0.2. The controller modifies its output action to select the primary or backup path. `OFPFC_MODIFY_STRICT` is used for path changes so the matching flow's counters are preserved.

### Dynamic behaviour

- Default high-rate threshold: 1500 packets/s (`SDN_HIGH_RATE_PPS`).
- Default low-rate threshold: 1000 packets/s (`SDN_LOW_RATE_PPS`).
- Above the high threshold, dynamic mode installs output port 3 (backup path).
- Below the low threshold, dynamic mode restores output port 2 if the primary link is up.
- A primary-link-down event also redirects to port 3. Recovery is recorded and the primary path is restored when safe.
- Static mode is the baseline and does not change the path in response to these conditions.

This is path redirection, not rate limiting or packet shaping. The sender continues transmitting at its configured rate.

## Run

Recommended integrated path:

```bash
sudo mn -c
sudo python3 web/server.py
```

Open http://127.0.0.1:8000. The web server starts the Ryu controller for each experiment and launches Mininet with the selected static/dynamic mode.

Command-line alternative:

```bash
sudo mn -c
sudo python3 integration/run_demo.py --mode dynamic --rate 500 --duration 10
```

The integration script starts its own controller. Do not start a second controller on the same OpenFlow port while running it.

## Interpreting counters

OpenFlow packet/byte counts are cumulative. Compare the final switch counter with its initial value for the same run. Switch bytes include protocol headers, while application bytes count UDP payload only. The sender emits five end markers, which the switch counts but the receiver excludes from data packet totals. Explain these accounting differences in the report.

## Validation checklist

- `h1 ping -c 3 h2` succeeds before traffic testing.
- The controller connects to all three switches.
- Normal UDP traffic appears in receiver results and s1 flow statistics.
- Random packet sizes are reproducible with the same seed.
- Link failure creates a `link_down` and (dynamic mode) `reroute` event.
- Link recovery creates a `link_up` and, when safe, a `recover` event.
- High-rate traffic creates `high_rate_detected` and `high_rate_redirect` events in dynamic mode.
- Traffic below the low threshold creates `high_rate_cleared` and `high_rate_recovery` events when the primary link is available.
- Static and dynamic runs are repeated under the same traffic settings before comparing results.

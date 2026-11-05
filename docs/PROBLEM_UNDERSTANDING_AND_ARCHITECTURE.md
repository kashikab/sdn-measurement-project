# Problem understanding and system architecture

## 1. Problem statement

Develop a UDP-based traffic measurement application and compare application-level bandwidth measurements with SDN flow statistics. The application sends numbered UDP datagrams; the receiver measures what arrived; an OpenFlow switch counts the matching flow; and the controller collects those counters.

## 2. Objectives

1. Generate UDP measurement traffic.
2. Count transmitted and received packets.
3. Calculate throughput and packet loss.
4. Collect OpenFlow flow statistics.
5. Compare application and switch measurements.
6. Demonstrate dynamic SDN behaviour using primary/backup paths and link-failure and high-rate events.

## 3. Architecture

| Component | Responsibility |
|---|---|
| UDP sender on h1 | Fixed or seeded-random datagram sizes, sequence numbers, timestamps and configurable pacing. |
| UDP receiver on h2 | Unique packet counting, sequence-gap loss detection, byte counting and throughput. |
| s1, s2, s3 | OpenFlow 1.3 forwarding with primary and backup paths. |
| Ryu controller | Flow installation, flow-stat polling, high-rate detection, path redirection and link recovery. |
| Web dashboard | Experiment setup, live telemetry, controller event display, results and static/dynamic comparison. |

### Topology

```text
h1 -- s1 ---- s3 -- h2    Primary path
      |       ^
      s2 -----+             Backup path via s2

Ryu controller <---- OpenFlow 1.3 ----> s1, s2, s3
```

Host addresses are h1 `10.0.0.1` and h2 `10.0.0.2`. The controller connects on TCP port 6633 in the supplied dashboard and integration runner.

## 4. Packet format and metrics

Each data packet starts with a 12-byte network-order header: a 4-byte unsigned sequence number and an 8-byte timestamp. The sender sends five end markers containing the expected number of data packets. The receiver uses sequence numbers and that expected count to detect missing packets and ignores duplicate sequence numbers for its unique packet total.

- Application packet count: data packets sent/uniquely received.
- Application bytes: UDP payload bytes.
- Application throughput: received payload bits divided by the receive interval.
- Switch packet/byte counters: OpenFlow counters for the measurement flow, including the five end markers.
- Switch bytes include Ethernet, IPv4 and UDP headers; for this topology, account for approximately 42 additional bytes per packet compared with application payload bytes.

## 5. Dynamic SDN behaviour

Dynamic mode uses two packet-rate thresholds with hysteresis. Above 1500 packets/s (configurable with `SDN_HIGH_RATE_PPS`), the controller redirects the measured flow from s1 port 2 (primary) to s1 port 3 (backup). Once the rate falls below 1000 packets/s (`SDN_LOW_RATE_PPS`), it restores the primary path if the primary link is available. A primary-link failure also triggers backup-path failover; recovery is recorded and the primary path can be restored when safe.

Static mode is the baseline and does not perform dynamic path changes. The high-rate response is path redirection, not rate limiting or packet shaping.

## 6. End-to-end flow

1. The dashboard starts a Ryu controller with the selected mode.
2. Mininet creates the topology and connects its switches to the controller.
3. The receiver starts on h2.
4. The sender on h1 transmits configured UDP traffic.
5. s1 forwards the flow and increments OpenFlow counters.
6. The controller polls counters and updates live telemetry/events.
7. The dashboard collects sender, receiver and switch measurements and writes the experiment row to CSV.
8. Static and dynamic runs can be compared using identical traffic settings.

## 7. Evaluation plan

Demonstrate normal traffic, seeded random-size traffic, configured packet loss, primary-link failure under static and dynamic modes, high-rate-triggered redirection, recovery, and application-versus-switch counter reconciliation. Report measured results from the actual Mininet environment; do not substitute dashboard demo-mode values for real measurements.

# UDP application and test plan

## UDP sender

`app/sender.py` sends sequence-numbered, timestamped UDP datagrams. It supports fixed packet sizes and random packet sizes in a configured range. A seed makes the random size sequence repeatable. The sender records data packet count, payload bytes, expected switch packet count and expected switch bytes.

Example:

```bash
python3 app/sender.py --ip 10.0.0.2 --port 9999 --rate 500 --duration 10 --pattern random --size-min 200 --size-max 1472 --seed 42
```

## UDP receiver

`app/receiver.py` counts unique sequence numbers, received payload bytes, malformed datagrams and missing packets based on the end marker's expected count. It reports throughput and loss in JSON.

**Concurrency.** The main thread only receives datagrams and timestamps them. Each distinct sender (source IP and source port) gets its own worker thread and queue, which does the counting, loss detection and end-marker handling for that sender. Several senders can be measured at the same time without mixing their sequence numbers. The top-level JSON fields are totals over all senders (a single-sender run looks as before); per-sender figures are under `senders`.

The receiver does not stop on silence. A failed link can stay down for several seconds, so it waits for every sender's end marker (plus a 0.5 s drain) or for the deadline. Silence in the middle of a run is counted as outage, and when `--measurement-start-file` is given, silence up to the end of the traffic window is counted too.

Example:

```bash
python3 app/receiver.py --port 9999 --duration 15 --out results/last_receiver.json
```

Start the receiver before the sender. UDP does not guarantee delivery, ordering or retransmission; this project measures loss rather than hiding it with retransmissions.

## Test matrix

| ID | Test | Procedure | Evidence / expected observation |
|---|---|---|---|
| T1 | Normal fixed-size transfer | Run fixed-size traffic at a moderate rate | Sender and receiver counts should agree when no loss is induced. |
| T2 | Random packet sizes | Run with a fixed seed twice | Same seed produces the same packet-size sequence; payload byte totals should match. |
| T3 | Packet loss | Set non-zero loss on the h2-facing link | Receiver reports missing sequence numbers and non-zero loss when packets are dropped. |
| T4 | Multiple experiment configurations | Change rate, duration and packet size | Throughput and byte totals respond to the configuration. |
| T5 | Static link failure baseline | Select static mode and schedule primary-link failure | Primary path fails; no dynamic redirect is expected. |
| T6 | Dynamic link failover | Repeat T5 in dynamic mode | Controller records link failure and switches the measurement flow to the backup path. |
| T7 | High-rate path response | Send traffic above the configured high-rate threshold in dynamic mode | Controller records a high-rate event and changes the flow output to the backup path; after rate falls below low threshold, it can restore primary. |
| T8 | App/network comparison | Compare sender/receiver results with s1 flow counters | Explain five end markers and per-packet header overhead when reconciling counts/bytes. |
| T9 | Two senders at once | Start two sender processes against one receiver | `sender_count` is 2 and each sender has its own received, expected and loss figures. |
| T10 | Long outage inside a run | Silence longer than 3 s in the middle of a run | The receiver keeps running; `outage_s` reflects the silence and no packets are lost from the count. |

## Metrics and interpretation

- Application packet loss: `(expected data packets - unique received data packets) / expected data packets * 100`.
- Application throughput: received UDP payload bits divided by the measured receive interval.
- Switch throughput/rate: derived from OpenFlow flow-counter deltas over the polling interval.
- Switch packet count includes end markers; application data count excludes them.
- Switch byte count includes Ethernet, IPv4 and UDP headers; application payload byte count does not.

Do not claim that the controller improved throughput or reduced loss until repeated static and dynamic Mininet runs produce measured evidence. Record test settings, actual results and any variability in the final report.

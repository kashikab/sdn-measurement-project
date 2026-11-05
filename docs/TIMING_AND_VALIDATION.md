# Dashboard timing and measurement validation

- The dashboard shows **Preparing network** while Ryu and Mininet initialize. The traffic timer begins immediately before the UDP sender is launched, not when Run is clicked.
- `/api/status` exposes `phase`, `start_ts`, `elapsed_s`, and `error`. Controller event timestamps are converted once to seconds relative to the traffic start (`rt`).
- The receiver no longer exits early on a 2.5-second idle gap. When invoked by the dashboard, it reads the shared measurement window and counts an unresolved trailing receive gap as outage. Gaps over 250 ms are counted; this is a heuristic, not a link-layer failover latency measurement.
- Receiver throughput is computed over the configured measurement duration (not just the time between first and last received packet).
- The dashboard calculates final packet loss using the sender's actual count, so losing the UDP EOF markers cannot hide trailing packet loss. `last_receiver.json` may still contain an estimated expected count when EOF is lost; use the dashboard CSV for final sender/receiver comparisons.
- The yellow **Simulated data** label indicates browser-only demonstration mode when the API cannot be reached. Never report simulated rows as Mininet measurements.
- A completed Python syntax check is not a substitute for testing on Ubuntu 22.04 with Mininet, OVS and Ryu. Test static/dynamic link failures using identical random seeds.

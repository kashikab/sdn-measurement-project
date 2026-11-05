# Bandwidth Meter and Traffic Analyzer

**SDN-Socket Programming Mini Project | Topic 26 | UDP + OpenFlow 1.3**

## Project objective

Generate UDP measurement traffic in Mininet, measure it at the sender/receiver and OpenFlow switch, and compare application-level measurements with network-level flow counters. The project also demonstrates dynamic SDN path redirection and recovery.

## Features

- UDP sender with fixed or reproducible random packet sizes, configurable rate, duration and seed.
- Sequence-numbered and timestamped datagrams; receiver detects missing and duplicate sequence numbers.
- Application-side packet/byte counts, packet loss and throughput.
- Three-switch Mininet topology with a primary path (`s1 -> s3`) and backup path (`s1 -> s2 -> s3`).
- Ryu OpenFlow 1.3 controller and flow-stat polling.
- Static and dynamic controller modes.
- Dynamic high-rate response: above 1,500 pps the controller redirects the measured flow to the backup path; below 1,000 pps it can restore the primary path.
- Link-failure detection, failover and recovery.
- Browser dashboard for experiment configuration, live telemetry, controller events, results and static/dynamic comparison.
- CSV experiment history and JSON run artifacts.

## Architecture

```text
                   Ryu SDN Controller
                 (OpenFlow 1.3, port 6633)
                          |
                    flow rules/stats
                          |
 h1 UDP sender -- s1 -----+----- s3 -- h2 UDP receiver
                 |              ^
                 |              |
                 +---- s2 ------+
                  backup path
```

| Component | Responsibility |
|---|---|
| `app/sender.py` | Generates UDP packets with sequence number and timestamp; supports fixed/random sizes and pacing. |
| `app/receiver.py` | Counts unique packets and bytes, detects missing sequence numbers using the end marker, and calculates throughput. |
| `network/topology.py` | Creates h1, h2 and the three-switch primary/backup topology. |
| `network/controller.py` | Installs OpenFlow rules, polls flow counters, detects high-rate traffic and link state, and redirects/restores the measured flow. |
| `web/server.py` | Starts Mininet experiments, manages the controller process, serves the dashboard API and records experiment history. |
| `web/index.html` | Experiment configuration, live network view, telemetry, events, results and comparisons. |
| `integration/run_demo.py` | Command-line end-to-end Mininet experiment runner. |

## Setup

The integrated network requires a Linux environment with Mininet, Open vSwitch, Ryu and Python 3. The dashboard does not replace those dependencies; it orchestrates them.

Install system packages (Ubuntu):

```bash
sudo apt update
sudo apt install -y mininet openvswitch-switch python3 python3-pip
```

Install Ryu in a compatible Python environment. Ryu/eventlet compatibility varies by Python version, so use the working environment validated in your lab rather than upgrading dependencies during the demo. Verify:

```bash
ryu-manager --version
mn --version
ovs-vsctl --version
```

Install any missing Python dependencies in that same environment. The dashboard uses the Python standard library plus Mininet/Ryu.

## Run the dashboard (recommended demo)

From the repository root:

```bash
sudo mn -c
sudo python3 web/server.py
```

Open **http://127.0.0.1:8000** in a browser on the same machine.

1. Choose **Dynamic** or **Static** mode.
2. Choose fixed or random packet sizes. A seed makes random packet sizes reproducible.
3. Set packet rate, size/range and duration.
4. Optionally set packet loss on the receiver-facing link or schedule a primary-link failure.
5. Run the experiment and inspect the live topology, switch rate and controller events.
6. Use the Result and Comparison tabs to inspect measurements and compare static/dynamic runs.

The web server must be run with root privileges because it creates Mininet namespaces and switches. Do not expose this development dashboard to an untrusted network; it is intended for a controlled local lab.

## Run a command-line experiment

```bash
sudo mn -c
sudo python3 integration/run_demo.py --mode dynamic --rate 500 --duration 10 --pattern random --seed 42
```

Use `--mode static` for a static-controller baseline. The script starts and stops its own Ryu controller. Results are written under `results/`.

## Packet format and measurement notes

Each data datagram begins with a 12-byte network-order header: unsigned 32-bit sequence number and 64-bit timestamp. The sender sends five end markers; each marker includes the expected data-packet count. The receiver uses unique sequence numbers and the reported expected count to calculate missing packets.

The application counts UDP payload bytes. OpenFlow switch byte counters include link/network/transport headers and therefore should be larger. For this Ethernet/IPv4/UDP Mininet setup, the expected difference is 42 bytes per packet, excluding any additional encapsulation. The five end markers are also counted by the switch but excluded from application data-packet totals. Do not compare raw byte totals without accounting for these differences.

The switch counter is a cumulative flow counter. Compare deltas from the beginning and end of a single run, not arbitrary snapshots. Flow-stat polling is periodic, so the last sample may lag the end of traffic slightly.

## Controller behaviour

- **Static mode:** keeps the measurement flow on the primary path; it records link events but does not dynamically redirect on high-rate conditions or link failure.
- **Dynamic mode:** redirects the measurement flow to the backup path when the observed rate exceeds `SDN_HIGH_RATE_PPS` (default 1500). It restores the primary path after the rate falls below `SDN_LOW_RATE_PPS` (default 1000), provided the primary link is available.
- **Link failure:** a primary-link-down event causes dynamic mode to fail over to the backup path; a recovered primary link can be selected again when the high-rate condition clears.

Thresholds can be changed through environment variables before starting the controller/server. Keep the low threshold lower than the high threshold.

### Flow rules and polling

There is no table-miss rule and no packet-in handling: on connect the controller deletes all flows and installs explicit rules, so unmatched packets are dropped (OpenFlow 1.3 default) and data packets are never sent to the controller.

| Switch | Priority | Match -> action |
|---|---|---|
| s1 | 100 | in_port 1, IPv4 UDP, dst 10.0.0.2 -> port 2 (primary) or port 3 (backup). **Measurement rule.** |
| s1 | 60 | ARP from port 1 -> ports 2 and 3 |
| s1 | 50 | in_port 2 -> 1, in_port 3 -> 1 (return traffic) |
| s1 | 30 | in_port 1 -> 2 (other traffic such as ping) |
| s2 | 50 | port 1 <-> port 2 (backup path) |
| s3 | 50 | port 2 -> 1, port 3 -> 1, port 1 -> 2 |
| s3 | 60 | ARP from port 1 -> ports 2 and 3 |

The controller requests flow statistics for the measurement rule on s1 every **0.5 s** and writes the packet and byte counters to the live telemetry file. Path changes use `OFPFC_MODIFY_STRICT`, so the counters are not reset on reroute.

### Receiver (threaded)

`app/receiver.py` runs one worker thread per sender (`ip:port`), so several senders to one port are counted separately. It stops when every sender seen has sent its end marker (plus a 0.5 s drain) or at the deadline, so a long link outage is not mistaken for the end of the run. Per-sender results are in the `senders` field of the receiver JSON.

## Results and reproducibility

The dashboard appends experiment rows to `results/experiments.csv`. Current-run JSON artifacts include `last_sender.json`, `last_receiver.json`, `live_telemetry.json` and `last_experiment.json`. These are generated measurements; do not present sample/demo-mode data as real Mininet measurements.

Before final submission, run and retain evidence for:

1. Normal fixed-size transfer.
2. Reproducible random-size transfer (same seed twice).
3. Configured packet loss and receiver-side loss detection.
4. Static versus dynamic primary-link failure experiment.
5. Congestion-triggered path redirection at a rate above the configured threshold, followed by recovery below the lower threshold.
6. Application-versus-switch packet and byte comparison, explaining end markers and headers.

A successful Python syntax check does not prove Mininet/Ryu runtime correctness. Validate the above on the actual lab machine and keep the resulting logs/screenshots.

## Repository layout

```text
app/          UDP sender and receiver
network/      Mininet topology and Ryu controller
web/          Browser dashboard and local experiment server
integration/  Command-line Mininet demo runner
docs/         Architecture, test plan and network guide
results/      Example screenshots and generated experiment data
```

## Ubuntu 22.04 quick start (updated)

After creating `.venv` with the project's Python dependencies and installing Mininet/OVS:

```bash
cd ~/sdn-measurement-project
./start.sh
```

If Ryu is installed in a different Python environment from Mininet (for example a Python 3.9 virtual environment for Ryu), start with `RYU_PYTHON=/path/to/that/python ./start.sh`. The dashboard then launches the controller with that interpreter. If `RYU_PYTHON` is not set, the controller uses the same Python as the dashboard.

Open `http://127.0.0.1:8000` inside the VM. The script cleans Mininet and starts the dashboard. Ryu is launched automatically via `run_ryu.py` (Eventlet compatibility wrapper). Do not launch another Ryu process at the same time. See `docs/TIMING_AND_VALIDATION.md` for timing and outage semantics.


## Advanced random-rate traffic

The dashboard defaults to **Random rate (300–2000 packets/s)** with reordering disabled. Constant and Burst rate modes are also selectable. See [docs/ADVANCED_TRAFFIC.md](docs/ADVANCED_TRAFFIC.md) for the algorithm, CLI flags, seed reproducibility, and measurement notes.


## Optional packet reordering

Enable **Packet reordering** in the Traffic panel (off by default). See `docs/PACKET_REORDERING.md` for methodology and limitations. The results show out-of-order packet count and percentage.

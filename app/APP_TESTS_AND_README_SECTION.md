# UDP Application (Person 1: Harsh Vardhan Lohia, PES1UG25CS199)

## Files

| File | Purpose |
|---|---|
| `app/sender.py` | Sends numbered UDP packets at a fixed rate and size, counts packets and bytes sent, then sends an end marker carrying the total. |
| `app/receiver.py` | Receives packets, counts packets and bytes per sender, detects loss from sequence numbers, computes throughput, writes `/tmp/app_results.json`. |

## Concurrency design

The receiver's main thread only receives datagrams and timestamps them. Each distinct sender IP gets its own worker thread with its own queue. The worker does the counting, loss detection and end-marker handling for that sender. Several senders can therefore be served at the same time without affecting each other's statistics. The sender is a single-threaded client, because one client sends one paced stream.

## How to run

Start the receiver first, then the sender.

```
python3 app/receiver.py --port 5001 --out /tmp/app_results.json
python3 app/sender.py --dst 10.0.0.2 --port 5001 --rate 100 --size 1000 --duration 10
```

### Sender options
| Option | Default | Meaning |
|---|---|---|
| `--dst` | required | Destination IPv4 address |
| `--port` | 5001 | Destination UDP port |
| `--rate` | 100 | Packets per second |
| `--size` | 1000 | Packet size in bytes (12 to 1472) |
| `--duration` | 10 | Seconds to send |
| `--out` | none | Optional JSON file for sender results |
| `--src` | none | Optional local source IP to bind (used for testing several senders on one machine) |

### Receiver options
| Option | Default | Meaning |
|---|---|---|
| `--port` | 5001 | UDP port to listen on |
| `--bind` | 0.0.0.0 | Address to bind |
| `--timeout` | 3 | Stop after this many seconds without packets |
| `--out` | /tmp/app_results.json | Results file |
| `--drop-every` | 0 (off) | Test only: ignore every Nth data packet from each sender to simulate loss |

## Packet format

| Bytes | Content |
|---|---|
| 0 to 3 | Sequence number, big-endian unsigned integer, starting at 0 |
| 4 to end | Padding, up to the configured packet size |

End marker: a packet whose first 4 bytes are `0xFFFFFFFF`, followed by 8 bytes holding the total number of packets sent (big-endian unsigned). It is sent 5 times because UDP may lose it. If every copy is lost, the receiver estimates the total as the highest sequence number seen plus one and says so in the results.

## Results file (`/tmp/app_results.json`)

One entry per sender IP:

```
{
  "10.0.0.1": {
    "sender": "10.0.0.1",
    "packets_sent_reported": 1000,
    "sent_source": "end_marker",
    "packets_received": 1000,
    "bytes_received": 1000000,
    "packets_lost": 0,
    "loss_percent": 0.0,
    "duration_s": 9.99,
    "throughput_bps": 800800.0,
    "ignored_malformed": 0
  }
}
```

Throughput is received bytes x 8 divided by the time between the first and last data packet. Bytes are counted at the application (payload only), so they exclude Ethernet, IP and UDP headers.

## Application test cases

| ID | What is tested | How | Expected | Actual |
|---|---|---|---|---|
| A1 | Normal transfer and throughput | receiver, then sender at 200 pkt/s, 1000 B, 5 s (loopback) | received = sent = 1000, loss 0%, throughput close to 200 x 1000 x 8 = 1.6 Mbit/s | 1000 sent, 1000 received, 0 lost (0.00%), 1.602 Mbit/s (screenshots: `results/A1_sender.png`, `results/A1_receiver.png`) |
| A2 | Forced loss detection | receiver with `--drop-every 10`, same sender | about 10% loss (100 of 1000) | 1000 sent, 900 received, 100 lost (10.00%), 1.443 Mbit/s (screenshots: `results/A2_receiver.png`, `results/A2_sender.png`) |
| A3 | Invalid arguments | `--dst 999.1.1.1`, `--rate 0`, `--size 5000` | clear error message each time, nothing is sent | Rejected each time: "'999.1.1.1' is not a valid IPv4 address"; "--rate must be greater than 0"; "--size must be between 12 and 1472 bytes" (screenshot: `results/A3_errors.png`) |
| A4 | Two senders at once | `--src 127.0.0.2` and `--src 127.0.0.3` sending to one receiver together | separate entry and correct counts per sender | Two separate entries: 127.0.0.3 sent 1000, received 1000, 0% loss, 1.001 Mbit/s; 127.0.0.2 sent 400, received 400, 0% loss, 0.802 Mbit/s (screenshots: `results/A4_receiver.png`, `results/A4_senders.png`) |
| A5 | Inside Mininet (no SDN controller) | `sudo mn --topo single,3` (Mininet fell back to a plain OVS bridge); h2 receiver, h1 sender at 100 pkt/s, 1000 B, 10 s | received equals sent, throughput close to 0.8 Mbit/s | pingall 0% dropped (6/6). 1000 sent, 1000 received, 0 lost (0.00%), 0.801 Mbit/s over 9.990 s (screenshot: `results/A5_mininet.png`) |
| A6 | Inside Mininet with the team's SDN controller | topology and controller from the repo; h2 receiver, h1 sender | received equals sent; controller installs the UDP flow and reports its counters | |

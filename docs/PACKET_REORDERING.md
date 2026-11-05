# Optional packet reordering

The dashboard **Enable packet reordering** checkbox is off by default. When on, the sender generates normal sequential packet IDs but buffers up to 8 UDP datagrams and shuffles their *transmission order*. The final partial batch is flushed before the five EOF markers. This is a sender-generated impairment, **not evidence that OpenFlow switches reorder traffic**.

The receiver counts each unique packet whose sequence number is lower than the highest sequence number previously received as out of order. It also reports `reordering_percent = out_of_order_packets / app_received * 100`. Packet loss is calculated separately and duplicate packets are not counted twice.

The sender uses independent random-number generators for sizes, rate windows, and shuffling. The same seed reproduces the shuffling choices under the same packet count and timing conditions. A live network can still introduce nondeterminism.

CLI: `python3 app/sender.py --rate-pattern random --rate-min 300 --rate-max 2000 --reorder --seed 42 ...`

**Note:** buffering batches introduces small timing bursts. The 8-packet batch size is fixed. Compare enabled/disabled runs at identical traffic settings and report the actual sent packet counts.

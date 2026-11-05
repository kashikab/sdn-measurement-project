# Advanced traffic generation

The dashboard defaults to **Random rate**, 300–2000 packets/s, with **packet reordering disabled**. The existing constant-rate experiment remains available by selecting Constant. Burst alternates the minimum and maximum once per second.

## Implementation

`app/sender.py` selects a new target packet rate at each one-second traffic window. In random mode, `random.Random(seed + 1000003).randint(min_rate, max_rate)` selects an integer target in packets/s. A monotonic-clock scheduler spaces datagrams at `1 / target_rate` seconds, and switches to the next target at the next one-second boundary. Actual packet counts can vary due to Linux scheduling and network overhead. No packet sequence numbers are deliberately shuffled.

The packet-size RNG uses its own seeded `random.Random(seed)` instance, so enabling rate randomization does not change the packet-size sequence. `os.urandom()` fills the payload bytes. The sender records the selected rate for each second in `results/last_sender.json` under `rate_windows`, alongside actual sent counts and switch-byte expectations. The switch measures *observed* rate, which may lag the target and varies with polling.

## Fair comparisons

Use the same seed, min/max rate, duration, packet-size pattern, and failure schedule for static and dynamic tests. Packet totals are measured from the sender rather than assumed to be `rate × duration`. For a high-rate policy demonstration, choose a range spanning the controller's 1500 pps high and 1000 pps low thresholds, but allow for the controller's sampling window.

## Command line

```bash
python3 app/sender.py --ip 127.0.0.1 --duration 5 --rate-pattern random --rate-min 300 --rate-max 2000 --pattern random --seed 42
```

Packet reordering is intentionally **disabled** in this release. The UI labels this explicitly; the sender emits sequential sequence numbers.

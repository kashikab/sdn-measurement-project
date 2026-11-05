#!/usr/bin/env python3
import argparse
import json
import os
import random
import socket
import struct
import time

HEADER_FORMAT = "!Id"  # seq_num (4B uint32), timestamp (8B double) = 12 bytes
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
EOF_SEQ = 0xFFFFFFFF

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ip", default="10.0.0.2")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--rate", type=float, default=100.0, help="Constant-mode packets per second")
    parser.add_argument("--rate-pattern", choices=["constant", "random", "burst"], default="constant")
    parser.add_argument("--rate-min", type=float, default=300.0)
    parser.add_argument("--rate-max", type=float, default=2000.0)
    parser.add_argument("--duration", type=float, default=15.0)
    parser.add_argument("--pattern", choices=["fixed", "random"], default="fixed")
    parser.add_argument("--size", type=int, default=1000)
    parser.add_argument("--size-min", type=int, default=200)
    parser.add_argument("--size-max", type=int, default=1472)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--reorder", action="store_true", help="Shuffle transmission order within batches of 8 UDP packets")
    parser.add_argument("--out", default="results/last_sender.json")
    args = parser.parse_args()

    import ipaddress
    try:
        ipaddress.IPv4Address(args.ip)
    except ipaddress.AddressValueError:
        parser.error("--ip must be a valid IPv4 address")
    if args.rate <= 0:
        parser.error("--rate must be greater than 0")
    if not (1 <= args.rate_min <= args.rate_max <= 5000):
        parser.error("rate range must satisfy 1 <= --rate-min <= --rate-max <= 5000")
    if args.duration <= 0:
        parser.error("--duration must be greater than 0")
    if not (HEADER_SIZE <= args.size <= 1472):
        parser.error(f"--size must be between {HEADER_SIZE} and 1472 bytes")
    if not (HEADER_SIZE <= args.size_min <= args.size_max <= 1472):
        parser.error("size range must satisfy HEADER_SIZE <= --size-min <= --size-max <= 1472")
    # Independent PRNG streams: changing the rate pattern must not change packet sizes.
    size_rng = random.Random(args.seed)
    rate_rng = random.Random(None if args.seed is None else args.seed + 1000003)
    reorder_rng = random.Random(None if args.seed is None else args.seed + 2000003)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    sent_packets = 0
    sent_payload_bytes = 0
    expected_wire_bytes = 0

    pending = []
    reordered_at_source = 0

    def flush_pending():
        nonlocal reordered_at_source
        if not pending:
            return
        batch = list(pending)
        if args.reorder and len(batch) > 1:
            reorder_rng.shuffle(batch)
            # Guarantee a changed order even if a shuffle happens to be identical.
            if [seq for seq, _ in batch] == [seq for seq, _ in pending]:
                batch[0], batch[1] = batch[1], batch[0]
            reordered_at_source += sum(a[0] != b[0] for a, b in zip(batch, pending))
        for _, payload in batch:
            sock.sendto(payload, (args.ip, args.port))
        pending.clear()

    start_time = time.monotonic()
    # Rate changes at each one-second traffic window; the packet scheduler resets
    # at each boundary to prevent a previous rate carrying into the next window.
    rate_windows = []
    window_index = -1
    next_send = start_time
    while True:
        now = time.monotonic()
        elapsed = now - start_time
        if elapsed >= args.duration:
            break
        current_window = int(elapsed)
        if current_window != window_index:
            window_index = current_window
            if args.rate_pattern == "random":
                current_rate = rate_rng.randint(int(args.rate_min), int(args.rate_max))
            elif args.rate_pattern == "burst":
                current_rate = args.rate_max if current_window % 2 else args.rate_min
            else:
                current_rate = args.rate
            rate_windows.append({"second": current_window, "target_pps": current_rate})
            next_send = max(now, start_time + current_window)
        # Never schedule a packet into the following rate window.
        if next_send >= min(start_time + current_window + 1, start_time + args.duration):
            time.sleep(min(0.01, max(0, start_time + current_window + 1 - now)))
            continue
        wait = next_send - now
        if wait > 0:
            time.sleep(min(wait, 0.05))
            continue
        if args.pattern == "random":
            pkt_size = size_rng.randint(args.size_min, args.size_max)
        else:
            pkt_size = args.size
        header = struct.pack(HEADER_FORMAT, sent_packets, time.time())
        padding = os.urandom(pkt_size - HEADER_SIZE)
        if args.reorder:
            pending.append((sent_packets, header + padding))
            if len(pending) >= 8:
                flush_pending()
        else:
            sock.sendto(header + padding, (args.ip, args.port))
        sent_packets += 1
        sent_payload_bytes += pkt_size
        expected_wire_bytes += pkt_size + 42  # L2/L3/L4 headers
        next_send += 1.0 / current_rate
        # When overloaded, skip stale pacing deadlines instead of emitting
        # a catch-up burst that distorts the intended traffic pattern.
        if next_send < time.monotonic() - 1.0 / current_rate:
            next_send = time.monotonic()

    flush_pending()  # Never leave buffered data behind when traffic ends.

    # Send EXACTLY 5 end markers (12 bytes each) as required by index.html line 292
    eof_pkt = struct.pack(HEADER_FORMAT, EOF_SEQ, time.time()) + struct.pack("!I", sent_packets)
    for _ in range(5):
        sock.sendto(eof_pkt, (args.ip, args.port))
        expected_wire_bytes += (len(eof_pkt) + 42)
        time.sleep(0.02)

    sock.close()
    out_dir = os.path.dirname(args.out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w") as f:
        json.dump({
            "app_sent": sent_packets,
            "sent_payload_bytes": sent_payload_bytes,
            "expected_switch_packets": sent_packets + 5,
            "expected_switch_bytes": expected_wire_bytes,
            "rate_pattern": args.rate_pattern,
            "rate_min_pps": args.rate_min,
            "rate_max_pps": args.rate_max,
            "rate_windows": rate_windows,
            "packet_reordering": "enabled" if args.reorder else "disabled",
            "reorder_batch_size": 8 if args.reorder else 0,
            "source_reordered_positions": reordered_at_source,
            "seed": args.seed
        }, f)

if __name__ == "__main__":
    main()

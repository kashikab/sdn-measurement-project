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
    parser.add_argument("--rate", type=float, default=100.0, help="Packets per second")
    parser.add_argument("--duration", type=float, default=15.0)
    parser.add_argument("--pattern", choices=["fixed", "random"], default="fixed")
    parser.add_argument("--size", type=int, default=1000)
    parser.add_argument("--size-min", type=int, default=200)
    parser.add_argument("--size-max", type=int, default=1472)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--out", default="results/last_sender.json")
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    interval = 1.0 / max(1.0, args.rate)

    sent_packets = 0
    sent_payload_bytes = 0
    expected_wire_bytes = 0

    start_time = time.time()
    next_send = start_time

    while True:
        now = time.time()
        if now - start_time >= args.duration:
            break

        if args.pattern == "random":
            pkt_size = random.randint(max(HEADER_SIZE, args.size_min), min(1472, args.size_max))
        else:
            pkt_size = max(HEADER_SIZE, min(1472, args.size))

        header = struct.pack(HEADER_FORMAT, sent_packets, now)
        padding = os.urandom(pkt_size - HEADER_SIZE)
        sock.sendto(header + padding, (args.ip, args.port))

        sent_packets += 1
        sent_payload_bytes += pkt_size
        expected_wire_bytes += (pkt_size + 42)  # +42B L2/L3/L4 headers

        next_send += interval
        sleep_dur = next_send - time.time()
        if sleep_dur > 0:
            time.sleep(sleep_dur)

    # Send EXACTLY 5 end markers (12 bytes each) as required by index.html line 292
    eof_pkt = struct.pack(HEADER_FORMAT, EOF_SEQ, time.time())
    for _ in range(5):
        sock.sendto(eof_pkt, (args.ip, args.port))
        expected_wire_bytes += (HEADER_SIZE + 42)
        time.sleep(0.02)

    sock.close()
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump({
            "app_sent": sent_packets,
            "sent_payload_bytes": sent_payload_bytes,
            "expected_switch_packets": sent_packets + 5,
            "expected_switch_bytes": expected_wire_bytes
        }, f)

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
import argparse
import json
import os
import socket
import struct
import time

HEADER_FORMAT = "!Id"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
EOF_SEQ = 0xFFFFFFFF

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ip", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--duration", type=float, default=15.0)
    parser.add_argument("--out", default="results/last_receiver.json")
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.ip, args.port))
    sock.settimeout(0.5)

    rx_packets = 0
    rx_bytes = 0
    first_ts = None
    last_ts = None
    outage_s = 0.0
    prev_pkt_time = None

    deadline = time.time() + args.duration + 4.0

    while time.time() < deadline:
        try:
            data, _ = sock.recvfrom(65535)
        except socket.timeout:
            if first_ts is not None and (time.time() - last_ts > 2.5):
                break
            continue

        now = time.time()
        if len(data) < HEADER_SIZE:
            continue

        seq, _ = struct.unpack(HEADER_FORMAT, data[:HEADER_SIZE])
        if seq == EOF_SEQ:
            break

        if first_ts is None:
            first_ts = now
        if prev_pkt_time is not None:
            gap = now - prev_pkt_time
            if gap > 0.25:  # Any inter-packet gap > 250ms counts toward link outage
                outage_s += gap
        prev_pkt_time = now
        last_ts = now

        rx_packets += 1
        rx_bytes += len(data)

    sock.close()
    active_time = max(0.001, args.duration)
    throughput_bps = int((rx_bytes * 8) / active_time)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump({
            "app_received": rx_packets,
            "rx_bytes": rx_bytes,
            "app_throughput_bps": throughput_bps,
            "outage_s": round(outage_s, 2)
        }, f)

if __name__ == "__main__":
    main()

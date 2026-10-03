#!/usr/bin/env python3
"""UDP sender for the Bandwidth Meter and Traffic Analyzer (Person 1).

Sends numbered UDP packets at a fixed rate for a fixed duration, counts what
it sends, then sends an end-marker packet carrying the total sent.
"""
import argparse
import json
import socket
import struct
import sys
import time

END_SEQ = 0xFFFFFFFF
HEADER = 4            # sequence number bytes
MIN_SIZE = 12         # end marker needs 12 bytes; keep all packets at least this big


def parse_args():
    ap = argparse.ArgumentParser(description="UDP bandwidth sender")
    ap.add_argument("--dst", required=True, help="destination IP address")
    ap.add_argument("--port", type=int, default=5001, help="destination UDP port (default 5001)")
    ap.add_argument("--rate", type=float, default=100.0, help="packets per second (default 100)")
    ap.add_argument("--size", type=int, default=1000, help="packet size in bytes (default 1000)")
    ap.add_argument("--duration", type=float, default=10.0, help="seconds to send (default 10)")
    ap.add_argument("--out", default="", help="optional JSON file for sender results")
    ap.add_argument("--src", default="", help="optional local source IP to bind (for testing)")
    return ap.parse_args()


def validate(args):
    try:
        socket.inet_aton(args.dst)
    except OSError:
        sys.exit(f"error: '{args.dst}' is not a valid IPv4 address")
    if not (1 <= args.port <= 65535):
        sys.exit("error: port must be between 1 and 65535")
    if args.rate <= 0:
        sys.exit("error: --rate must be greater than 0")
    if args.duration <= 0:
        sys.exit("error: --duration must be greater than 0")
    if not (MIN_SIZE <= args.size <= 1472):
        sys.exit(f"error: --size must be between {MIN_SIZE} and 1472 bytes "
                 "(1472 keeps the packet inside one Ethernet frame)")


def main():
    args = parse_args()
    validate(args)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if args.src:
        try:
            sock.bind((args.src, 0))
        except OSError as e:
            sys.exit(f"error: cannot bind source address {args.src}: {e}")
    padding = b"x" * (args.size - HEADER)
    interval = 1.0 / args.rate
    dest = (args.dst, args.port)

    sent_packets = 0
    sent_bytes = 0
    errors = 0
    total = max(1, round(args.rate * args.duration))   # exact packet count
    start = time.time()
    next_t = start

    print(f"[sender] {args.rate:g} pkt/s, {args.size} B, {args.duration:g} s "
          f"({total} packets) -> {args.dst}:{args.port}", flush=True)

    try:
        for i in range(total):
            pkt = struct.pack("!I", sent_packets) + padding
            try:
                sock.sendto(pkt, dest)
                sent_packets += 1
                sent_bytes += len(pkt)
            except OSError as e:
                errors += 1
                if errors == 1:
                    print(f"[sender] send error: {e} (will keep trying)", file=sys.stderr)
                if errors >= 50:
                    print("[sender] too many send errors, giving up", file=sys.stderr)
                    break
            next_t = start + (i + 1) * interval   # no drift: always relative to start
            delay = next_t - time.time()
            if delay > 0:
                time.sleep(delay)
    except KeyboardInterrupt:
        print("\n[sender] interrupted", flush=True)

    elapsed = time.time() - start

    # End marker: sent several times because UDP may lose it. The receiver
    # uses the first one it sees and ignores later copies.
    marker = struct.pack("!IQ", END_SEQ, sent_packets)
    marker += b"\x00" * (args.size - len(marker))
    for _ in range(5):
        try:
            sock.sendto(marker, dest)
        except OSError:
            pass
        time.sleep(0.05)
    sock.close()

    offered_bps = sent_bytes * 8 / elapsed if elapsed > 0 else 0.0
    print("\n===== Sender summary =====")
    print(f"  Packets sent    : {sent_packets}")
    print(f"  Bytes sent      : {sent_bytes}")
    print(f"  Send errors     : {errors}")
    print(f"  Elapsed         : {elapsed:.3f} s")
    print(f"  Offered rate    : {offered_bps / 1e6:.3f} Mbit/s")

    if args.out:
        try:
            with open(args.out, "w") as f:
                json.dump({"packets_sent": sent_packets, "bytes_sent": sent_bytes,
                           "send_errors": errors, "elapsed_s": elapsed,
                           "offered_bps": offered_bps}, f, indent=2)
        except OSError as e:
            print(f"[sender] warning: could not write {args.out}: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()

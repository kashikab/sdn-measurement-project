#!/usr/bin/env python3
"""UDP receiver for the Bandwidth Meter and Traffic Analyzer (Person 1).

Design (concurrency):
  * The main thread only receives datagrams and timestamps them.
  * Each distinct sender IP gets its own worker thread (with its own queue)
    that does the counting, loss detection and end-marker handling.
  So several senders can be served at the same time without interfering.

Packet format (see docs/CONTRACT.md):
    bytes 0-3   : sequence number (big-endian unsigned int)
    bytes 4-end : padding
    End marker  : a packet whose first 4 bytes are 0xFFFFFFFF, followed by
                  8 bytes (big-endian unsigned long long) = total packets sent.
"""
import argparse
import json
import queue
import socket
import struct
import sys
import threading
import time

END_SEQ = 0xFFFFFFFF


def parse_args():
    ap = argparse.ArgumentParser(description="UDP bandwidth receiver")
    ap.add_argument("--port", type=int, default=5001, help="UDP port to listen on (default 5001)")
    ap.add_argument("--bind", default="0.0.0.0", help="address to bind (default 0.0.0.0)")
    ap.add_argument("--timeout", type=float, default=3.0,
                    help="stop after this many seconds of silence (default 3)")
    ap.add_argument("--out", default="/tmp/app_results.json", help="results JSON file")
    ap.add_argument("--drop-every", type=int, default=0,
                    help="TEST ONLY: ignore every Nth data packet per sender to simulate loss (0 = off)")
    return ap.parse_args()


class SenderWorker(threading.Thread):
    """Handles all packets from one sender IP."""

    def __init__(self, ip, drop_every):
        super().__init__(daemon=True, name=f"worker-{ip}")
        self.ip = ip
        self.drop_every = drop_every
        self.q = queue.Queue()
        self.received = 0
        self.bytes = 0
        self.first_time = None
        self.last_time = None
        self.max_seq = -1
        self.sent_reported = None
        self.ignored = 0
        self.seen = 0
        self.done = False       # set once the end marker has been processed

    def run(self):
        while True:
            item = self.q.get()
            if item is None:            # sentinel from main thread: finish
                return
            data, now = item
            if len(data) < 4:
                self.ignored += 1
                continue
            seq = struct.unpack("!I", data[:4])[0]
            if seq == END_SEQ:
                if len(data) >= 12:
                    if self.sent_reported is None:      # first copy wins
                        self.sent_reported = struct.unpack("!Q", data[4:12])[0]
                    self.done = True
                else:
                    self.ignored += 1
                continue
            self.seen += 1
            if self.drop_every and self.seen % self.drop_every == 0:
                continue                # simulated loss
            if self.first_time is None:
                self.first_time = now
            self.last_time = now
            self.received += 1
            self.bytes += len(data)
            self.max_seq = max(self.max_seq, seq)

    def result(self):
        # If every end marker was lost, fall back to highest sequence seen + 1.
        if self.sent_reported is not None:
            sent, source = self.sent_reported, "end_marker"
        else:
            sent, source = self.max_seq + 1, "estimated_from_max_seq"
        duration = throughput = None
        if self.first_time is not None and self.last_time > self.first_time:
            duration = self.last_time - self.first_time
            throughput = self.bytes * 8 / duration
        lost = max(0, sent - self.received)
        return {
            "sender": self.ip,
            "packets_sent_reported": sent,
            "sent_source": source,
            "packets_received": self.received,
            "bytes_received": self.bytes,
            "packets_lost": lost,
            "loss_percent": (100.0 * lost / sent) if sent > 0 else 0.0,
            "duration_s": duration,
            "throughput_bps": throughput,
            "ignored_malformed": self.ignored,
        }


def main():
    args = parse_args()
    if not (1 <= args.port <= 65535):
        sys.exit("error: port must be between 1 and 65535")
    if args.drop_every < 0:
        sys.exit("error: --drop-every must be >= 0")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((args.bind, args.port))
    except OSError as e:
        sys.exit(f"error: cannot bind {args.bind}:{args.port}: {e}")

    print(f"[receiver] listening on {args.bind}:{args.port} "
          f"(stops after {args.timeout}s of silence)", flush=True)

    workers = {}
    seen_any = False
    try:
        while True:
            # Before the first packet, wait longer so the sender can start late.
            sock.settimeout(60.0 if not seen_any else args.timeout)
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                break
            now = time.time()
            seen_any = True
            ip = addr[0]
            w = workers.get(ip)
            if w is None:
                w = SenderWorker(ip, args.drop_every)
                w.start()
                workers[ip] = w
                print(f"[receiver] new sender {ip}", flush=True)
            w.q.put((data, now))
            # Stop early once every sender heard from has sent its end marker
            # (check after giving the workers a moment to drain their queues).
            if w.done and all(x.done for x in workers.values()):
                time.sleep(0.2)
                if all(x.done and x.q.empty() for x in workers.values()):
                    break
    except KeyboardInterrupt:
        print("\n[receiver] interrupted, reporting what was received", flush=True)
    finally:
        sock.close()
        for w in workers.values():
            w.q.put(None)
        for w in workers.values():
            w.join(timeout=5)

    results = {ip: w.result() for ip, w in workers.items()}

    try:
        with open(args.out, "w") as f:
            json.dump(results, f, indent=2)
    except OSError as e:
        print(f"[receiver] warning: could not write {args.out}: {e}", file=sys.stderr)

    print("\n===== Receiver summary =====")
    if not results:
        print("No packets received.")
    for ip, r in results.items():
        print(f"Sender {ip}")
        print(f"  Packets sent (reported) : {r['packets_sent_reported']} ({r['sent_source']})")
        print(f"  Packets received        : {r['packets_received']}")
        print(f"  Bytes received          : {r['bytes_received']}")
        print(f"  Packets lost            : {r['packets_lost']} ({r['loss_percent']:.2f}%)")
        if r["throughput_bps"] is not None:
            print(f"  Duration                : {r['duration_s']:.3f} s")
            print(f"  Throughput              : {r['throughput_bps'] / 1e6:.3f} Mbit/s")
        else:
            print("  Throughput              : n/a (too few packets)")
    print(f"Results written to {args.out}")


if __name__ == "__main__":
    main()

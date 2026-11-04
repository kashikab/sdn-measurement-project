#!/usr/bin/env python3
import socket
import time
import struct
import random
import os
import argparse
import csv

HEADER_FORMAT = '!IId'  # seq_num (uint32), pkt_size (uint32), timestamp (double)
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

def run_sender(target_ip, target_port, duration, mode, min_size, max_size, rate_mbps, csv_path, flow_id):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    start_time = time.time()
    seq = 0
    total_bytes = 0

    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    csv_file = open(csv_path, 'w', newline='')
    writer = csv.writer(csv_file)
    writer.writerow(['timestamp', 'seq', 'size_bytes', 'instant_rate_mbps', 'mode', 'flow_id'])

    print(f"[{flow_id}] Sending to {target_ip}:{target_port} | Mode: {mode} | Duration: {duration}s")

    window_start = start_time
    window_bytes = 0

    try:
        while time.time() - start_time < duration:
            now = time.time()
            elapsed = now - start_time

            # Determine packet size & inter-packet delay based on mode
            if mode == 'fixed':
                pkt_size = max_size
                target_rate = rate_mbps
                sleep_time = (pkt_size * 8) / (target_rate * 1e6)
            elif mode == 'random':
                pkt_size = random.randint(min_size, max_size)
                # Randomize instantaneous target rate around rate_mbps (+/- 50%)
                target_rate = max(0.5, random.uniform(rate_mbps * 0.5, rate_mbps * 1.5))
                sleep_time = (pkt_size * 8) / (target_rate * 1e6)
            elif mode == 'ramp':
                pkt_size = random.randint(min_size, max_size)
                # Linearly ramp traffic from 1 Mbps up to rate_mbps over the run duration
                progress = min(1.0, elapsed / (duration * 0.75))
                target_rate = 1.0 + progress * (rate_mbps - 1.0)
                sleep_time = (pkt_size * 8) / (target_rate * 1e6)

            pkt_size = max(pkt_size, HEADER_SIZE + 4)
            payload_size = pkt_size - HEADER_SIZE

            header = struct.pack(HEADER_FORMAT, seq, pkt_size, now)
            payload = os.urandom(payload_size)
            sock.sendto(header + payload, (target_ip, target_port))

            seq += 1
            total_bytes += pkt_size
            window_bytes += pkt_size

            # Log application send metrics every 0.5 seconds
            if now - window_start >= 0.5:
                inst_mbps = (window_bytes * 8) / ((now - window_start) * 1e6)
                writer.writerow([round(now - start_time, 2), seq, pkt_size, round(inst_mbps, 3), mode, flow_id])
                csv_file.flush()
                window_start = now
                window_bytes = 0

            if sleep_time > 0:
                time.sleep(sleep_time)

        # Send 3 EOF termination packets (seq = 0xFFFFFFFF)
        eof_pkt = struct.pack(HEADER_FORMAT, 0xFFFFFFFF, HEADER_SIZE, time.time())
        for _ in range(3):
            sock.sendto(eof_pkt, (target_ip, target_port))
            time.sleep(0.02)

    finally:
        total_time = max(0.001, time.time() - start_time)
        avg_mbps = (total_bytes * 8) / (total_time * 1e6)
        print(f"[{flow_id}] Done. Sent {seq} packets ({total_bytes} B) in {total_time:.2f}s | Avg: {avg_mbps:.2f} Mbps")
        csv_file.close()
        sock.close()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Randomized UDP Traffic Sender")
    parser.add_argument('--ip', default='10.0.0.2', help='Receiver IP')
    parser.add_argument('--port', type=int, default=9999, help='Receiver UDP Port')
    parser.add_argument('--duration', type=int, default=20, help='Duration in seconds')
    parser.add_argument('--mode', choices=['fixed', 'random', 'ramp'], default='random')
    parser.add_argument('--min-size', type=int, default=256, help='Minimum UDP payload size')
    parser.add_argument('--max-size', type=int, default=1400, help='Maximum UDP payload size')
    parser.add_argument('--rate', type=float, default=6.0, help='Target rate in Mbps')
    parser.add_argument('--csv', default='results/app_sender.csv', help='Output CSV path')
    parser.add_argument('--id', default='h1_main', help='Flow Identifier')
    args = parser.parse_args()

    run_sender(args.ip, args.port, args.duration, args.mode, args.min_size, args.max_size, args.rate, args.csv, args.id)

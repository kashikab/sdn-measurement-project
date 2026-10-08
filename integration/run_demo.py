import sys
import os
import time
from pathlib import Path

# Resolve absolute path to the repo root
REPO_ROOT = Path(__file__).resolve().parent.parent

# Inject repo root into sys.path to fix the import architecture
sys.path.insert(0, str(REPO_ROOT))

from network.topology import build

def run_integration_test():
    print("Clearing old metrics...")
    # Delete app results and target Gyanesh's actual CSV file
    stats_file = REPO_ROOT / "network" / "results" / "flow_stats.csv"
    os.system("rm -f /tmp/app_results.json")
    if stats_file.exists():
        stats_file.unlink()

    print("Initializing Mininet...")
    net = build(ip='127.0.0.1', port=6653)
    net.start()

    h1 = net.get('h1')
    h2 = net.get('h2')

    # Construct absolute paths for the application scripts
    receiver_script = REPO_ROOT / "app" / "receiver.py"
    sender_script = REPO_ROOT / "app" / "sender.py"
    
    print("Starting UDP Receiver on h2...")
    # Inject absolute script path into the Mininet command
    h2.cmd(f'python3 {receiver_script} --out /tmp/app_results.json &')
    
    time.sleep(1) 

    print("Injecting UDP Traffic from h1...")
    # Inject absolute script path into the Mininet command
    h1.cmd(f'python3 {sender_script} --dst 10.0.0.2 --rate 100 --size 1000 --duration 10')

    print("Transmission complete. Waiting 5 seconds for final controller polling...")
    time.sleep(5) 

    print("Shutting down network...")
    net.stop()

if __name__ == '__main__':
    run_integration_test()

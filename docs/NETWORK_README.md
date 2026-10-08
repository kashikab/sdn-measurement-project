# Network Side (Gyanesh JN, PES1UG25CS189)

## What this part does
It builds a small virtual network and measures the UDP traffic **from inside the switch**, so it can be compared with Harsh's sender/receiver numbers.

- `topology.py` creates h1 (10.0.0.1), h2 (10.0.0.2) and the switch s1.
- `controller.py` tells the switch how to forward packets, and counts only UDP traffic from h1 to h2 on port 5001.
- The counts are saved to `results/flow_stats.csv` every 2 seconds.

## One-time setup (Ubuntu)

**1. Install Mininet, Open vSwitch and git**
```
sudo apt update
sudo apt install -y mininet openvswitch-switch git
```

**2. Install Python 3.9** (Ryu does not install cleanly on newer Python versions)
```
sudo apt install -y software-properties-common
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt update
sudo apt install -y python3.9 python3.9-venv python3.9-distutils
```
(If `python3.9 --version` already works, skip this step.)

**3. Install Ryu in its own environment**
```
python3.9 -m venv ~/ryuenv
source ~/ryuenv/bin/activate
pip install setuptools==57.5.0 wheel pbr
pip install eventlet==0.30.2
pip install --no-build-isolation git+https://github.com/faucetsdn/ryu.git
ryu-manager --version
```
The last command should print a version number.

**Common problems**
- Do **not** run `pip install --upgrade setuptools`. The newest version breaks the Ryu build ("subprocess-exited-with-error").
- A red message about `wheel` and `packaging` at the end of the install can be ignored.
- If `ryu-manager` says `No module named 'oslo'`, run this one line, then try again:
  `grep -rl "oslo\.config" ~/ryuenv/lib/python3.9/site-packages/ryu ~/ryuenv/bin/ryu-manager | xargs -r sed -i 's/oslo\.config/oslo_config/g'`
- If Mininet says it cannot import `OVSKSwitch`, the file is out of date. Use the `topology.py` that came with this README (it uses `OVSSwitch`).
- Use `python3`, not `python`, in all commands.
- Only the controller terminal needs `source ~/ryuenv/bin/activate`. Run `topology.py` in a normal terminal (no `(ryuenv)` in the prompt).

## How to run
Keep `controller.py` and `topology.py` in the same folder. Use two terminals.

**Terminal 1: controller**
```
source ~/ryuenv/bin/activate
ryu-manager controller.py
```
Leave it running.

**Terminal 2: network**
```
sudo mn -c
sudo python3 topology.py
```
At the `mininet>` prompt, test with: `h1 ping -c 3 h2` (should show 0% loss).
Then run Harsh's receiver on h2 and sender on h1.

**To stop:** type `exit` in the network terminal, then press Ctrl+C in the controller terminal.

Rules:
1. Start the controller **first**, then the network.
2. Run `sudo mn -c` **before** starting the controller. Running it later stops the controller.

## The output file: `results/flow_stats.csv`

| Column | Meaning |
|---|---|
| timestamp | Time the reading was taken |
| flow | Which traffic is counted (h1 to h2, UDP port 5001) |
| packet_count | Total packets the switch has seen so far |
| byte_count | Total bytes the switch has seen so far |
| duration_sec | How long the counting rule has existed |

## Things to know for the comparison
- Counts are **running totals**. For one test, use the last row minus the row before traffic started.
- The switch counts **42 extra bytes per packet** (network headers). So:
  switch bytes = application bytes + 42 x number of packets.
- The file updates every 2 seconds, so wait 2 seconds after the sender finishes before reading the last row.
- Restart the controller before each new test to start the counts from zero. This also clears the old CSV.
- Ping and other traffic are **not** counted. Only UDP to port 5001 is.

## Example (included: `flow_stats_sample.csv`)
Test: 100 packets, each with 100 bytes of data.
Result: 100 packets and 14,200 bytes (100 x 142, since 100 data + 42 headers).
(The sample file shows 101 packets and 14,542 bytes because one earlier 342-byte test packet was also counted.)

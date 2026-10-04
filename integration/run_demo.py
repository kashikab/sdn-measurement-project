import os
import time
from network.topology import build

def run_integration_test():
    print("Clearing old metrics...")
    os.system("rm -f /tmp/app_results.json /tmp/flowstats.json")


    print("Initializing Mininet...")
    net = build(ip='127.0.0.1', port=6653)
    net.start()

    
    h1 = net.get('h1')
    h2 = net.get('h2')

    
    print("Starting UDP Receiver on h2...")
    h2.cmd('python3 app/receiver.py --out /tmp/app_results.json &')
    
    time.sleep(1) 

  
    print("Injecting UDP Traffic from h1...")
    h1.cmd('python3 app/sender.py --dst 10.0.0.2 --rate 100 --size 1000 --duration 10')

   
    print("Transmission complete. Waiting 5 seconds for final controller polling...")
    time.sleep(5) 

  
    print("Shutting down network...")
    net.stop()

if __name__ == '__main__':
    run_integration_test()

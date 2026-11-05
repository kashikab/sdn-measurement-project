#!/usr/bin/env python3
"""Ubuntu 22.04 / Python 3.10 compatibility entrypoint for Ryu 4.34."""
import eventlet
eventlet.monkey_patch()
import eventlet.wsgi
if not hasattr(eventlet.wsgi, "ALREADY_HANDLED"):
    eventlet.wsgi.ALREADY_HANDLED = object()
from ryu.cmd.manager import main
if __name__ == "__main__":
    main()

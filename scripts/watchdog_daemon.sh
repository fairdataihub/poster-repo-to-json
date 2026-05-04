#!/bin/bash
# Long-running watchdog daemon — runs inside WSL.
# Loops forever, calling watchdog.sh every 5 minutes.

LOG=/home/james/logs/watchdog_daemon.log
mkdir -p /home/james/logs

echo "[$(date)] Daemon started (pid $$)" >> "$LOG"

while true; do
    bash /home/james/watchdog.sh 2>> "$LOG"
    sleep 300
done

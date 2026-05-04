#!/bin/bash
# Ensure cron is running (WSL may have restarted, killing it).
# Triggered hourly by a Windows scheduled task.
service cron status >/dev/null 2>&1 || service cron start

import json
import os
import time

LOG_FILE = "appeals.log"
JSON_FILE = "appeals.json"

if not os.path.exists(LOG_FILE):
    print("No appeals.log file found, skipping migration.")
    exit()

appeals = []

with open(LOG_FILE, "r") as f:
    for line in f:
        # Expected format: [Thu Jul 15 22:00:00 2025] IP: 1.2.3.4 — Appeal: appeal text here
        try:
            time_part = line[line.find("[")+1:line.find("]")]
            tstamp = time.mktime(time.strptime(time_part, "%a %b %d %H:%M:%S %Y"))
            ip_start = line.find("IP: ") + 4
            ip_end = line.find(" — Appeal:")
            ip = line[ip_start:ip_end].strip()
            appeal_text = line[line.find("Appeal:") + 7:].strip()

            appeals.append({
                "ip": ip,
                "text": appeal_text,
                "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(tstamp))
            })
        except Exception as e:
            print(f"Skipping line due to error: {e}")

with open(JSON_FILE, "w") as f:
    json.dump(appeals, f, indent=2)

print(f"Migrated {len(appeals)} appeals to {JSON_FILE}")

#!/usr/bin/env python3
"""
LeadRescuePro Daily Caller Report Generator
Run this at end of day to get a report of the caller's performance.
"""
import json, os, sys
from datetime import date, datetime
from pathlib import Path

DATA_FILE = os.path.expanduser("~/.lrp_caller_data.json")
REPORT_DIR = os.path.expanduser("~/leadrescuepro_ops/daily-reports")

RESULT_LABELS = {
    "loom": "🎥 Loom Requested",
    "interest": "💬 Interested",
    "callback": "⏰ Callback Set",
    "voicemail": "📞 Left Voicemail",
    "noanswer": "❌ No Answer",
    "dnc": "🚫 DNC / Not Interested"
}

def load_data():
    if not os.path.exists(DATA_FILE):
        print("No caller data found. Has anyone called today?")
        sys.exit(0)
    with open(DATA_FILE) as f:
        return json.load(f)

def generate_report(data):
    today_str = date.today().isoformat()
    log = data.get("call_log", [])
    caller = data.get("caller_name", "Caller")
    
    # Filter today's calls
    today_calls = [c for c in log if c.get("date", "").startswith(today_str)]
    
    if not today_calls:
        total_dials = len(log)
        if total_dials == 0:
            return None, "No calls logged at all."
        # Use all calls if they span a single day
        today_calls = log
    
    total_dials = len(today_calls)
    total_connects = len([c for c in today_calls if c.get("result") in ["loom", "interest", "callback", "voicemail"]])
    total_looms = len([c for c in today_calls if c.get("result") == "loom"])
    total_dnc = len([c for c in today_calls if c.get("result") == "dnc"])
    total_noanswer = len([c for c in today_calls if c.get("result") == "noanswer"])
    total_callback = len([c for c in today_calls if c.get("result") == "callback"])
    
    connect_rate = round((total_connects / total_dials * 100), 1) if total_dials > 0 else 0
    loom_rate = round((total_looms / total_connects * 100), 1) if total_connects > 0 else 0
    
    report = f"""
{'=' * 50}
📅 LEADRESCUEPRO DAILY CALLER REPORT
Date: {today_str}
Caller: {caller}
{'=' * 50}

📊 PERFORMANCE METRICS
  Total Dials:        {total_dials}
  Total Connects:     {total_connects} ({connect_rate}% connect rate)
  Looms Requested:    {total_looms} ({loom_rate}% of connects converted)
  Callbacks Set:      {total_callback}
  Voicemails:         {len([c for c in today_calls if c.get('result') == 'voicemail'])}
  No Answer:          {total_noanswer}
  DNC / Not Int.:     {total_dnc}

🎯 CONVERSION FUNNEL
  Dials ➡️ Connects:  {connect_rate}%
  Connects ➡️ Looms:  {loom_rate}%
  Overall ➡️ Looms:   {round((total_looms / total_dials * 100), 1) if total_dials > 0 else 0}%

📋 CALL LOG
"""
    for i, c in enumerate(today_calls, 1):
        label = RESULT_LABELS.get(c.get("result", ""), c.get("result", "?"))
        notes = c.get("notes", "")
        report += f"  {i:2d}. {c.get('time','')} | {c.get('business','?')} | {label}"
        if notes:
            report += f" — {notes[:80]}"
        report += "\n"
    
    report += f"\n{'=' * 50}\n"
    
    return report, today_str

def save_report(report, date_str):
    os.makedirs(REPORT_DIR, exist_ok=True)
    path = os.path.join(REPORT_DIR, f"caller-report-{date_str}.md")
    with open(path, "w") as f:
        f.write(report)
    return path

def main():
    data = load_data()
    report, date_str = generate_report(data)
    
    if report is None:
        print(f"📋 No calls logged for today ({date.today().isoformat()}).")
        print(f"   Message: {date_str}")
        return
    
    path = save_report(report, date_str or date.today().isoformat())
    print(report)
    print(f"Report saved to: {path}")

if __name__ == "__main__":
    main()

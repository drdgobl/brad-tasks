#!/usr/bin/env python3
"""
Sync Google Calendar events with task data.
Runs via GitHub Actions every 15 min.
Outputs tasks-data.json with calendar coverage status per task.
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta
from difflib import SequenceMatcher

import requests


def get_access_token():
    """Exchange refresh token for access token."""
    client_id = os.environ["GOOGLE_CLIENT_ID"]
    client_secret = os.environ["GOOGLE_CLIENT_SECRET"]
    refresh_token = os.environ["GOOGLE_REFRESH_TOKEN"]

    resp = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    })
    resp.raise_for_status()
    return resp.json()["access_token"]


def fetch_events(access_token, days=14):
    """Fetch calendar events for the next N days."""
    now = datetime.utcnow()
    time_min = now.isoformat() + "Z"
    time_max = (now + timedelta(days=days)).isoformat() + "Z"

    events = []
    page_token = None

    while True:
        params = {
            "timeMin": time_min,
            "timeMax": time_max,
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": 250,
        }
        if page_token:
            params["pageToken"] = page_token

        resp = requests.get(
            "https://www.googleapis.com/calendar/v3/calendars/primary/events",
            headers={"Authorization": f"Bearer {access_token}"},
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()
        events.extend(data.get("items", []))

        page_token = data.get("nextPageToken")
        if not page_token:
            break

    return events


def event_duration_hours(event):
    """Calculate event duration in hours."""
    start = event.get("start", {})
    end = event.get("end", {})

    if "dateTime" in start and "dateTime" in end:
        fmt = "%Y-%m-%dT%H:%M:%S"
        s = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
        e = datetime.fromisoformat(end["dateTime"].replace("Z", "+00:00"))
        return (e - s).total_seconds() / 3600
    return 0


def extract_keywords(text):
    """Extract meaningful keywords from text."""
    # Remove common prefixes/suffixes
    text = re.sub(r"^(CE|ECU|GL):\s*", "", text, flags=re.IGNORECASE)
    # Split on non-alpha and filter short words
    words = re.findall(r"[a-zA-Z]{3,}", text.lower())
    stop = {"the", "and", "for", "with", "from", "that", "this", "will", "have", "are", "was", "been"}
    return [w for w in words if w not in stop]


def match_score(task_title, event_title):
    """Score how well an event matches a task (0-1)."""
    task_kw = extract_keywords(task_title)
    event_kw = extract_keywords(event_title)

    if not task_kw or not event_kw:
        return 0

    # Keyword overlap
    task_set = set(task_kw)
    event_set = set(event_kw)
    overlap = len(task_set & event_set)
    max_possible = min(len(task_set), len(event_set))
    keyword_score = overlap / max_possible if max_possible else 0

    # Sequence similarity
    seq_score = SequenceMatcher(None, task_title.lower(), event_title.lower()).ratio()

    # Client prefix bonus (e.g., "Green Llama" in both)
    client_bonus = 0
    client_patterns = [
        "green llama", "ecu", "red gold", "pal's", "oolie",
        "le bleu", "sapphire", "mountain air", "butterball"
    ]
    for client in client_patterns:
        if client in task_title.lower() and client in event_title.lower():
            client_bonus = 0.3
            break

    return min(1.0, keyword_score * 0.4 + seq_score * 0.3 + client_bonus)


def parse_estimate_hours(est_str):
    """Parse estimate string like '2hr', '30min', '1.5hr' to hours."""
    if not est_str:
        return None
    est = est_str.strip().lower()
    if "hr" in est:
        try:
            return float(est.replace("hr", "").strip())
        except ValueError:
            return None
    if "min" in est:
        try:
            return float(est.replace("min", "").strip()) / 60
        except ValueError:
            return None
    return None


def parse_tasks_md(content):
    """Parse TASKS.md and return active tasks with titles and estimates."""
    tasks = []
    in_active = False

    for line in content.split("\n"):
        stripped = line.strip()
        if stripped == "## Active":
            in_active = True
            continue
        if stripped.startswith("## ") and in_active:
            break
        if not in_active or not stripped.startswith("- [ ]"):
            continue

        title_match = re.search(r"\*\*(.+?)\*\*", stripped)
        if not title_match:
            continue

        title = title_match.group(1)
        est = ""
        est_match = re.search(r"est:\s*([^\|]+)", stripped)
        if est_match:
            est = est_match.group(1).strip()

        tasks.append({"title": title, "est": est})

    return tasks


def main():
    # Read TASKS.md
    tasks_path = os.path.join(os.path.dirname(__file__), "..", "TASKS.md")
    with open(tasks_path) as f:
        tasks = parse_tasks_md(f.read())

    if not tasks:
        print("No active tasks found")
        sys.exit(0)

    # Fetch calendar events
    access_token = get_access_token()
    events = fetch_events(access_token)
    print(f"Fetched {len(events)} calendar events")

    # Match events to tasks
    result = {}
    match_threshold = 0.25

    for task in tasks:
        est_hours = parse_estimate_hours(task["est"])
        matched_events = []
        total_hours = 0

        for event in events:
            title = event.get("summary", "")
            score = match_score(task["title"], title)
            if score >= match_threshold:
                dur = event_duration_hours(event)
                matched_events.append({
                    "title": title,
                    "score": round(score, 2),
                    "hours": round(dur, 1),
                    "start": event.get("start", {}).get("dateTime", ""),
                })
                total_hours += dur

        # Determine status
        if est_hours is None:
            status = "gray"
            tooltip = "No estimate set"
        elif total_hours >= est_hours:
            status = "green"
            tooltip = f"{total_hours:.1f}hr blocked / {est_hours:.1f}hr estimated"
        elif total_hours > 0:
            status = "yellow"
            tooltip = f"{total_hours:.1f}hr blocked / {est_hours:.1f}hr estimated"
        else:
            status = "red"
            tooltip = f"No calendar time found ({est_hours:.1f}hr estimated)"

        result[task["title"]] = {
            "status": status,
            "tooltip": tooltip,
            "matched_hours": round(total_hours, 1),
            "estimated_hours": est_hours,
            "matched_events": matched_events[:5],  # Top 5
        }

    # Write output
    out_path = os.path.join(os.path.dirname(__file__), "..", "tasks-data.json")
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)

    print(f"Wrote calendar status for {len(result)} tasks")
    for title, info in result.items():
        print(f"  [{info['status']}] {title}")


if __name__ == "__main__":
    main()

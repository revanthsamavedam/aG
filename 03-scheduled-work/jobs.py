"""jobs.py — what a scheduled job is, and how to load jobs.json.

A Job is just a task for the team, plus how often it should run:

    name              unique id, used on the command line and in the log
    task              what the team should do (the prompt handed to Team.run)
    interval_seconds  how often it runs — the runner decides due-ness from
                      this, not from the cron string
    cron              optional, display only: the crontab line this job
                      *would* have, so a human can see the intent at a
                      glance. No cron parser is built — the runner is
                      interval-based, crontab just calls it (see README).
    description       optional one-liner for --list

jobs.json is the file of record: {"jobs": [ {...}, {...} ]}.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass


@dataclass
class Job:
    name: str
    task: str
    interval_seconds: int
    cron: str = ""
    description: str = ""

    def is_due(self, last_run: float | None, now: float | None = None) -> bool:
        """Due when it has never run, or now - last_run >= interval."""
        now = time.time() if now is None else now
        if last_run is None:
            return True
        return now - last_run >= self.interval_seconds

    def next_due(self, last_run: float | None) -> float:
        """Epoch seconds of the next run. Never-run jobs are due now."""
        if last_run is None:
            return time.time()
        return last_run + self.interval_seconds


def load_jobs(path: str) -> list[Job]:
    """Read jobs.json -> [Job, ...]. Unknown keys are ignored; a job
    missing name/task/interval_seconds raises a clear error."""
    with open(path) as f:
        data = json.load(f)
    jobs = []
    for entry in data.get("jobs", []):
        for field in ("name", "task", "interval_seconds"):
            if field not in entry:
                raise ValueError(f"job in {path} is missing {field!r}: {entry}")
        jobs.append(Job(
            name=entry["name"],
            task=entry["task"],
            interval_seconds=int(entry["interval_seconds"]),
            cron=entry.get("cron", ""),
            description=entry.get("description", ""),
        ))
    return jobs

"""When the bot runs. This is the single source of truth: the workflow's gate step reads it, and
run.py publishes it inside forecast.json so the website's countdown uses the same numbers.

GitHub's scheduler is best-effort, and a plain "every 4 hours" cron was in practice running about
every 6 hours (median gap) and sometimes went 16 hours without firing. So the workflow now wakes
every hour, and each wake-up only scrapes when the newest snapshot is old enough. One dropped
trigger then costs an hour, not a whole cycle.
"""

CHECK_MINUTE = 7        # the workflow wakes at this minute past every UTC hour (":37" in IST)
MIN_GAP_MINUTES = 210   # ...and only runs if the newest snapshot is at least this old (about 4 h per cycle)
START_LAG_MINUTES = 10  # typical delay between that minute and GitHub actually starting the run
PROCESS_SECONDS = 120   # scrape + forecast + publish, from run start until the site serves it


def as_dict():
    return {
        "check_minute": CHECK_MINUTE,
        "min_gap_minutes": MIN_GAP_MINUTES,
        "start_lag_minutes": START_LAG_MINUTES,
        "process_seconds": PROCESS_SECONDS,
    }

#!/bin/sh
# Root-owned helper. Schedules only this service's wake events, never sleeps,
# shuts down, unlocks, or cancels another application's events.
set -eu
owner=local.tado-history.wake
now=$(/bin/date +%s)
scheduled=$(/usr/bin/pmset -g sched)
for offset in 0 1 2; do
    day=$(/bin/date -v+"${offset}"d +%m/%d/%y)
    long_day=$(/bin/date -v+"${offset}"d +%m/%d/%Y)
    # Wake ten seconds before the launchd timer. A longer gap could allow
    # an idle-sleep timer to suspend it again first.
    for clock in 06:04:50 12:04:50 18:04:50 23:54:50 23:56:50 23:58:50; do
        event="$day $clock"
        epoch=$(/bin/date -j -f '%m/%d/%y %H:%M:%S' "$event" +%s)
        [ "$epoch" -gt "$now" ] || continue
        if /usr/bin/printf '%s\n' "$scheduled" | /usr/bin/awk -v event="$event" -v long_event="$long_day $clock" -v owner="$owner" '(index($0,event) || index($0,long_event)) && index($0,owner) { found=1 } END { exit !found }'; then
            continue
        fi
        /usr/bin/pmset schedule wake "$event" "$owner"
    done
done

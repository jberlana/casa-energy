"""Shared local quota accounting. Reads never contact Tado or assume a midnight reset."""
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import json
import math
import re
from zoneinfo import ZoneInfo

DAY = 86400


def epoch(value):
    if not value: return 0
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def read(db, key, default=None):
    row = db.execute('SELECT value FROM state WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else default


def write(db, key, value):
    db.execute('INSERT OR REPLACE INTO state VALUES (?,?)', (key, json.dumps(value)))


def usage(db, now):
    buckets = read(db, 'quota_usage')
    if buckets is None:
        # Legacy counters have only UTC dates. Place counts at each day's latest possible
        # instant so upgrading cannot accidentally grant a fresh allowance.
        since = datetime.fromtimestamp(now-DAY, timezone.utc).date().isoformat()
        buckets = []
        for day, count in db.execute('SELECT day,count FROM request_counts WHERE day>=?', (since,)):
            end = datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp()+DAY-1
            buckets.append([int(min(now, end)//60)*60, count])
    return [[at, count] for at, count in buckets if at+60 > now-DAY]


def policy(value):
    match = re.search(r'"perday"\s*;\s*q=(\d+)\s*;\s*w=(\d+)', value or '', re.I)
    if match and 0 < int(match[1]) <= 1_000_000 and 0 < int(match[2]) <= DAY:
        return int(match[1]), int(match[2])
    return None


def observe(db, headers, status, now):
    headers = {k.lower(): v for k, v in headers.items()}
    raw = headers.get('ratelimit', '')
    daily = re.search(r'(?:"perday"|perday)\s*;[^,]+', raw, re.I)
    value = daily[0] if daily else ''
    remaining = re.search(r'(?:^|;)\s*r=(\d+)', value)
    seconds = re.search(r'(?:^|;)\s*t=(\d+)', value or (raw if status == 429 else ''))
    reset = now+int(seconds[1]) if seconds else None
    retry = headers.get('retry-after', '')
    if status == 429 and retry:
        try:
            after = now+int(retry) if retry.isdigit() else parsedate_to_datetime(retry).timestamp()
            reset = max(reset or now, after)
        except (ValueError, TypeError, OverflowError): pass
    old = read(db, 'server_quota', {})
    if remaining:
        current_policy = headers.get('ratelimit-policy') or old.get('policy')
        if reset is None and current_policy == old.get('policy') and (old.get('reset_at') or 0) > now:
            reset = old['reset_at']
        policy_seen = now if headers.get('ratelimit-policy') else old.get('policy_seen_at', epoch(old.get('observed_at')))
        write(db, 'server_quota', {'policy_seen_at': policy_seen, 'remaining': int(remaining[1]), 'policy': current_policy,
              'observed_at': datetime.fromtimestamp(now, timezone.utc).isoformat(), 'reset_at': reset,
              'sequence': read(db, 'quota_sequence', 0)})
    if status == 429:
        # Without a server deadline, retry conservatively after a full day, not midnight.
        write(db, 'api_blocked_until', datetime.fromtimestamp(max(now+120, reset or now+DAY), timezone.utc).isoformat())


def pending_slots(db, cfg, now, reset=None):
    zone = ZoneInfo(cfg.get('timezone', 'Europe/Madrid'))
    current = datetime.fromtimestamp(now, zone)
    end = datetime.fromtimestamp(reset, zone) if reset and reset > now else current.replace(hour=23, minute=59, second=59)
    end = datetime.fromtimestamp(min(end.timestamp(), now+DAY), zone)
    pending = []
    slots = cfg.get('slots', ['06:05', '12:05', '18:05', '23:55'])
    day = current.date()
    while day <= end.date():
        for slot in sorted(set(slots)):
            hour, minute = map(int, slot.split(':'))
            due = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone).timestamp()
            if not now <= due <= end.timestamp(): continue
            row = db.execute('SELECT completed_at FROM runs WHERE day=? AND slot=?', (day.isoformat(), slot)).fetchone()
            if not row or not row[0]: pending.append(due)
        day += timedelta(days=1)
    # Include the latest missed slot if it is still eligible for catch-up/retry.
    eligible = [s for s in slots if s <= current.strftime('%H:%M')]
    if eligible:
        slot = max(eligible)
        row = db.execute('SELECT completed_at,attempts FROM runs WHERE day=? AND slot=?', (current.date().isoformat(), slot)).fetchone()
        if (not row or (not row[0] and row[1] < cfg.get('max_attempts_per_slot', 3))) and now not in pending:
            pending.append(now)
    return pending


def budget(db, cfg, now, needed=1, scheduled=False):
    buckets = usage(db, now)
    used = sum(count for _, count in buckets)
    server = read(db, 'server_quota', {})
    parsed = policy(server.get('policy'))
    observed = epoch(server.get('observed_at'))
    reset = server.get('reset_at')
    valid = bool(parsed and 0 <= now-server.get('policy_seen_at', observed) < parsed[1] and 0 <= now-observed < parsed[1] and (not reset or now < reset))
    limit, window = parsed if valid else (100, DAY)
    reserve = max(int(cfg.get('server_quota_reserve', 20)), math.ceil(limit*.2))
    adaptive = cfg.get('adaptive_quota', True)
    allowance = max(0, limit-reserve) if valid and adaptive else min(max(0, limit-reserve), cfg.get('daily_request_limit', 80))
    sequence = read(db, 'quota_sequence', 0)
    remaining = None
    # Old observations without a policy cannot raise the allowance, but still protect low headroom.
    fresh_remaining = isinstance(server.get('remaining'), int) and (not observed or 0 <= now-observed < DAY) and (not reset or now < reset)
    if fresh_remaining:
        delta = max(0, sequence-server['sequence']) if 'sequence' in server else used
        remaining = max(0, server['remaining']-delta)
    rooms = db.execute('SELECT count(*) FROM rooms WHERE active=1').fetchone()[0]
    slots = pending_slots(db, cfg, now, reset if valid else None) if scheduled else []
    planned = len(slots)*(max(1, rooms)+3)
    blocked = epoch(read(db, 'api_blocked_until'))
    retry = None
    reason = None
    if blocked > now:
        reason, retry = 'server_quota', blocked
    elif remaining is not None and remaining < needed+reserve:
        reason = 'server_quota' if remaining < needed else 'safety_reserve'
        retry = reset if reset and reset > now else (observed or now)+window
    elif used+needed > allowance:
        reason = 'local_budget'
        released = used
        for at, count in sorted(buckets):
            released -= count
            if released+needed <= allowance:
                retry = at+60+DAY
                break
    elif used+needed+planned > allowance or (remaining is not None and remaining < needed+reserve+planned):
        reason = 'scheduled_reserve'
        # A future collection may consume its reservation; reevaluate then, never promise a reset.
        retry = min((s for s in slots if s > now), default=None)
    return {'allowed': reason is None, 'reason': reason, 'retryAt': int(retry*1000) if retry else None,
            'limit': limit, 'budget': allowance, 'used': used, 'remaining': remaining,
            'reserve': reserve, 'scheduled': planned, 'observedAt': int(observed*1000) if observed else None,
            'resetAt': int(reset*1000) if valid and reset else None, 'adaptive': valid and adaptive}


def reserve_request(db, cfg, now):
    decision = budget(db, cfg, now, scheduled=cfg.get('reserve_scheduled', False))
    if not decision['allowed']: raise ValueError(decision['reason'])
    buckets = usage(db, now)
    minute = int(now//60)*60
    if buckets and buckets[-1][0] == minute: buckets[-1][1] += 1
    else: buckets.append([minute, 1])
    with db:
        write(db, 'quota_usage', buckets)
        write(db, 'quota_sequence', read(db, 'quota_sequence', 0)+1)
        day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
        db.execute('INSERT INTO request_counts VALUES (?,1) ON CONFLICT(day) DO UPDATE SET count=count+1', (day,))

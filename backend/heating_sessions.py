"""Pure session analysis. Demand is not burner runtime or metered energy."""
from bisect import bisect_left, bisect_right
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

MINUTE = 60_000
DAY = 86_400_000
HEAT = {'LOW', 'MEDIUM', 'HIGH', 'HEATING'}
TZ = ZoneInfo('Europe/Madrid')
DEMO_PRICE_PER_KWH = .08


def simulated_energy(duration_ms, source):
    if source != 'demo':
        return None, None
    hours = duration_ms / 3_600_000
    energy = {'low': hours * 8, 'high': hours * 12}
    cost = {bound: kwh * DEMO_PRICE_PER_KWH for bound, kwh in energy.items()}
    return energy, cost


def analyse(rooms, selected, start, end, context_start, context_end, source):
    # Sweep normalized state intervals. Unknown/disconnected rooms cannot prove a house-wide stop.
    changes = {}
    def add(t, value):
        changes.setdefault(t, []).append(value)
    for rid, room in rooms.items():
        for index, e in enumerate(room['events']):
            if e['series'] not in ('callForHeat', 'connected'):
                continue
            a, b = max(context_start, e['start']), min(context_end, e['end'])
            if a >= b:
                continue
            key = (rid, e['series'], index)
            add(a, (key, e['value']))
            add(b, (key, None))
    changes.setdefault(context_start, [])
    changes.setdefault(context_end, [])
    points = sorted(changes)
    state, spans = {}, []
    for t, following in zip(points, points[1:]):
        for key, value in changes[t]:
            if value is None:
                state.pop(key, None)
            else:
                state[key] = value
        demands = {key[0]: value for key, value in state.items() if key[1] == 'callForHeat'}
        offline = {key[0] for key, value in state.items() if key[1] == 'connected' and value is False}
        active = sorted(rid for rid, value in demands.items() if value in HEAT and rid not in offline)
        cold = bool(rooms) and all(demands.get(rid) == 'NONE' and rid not in offline for rid in rooms)
        spans.append({'start': t, 'end': following, 'active': active, 'cold': cold})
    sessions, current = [], None
    for i, span in enumerate(spans):
        if span['active']:
            if current is None:
                current = {'start': span['start'], 'end': span['end'], 'roomMs': {},
                           'partialStart': not (i and spans[i-1]['cold'])}
            current['end'] = span['end']
            for rid in span['active']:
                current['roomMs'][rid] = current['roomMs'].get(rid, 0) + span['end']-span['start']
        elif current:
            current['partialEnd'] = not span['cold']
            sessions.append(current)
            current = None
    if current:
        current['partialEnd'] = True
        sessions.append(current)

    samples = rooms[selected]['samples']
    times = [s[0] for s in samples]
    max_gap = (2 if source == 'demo' else 25) * MINUTE
    visible = []
    for s in sessions:
        if s['end'] <= start or s['start'] >= end:
            continue
        rows = samples[bisect_left(times, s['start']):bisect_right(times, s['end'])]
        valid = [r for r in rows if r[1] is not None]
        complete = (len(valid) >= 2 and valid[0][0]-s['start'] <= max_gap
                    and s['end']-valid[-1][0] <= max_gap
                    and all(b[0]-a[0] <= max_gap for a,b in zip(valid,valid[1:])))
        disconnected = any(e['series'] == 'connected' and e['value'] is False
                           and e['start'] < s['end'] and e['end'] > s['start']
                           for e in rooms[selected]['events'])
        complete = complete and not disconnected
        response = {'first': valid[0][:2] if valid else None, 'last': valid[-1][:2] if valid else None,
                    'delta': round(valid[-1][1]-valid[0][1], 2) if complete else None,
                    'complete': complete}
        duration = s['end']-s['start']
        # Illustrative gas input power and unit price; never fitted to real household invoices.
        energy, cost = simulated_energy(duration, source)
        visible.append({**s, 'id': str(s['start']), 'durationMs': duration,
                        'visibleMs': min(end,s['end'])-max(start,s['start']),
                        'rooms': [{'id': rid, 'name': rooms[rid]['name'], 'demandMs': value}
                                  for rid,value in s['roomMs'].items()],
                        'response': response, 'energyKwh': energy, 'cost': cost})
        del visible[-1]['roomMs']

    # Calendar days, not fixed 24h bins (Europe/Madrid DST days can be 23/25 hours).
    days = []
    day = datetime.fromtimestamp(start/1000, TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    while day.timestamp()*1000 < end:
        next_day = day + timedelta(days=1)
        a, b = max(start, int(day.timestamp()*1000)), min(end, int(next_day.timestamp()*1000))
        demand = sum(max(0, min(b,s['end'])-max(a,s['start'])) for s in visible)
        known = sum(max(0, min(b,s['end'])-max(a,s['start'])) for s in spans if s['active'] or s['cold'])
        energy, cost = simulated_energy(demand, source)
        days.append({'start': a, 'end': b, 'demandMs': demand, 'knownMs': known,
                     'energyKwh': energy, 'cost': cost})
        day = next_day
    known = []
    for s in spans:
        a,b=max(start,s['start']),min(end,s['end'])
        if a < b and (s['active'] or s['cold']):
            if known and known[-1][1] == a:
                known[-1][1] = b
            else:
                known.append([a,b])
    return {'source': source, 'room': selected, 'start': start, 'end': end,
            'sessions': visible, 'days': days, 'known': known,
            'costModel': 'illustration' if source == 'demo' else 'uncalibrated'}

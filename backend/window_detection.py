"""Explainable ventilation candidates. Pure calculations; never commands heating."""
from bisect import bisect_left, bisect_right
import math
from statistics import median

MINUTE = 60_000
DAY = 1440 * MINUTE
VERSION = "ventilation-1"
DEFAULT_DROP = .7
MAX_GAP = 20 * MINUTE
HEAT = {"LOW", "MEDIUM", "HIGH", "HEATING"}


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def absolute_humidity(temperature, humidity):
    if not finite(temperature) or not finite(humidity) or not -50 <= temperature <= 60 or not 0 <= humidity <= 100:
        return None
    return 216.7 * ((humidity / 100) * 6.112 * math.exp(17.62 * temperature / (243.12 + temperature))) / (273.15 + temperature)


class Context:
    def __init__(self, events):
        self.rows = {}
        for event in sorted(events, key=lambda e: e['start']):
            self.rows.setdefault(event['series'], []).append(event)
        self.times = {k: [e['start'] for e in rows] for k, rows in self.rows.items()}

    def at(self, series, timestamp):
        rows = self.rows.get(series, [])
        i = bisect_right(self.times.get(series, []), timestamp) - 1
        return rows[i]['value'] if i >= 0 and rows[i]['end'] > timestamp else None

    def connected(self, start, end):
        rows = self.rows.get('connected', [])
        i = max(0, bisect_right(self.times.get('connected', []), start) - 1)
        covered = start
        for row in rows[i:]:
            if row['start'] > covered or row['value'] is not True:
                break
            covered = max(covered, row['end'])
            if covered >= end:
                return True
        return False


def room_threshold(labels):
    """Only the first two confirmed positives on distinct days train; later cases are holdouts."""
    positives = sorted((e for e in labels if e['label'] == 'yes' and finite(e['event'].get('dropC')) and e['event']['dropC'] > 0),
                       key=lambda e: e['created'])
    training = []
    days = set()
    for label in positives:
        day = label['event']['start'] // DAY
        if day not in days and len(training) < 2:
            days.add(day)
            training.append(label)
    if len(training) < 2:
        return DEFAULT_DROP, []
    # Small, deliberately bounded adaptation. This is sensitivity, not a probability of opening.
    return round(max(.35, min(1.2, median(e['event']['dropC'] for e in training) * .7)), 2), [e['id'] for e in training]


def detection(room, samples, intervals, start, end, *, snapshots=(), peers=(), outdoor=(), labels=(), confirmed=()):
    annotations = {e['id']: e for e in [*confirmed, *labels]}
    threshold, training = room_threshold(annotations.values())
    context = Context(intervals)
    missing = sorted(s[0] for s in samples if not finite(s[1]))
    rows = sorted((s for s in samples if finite(s[1])), key=lambda s: s[0])
    times = [s[0] for s in rows]
    peer_indexes = [(p, [s[0] for s in p]) for p in peers]
    weather = sorted((s for s in outdoor if finite(s[1])), key=lambda s: s[0])
    weather_times = [s[0] for s in weather]
    candidates = []
    reference = {True: [], False: [], None: []}
    reference_times = {True: [], False: [], None: []}
    segment = 0
    last_candidate_end = -1
    for i, current in enumerate(rows):
        if not i:
            continue
        previous = rows[i - 1]
        delta = current[0] - previous[0]
        if (delta <= 0 or delta > MAX_GAP or not context.connected(previous[0], current[0])
                or bisect_right(missing, previous[0]) != bisect_left(missing, current[0])):
            segment = i
            continue
        heat_value = context.at('callForHeat', previous[0])
        heating = True if heat_value in HEAT else False if heat_value == 'NONE' else None
        slope = (previous[1] - current[1]) * MINUTE / delta
        lo = max(segment, bisect_left(times, current[0] - 30 * MINUTE))
        first = max(rows[lo:i], key=lambda s: (s[1], s[0])) if lo < i else previous
        drop = first[1] - current[1]
        # A single down/up sensor spike is not enough. Wait for a subsequent independent sample.
        following = rows[i + 1] if i + 1 < len(rows) else None
        sustained = (following and 0 < following[0] - current[0] <= MAX_GAP
                     and bisect_right(missing, current[0]) == bisect_left(missing, following[0])
                     and context.connected(current[0], following[0]) and following[1] <= first[1] - drop / 2)
        if drop >= threshold and sustained and current[0] > last_candidate_end:
            duration = current[0] - first[0]
            first_index = bisect_left(times, first[0])
            onset_end = rows[first_index + 1][0]
            reasons = [f"Descenso de {drop:.2f} °C en {duration / MINUTE:.0f} min"]
            alternatives = []
            prior_times = reference_times[heating]
            a = bisect_left(prior_times, first[0] - 30 * DAY)
            b = bisect_left(prior_times, first[0])
            reference_values = reference[heating][a:b]
            reference_count = len(reference_values)
            if reference_count >= 30:
                ordered = sorted(reference_values)
                unusual = drop * MINUTE / duration > max(.005, ordered[int(.95 * (len(ordered) - 1))])
                (reasons if unusual else alternatives).append('Más rápido que el enfriamiento habitual' if unusual else 'Similar a otros enfriamientos de la habitación')
            else:
                alternatives.append('Referencia de la habitación todavía insuficiente')
            relevant = [e for e in intervals if first[0] - 20 * MINUTE <= e['start'] <= current[0]]
            if any(e['series'] == 'settings' for e in relevant):
                alternatives.append('Coincide con un cambio de consigna')
            if any(e['series'] == 'callForHeat' and e['value'] == 'NONE' and context.at('callForHeat', e['start'] - 1) in HEAT for e in relevant):
                alternatives.append('La demanda acababa de terminar')
            if heating is None:
                alternatives.append('Estado de demanda desconocido')
            peer_drops = []
            for peer, ts in peer_indexes:
                x, y = bisect_right(ts, first[0]) - 1, bisect_right(ts, current[0]) - 1
                if x >= 0 and y > x and first[0] - ts[x] <= MAX_GAP and current[0] - ts[y] <= MAX_GAP:
                    part = peer[x:y + 1]
                    if all(finite(s[1]) for s in part) and all(b[0] - a[0] <= MAX_GAP for a, b in zip(part, part[1:])):
                        peer_drops.append(peer[x][1] - peer[y][1])
            if peer_drops and median(peer_drops) >= drop * .6:
                alternatives.append('Otras habitaciones se enfrían al mismo tiempo')
            ah_before, ah_after = absolute_humidity(first[1], first[2]), absolute_humidity(current[1], current[2])
            ah_delta = round(ah_after - ah_before, 2) if ah_before is not None and ah_after is not None else None
            if ah_delta is not None and abs(ah_delta) >= .4:
                reasons.append('Cambio simultáneo de humedad absoluta')
            wi = bisect_right(weather_times, current[0]) - 1
            outside = weather[wi][1] if wi >= 0 and current[0] - weather[wi][0] <= 90 * MINUTE else None
            if outside is not None and outside >= first[1] - 1:
                alternatives.append('Poca diferencia entre temperatura interior y exterior')
            event = {'id': f'{room}:{first[0]}', 'start': first[0], 'end': current[0],
                     'onset': [first[0], onset_end], 'dropC': round(drop, 2), 'demand': heating,
                     'absoluteHumidityChange': ah_delta, 'outsideC': outside, 'origins': ['thermal'],
                     'reasons': reasons, 'alternatives': alternatives, 'referenceCount': reference_count,
                     'provisional': True, 'label': None, 'version': VERSION}
            last = candidates[-1] if candidates else None
            between = rows[bisect_left(times, last['end']):i + 1] if last else []
            low, recovered = math.inf, False
            for point in between:
                low = min(low, point[1])
                recovered = recovered or point[1] - low >= .3
            if (last and first[0] <= last['end'] + 30 * MINUTE and not recovered
                    and context.connected(last['end'], current[0])
                    and all(y[0] - x[0] <= MAX_GAP for x, y in zip(between, between[1:]))
                    and bisect_right(missing, last['end']) == bisect_left(missing, current[0])):
                last['end'] = current[0]
                original = rows[bisect_left(times, last['start'])]
                last['dropC'] = round(original[1] - current[1], 2)
                last['reasons'][0] = f"Descenso de {last['dropC']:.2f} °C en {(current[0] - last['start']) / MINUTE:.0f} min"
            else:
                candidates.append(event)
            # One bounded cooling episode, not one marker for every rolling window.
            last_candidate_end = current[0] + 30 * MINUTE
        if slope >= 0:
            reference[heating].append(slope)
            reference_times[heating].append(current[0])

    # Presence of the object is the provider signal; 'activated' means shut-off, not detection.
    # Do not guess expiry units or infer a physical closing time from a null/expired notification.
    for snapshot in sorted(snapshots, key=lambda s: s['at']):
        value = snapshot.get('openWindow')
        if not isinstance(value, dict) or not isinstance(value.get('activated'), bool):
            continue
        at = snapshot['at']
        match = next((e for e in candidates if e['start'] - 20 * MINUTE <= at <= e['end'] + 20 * MINUTE), None)
        if match:
            if 'tado' not in match['origins']:
                match['origins'].append('tado')
                match['reasons'].append('Tado comunicó una detección durante este tramo')
            match['tadoShutoff'] = value.get('activated') if isinstance(value.get('activated'), bool) else None
        else:
            candidates.append({'id': f'{room}:tado:{at}', 'start': at, 'end': at,
                               'onset': [at, at], 'dropC': None, 'demand': None, 'outsideC': None,
                               'absoluteHumidityChange': None, 'origins': ['tado'], 'label': None,
                               'reasons': ['Aviso de Tado observado en este momento'],
                               'alternatives': ['Inicio y duración de la apertura desconocidos'],
                               'tadoShutoff': value.get('activated') if isinstance(value.get('activated'), bool) else None,
                               'referenceCount': 0, 'provisional': True, 'version': VERSION})

    # Human annotations are durable evidence, even if a new detector no longer emits the event.
    for annotation in [*confirmed, *labels]:
        saved = annotation['event']
        match = next((e for e in candidates if e['id'] == annotation['id'] or
                      (e['start'] <= saved['end'] and e['end'] >= saved['start'])), None)
        if match is None:
            match = dict(saved)
            candidates.append(match)
        match['id'] = annotation['id']
        match['label'] = annotation['label']
        match['version'] = VERSION
        match['provisional'] = annotation['label'] != 'yes'
        if annotation.get('experiment') and 'guided' not in match['origins']:
            match['origins'] = [*match['origins'], 'guided']
        if annotation.get('experiment'):
            match['confirmedWindow'] = [saved['start'], saved['end']]
    reviewed = {e['id'] for e in [*confirmed, *labels]}
    return {'events': sorted((e for e in candidates if e['start'] <= end and e['end'] >= start), key=lambda e: e['start']),
            'thresholdC': threshold, 'trainingIds': training, 'version': VERSION,
            'reviewedCount': len(reviewed), 'askForFeedback': len(reviewed) < 3,
            'precision': 'experimental'}


def guided_annotations(experiments, samples, intervals=()):
    result = []
    context = Context(intervals)
    for trial in experiments:
        if not trial['opened'] or not trial['closed']:
            continue
        # Only original historical resolution is used for learning historical sensitivity.
        before = [s for s in samples if trial['opened'] - MAX_GAP <= s[0] <= trial['opened'] and finite(s[1])]
        after = [s for s in samples if trial['opened'] < s[0] <= trial['closed'] + MAX_GAP and finite(s[1])]
        usable = False
        if before and after:
            portion = [s for s in samples if before[-1][0] <= s[0] <= after[-1][0]]
            usable = (all(finite(s[1]) for s in portion)
                      and all(y[0] - x[0] <= MAX_GAP for x, y in zip(portion, portion[1:]))
                      and context.connected(before[-1][0], after[-1][0]))
        drop = round(max(0, before[-1][1] - min(s[1] for s in after)), 2) if usable and not trial['confounded'] else None
        event = {'id': 'guided:' + trial['id'], 'start': trial['opened'], 'end': trial['closed'],
                 'onset': [trial['opened'], trial['opened']], 'dropC': drop, 'demand': None,
                 'absoluteHumidityChange': None, 'outsideC': None, 'origins': ['guided'],
                 'reasons': ['Apertura y cierre anotados durante una prueba guiada'],
                 'alternatives': ['La prueba tuvo otros cambios; no se usa para ajustar el detector'] if trial['confounded'] else [],
                 'referenceCount': 0, 'provisional': False, 'version': VERSION}
        result.append({'id': event['id'], 'event': event, 'label': 'yes', 'created': trial['opened'], 'experiment': True})
    return result

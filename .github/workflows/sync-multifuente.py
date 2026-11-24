#!/usr/bin/env python3
"""APOSTARDO 5.2: importación pública y conservadora de TheSportsDB.

Solo estadísticas descriptivas de eventos; jamás genera pronósticos. Nunca borra el
archivo histórico cuando un proveedor falla. La API gratuita es PARCIAL y no es
un feed de resultados en vivo. Para ejecutar: python scripts/sync-multifuente.py
"""
from __future__ import annotations
import datetime as dt
import json
import os
import re
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

BASE = 'https://www.thesportsdb.com/api/v1/json'
KEY = os.getenv('THESPORTSDB_KEY', '123').strip()  # 123 es la clave pública publicada por TheSportsDB
DELAY = max(float(os.getenv('THESPORTSDB_DELAY', '2.2')), 0.0)
FETCH_DAY = os.getenv('THESPORTSDB_DAY_CALLS', '1') != '0'
TODAY = dt.datetime.now(ZoneInfo('America/Santiago')).date()
NOW = dt.datetime.now(dt.timezone.utc).isoformat().replace('+00:00', 'Z')
DATA = Path('data')

# IDs confirmados en páginas de temporada de TheSportsDB. ATP y UFC se tratan
# como eventos de calendario, NO como conjuntos exhaustivos de partidos/peleas.
LEAGUES = (
    ('chile', '4627', 'football', 'Primera División de Chile', 'Chile', '2026'),
    ('argentina', '4406', 'football', 'Primera División Argentina', 'Argentina', '2026'),
    ('brasil', '4351', 'football', 'Brasileirão Série A', 'Brasil', '2026'),
    ('nba', '4387', 'nba', 'NBA', 'Estados Unidos', '2026-2027'),
    ('atp', '4464', 'tennis', 'ATP World Tour', 'Internacional', '2026'),
    ('ufc', '4443', 'mma', 'UFC', 'Internacional', '2026'),
)
DEST = {'football': 'football-southamerica.json', 'nba': 'nba.json',
        'tennis': 'tennis.json', 'mma': 'mma.json'}
FINISHED = {'FT', 'AET', 'PEN', 'AOT', 'AP', 'AWD', 'AW', 'WO', 'FINAL', 'FINISHED', 'ENDED'}
LIVE = {'1H', 'HT', '2H', 'ET', 'BT', 'P', 'PT', 'Q1', 'Q2', 'Q3', 'Q4', 'OT', 'S1', 'S2', 'S3', 'S4', 'S5'}
ABORTED = {'PST', 'POST', 'CANC', 'SUSP', 'INT', 'INTR', 'ABD'}
STAT_KEY = {
    'shots on goal': 'shots_on_target', 'total shots': 'shots',
    'corner kicks': 'corners', 'corners': 'corners',
    'fouls': 'fouls', 'yellow cards': 'yellow_cards',
    'red cards': 'red_cards', 'tackles': 'tackles',
}


def number(v):
    if isinstance(v, bool): return None
    try:
        n = int(str(v).strip())
    except (ValueError, TypeError):
        return None
    return n if n >= 0 else None


def iso_date(raw):
    """TSDB strTimestamp es UTC sin sufijo. Nunca inventar una hora ausente."""
    stamp = (raw.get('strTimestamp') or '').strip()
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}', stamp):
        try:
            dt.datetime.fromisoformat(stamp)
            return stamp + 'Z'
        except ValueError: pass
    day = (raw.get('dateEvent') or '').strip()
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
        try:
            dt.date.fromisoformat(day)
            return day  # Sin hora; se mostrará como hora N/D
        except ValueError: pass
    return None


def participants(row, kind):
    a = (row.get('strHomeTeam') or '').strip()
    b = (row.get('strAwayTeam') or '').strip()
    if a and b: return a, b
    title = (row.get('strEvent') or '').strip()
    # En lucha/tenis se aceptan SOLO participantes explícitos con 'vs'.
    if kind in ('mma', 'tennis') and ' vs ' in title.lower():
        x, y = re.split(r'\s+vs\.?\s+', title, maxsplit=1, flags=re.I)
        if kind == 'mma':
            x = re.sub(r'^UFC(?: Fight Night| on ESPN| on ABC)?(?: \d+)?\s*[:\-]?\s*', '', x, flags=re.I).strip()
        if x.strip() and y.strip(): return x.strip(), y.strip()
    return None


def normalized(row, kind):
    if not isinstance(row, dict) or not row.get('idEvent'): return None
    sides = participants(row, kind)
    date = iso_date(row)
    if not sides or not date: return None
    a, b = sides
    x, y = number(row.get('intHomeScore')), number(row.get('intAwayScore'))
    raw_status = str(row.get('strStatus') or '').strip().upper()
    if raw_status in ABORTED: status = raw_status
    elif raw_status in LIVE: status = 'LIVE'
    elif raw_status in FINISHED or (x is not None and y is not None and raw_status not in {'NS', 'TBD'}): status = 'FINISHED'
    else: status = 'SCHEDULED'
    league = str(row.get('strLeague') or 'Competición N/D').strip()
    url = f'https://www.thesportsdb.com/event/{str(row["idEvent"]).strip()}'
    e = {'id': 'tsdb-' + str(row['idEvent']).strip(), 'sport': kind,
         'name': (row.get('strEvent') or f'{a} vs {b}').strip(),
         'home': a, 'away': b, 'league': league, 'date': date,
         'status': status, 'score': {'home': x, 'away': y},
         'source': 'TheSportsDB', 'source_url': url,
         'source_event_id': str(row['idEvent']), 'updated_at': NOW,
         'coverage_notice': 'Muestra limitada de API pública; no implica cartelera completa'}
    # No falseamos rounds. Si consta, es metadato del proveedor.
    if row.get('strSeason'): e['season'] = str(row['strSeason'])
    if row.get('intRound') is not None: e['round'] = str(row['intRound'])
    return e


def http_json(endpoint, **params):
    uri = f'{BASE}/{KEY}/{endpoint}?{urlencode(params)}'
    request = Request(uri, headers={'Accept': 'application/json', 'User-Agent': 'APOSTARDO-Observatorio/5.2'})
    try:
        with urlopen(request, timeout=22) as response:
            out = json.load(response)
        if not isinstance(out, dict): raise ValueError('respuesta no es un objeto JSON')
        return out
    finally:
        if DELAY: time.sleep(DELAY)


def existing(path):
    if not path.exists(): return []
    try:
        d = json.loads(path.read_text(encoding='utf-8'))
        return d.get('events', []) if isinstance(d, dict) and isinstance(d.get('events'), list) else []
    except (OSError, ValueError):
        return []


def atomic(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(tmp, path)


def prefer(old, new):
    """Prefiere nuevos datos; conserva puntuación si el proveedor la omite después."""
    value = {**old, **new}
    if new.get('score', {}).get('home') is None and old.get('score', {}).get('home') is not None:
        value['score'] = old['score']
        if old.get('status') == 'FINISHED': value['status'] = 'FINISHED'
    if old.get('statistics') and not new.get('statistics'):
        value['statistics'] = old['statistics']
    return value


def stats_for_game(ev_id):
    raw = http_json('lookupeventstats.php', id=ev_id).get('eventstats') or []
    metrics = {}
    for rec in raw:
        if not isinstance(rec, dict): continue
        key = STAT_KEY.get(str(rec.get('strStat') or '').strip().lower())
        a, b = number(rec.get('intHome')), number(rec.get('intAway'))
        if key and a is not None and b is not None:
            metrics[key] = {'home': a, 'away': b}
    return metrics


def collect():
    buckets = {kind: {} for kind in DEST}
    for kind, filename in DEST.items():
        for x in existing(DATA / filename):
            if isinstance(x, dict) and x.get('id'): buckets[kind][str(x['id'])] = x
    tracking = {kind: {'requests': [], 'errors': [], 'received': 0, 'accepted': 0, 'new': 0}
                for kind in DEST}
    # Consultas moderadas: temporada + tres fechas por liga, máximo 24 llamadas
    # para 6 ligas (más hasta 2 consultas estadísticas de fútbol).
    dates = (TODAY - dt.timedelta(days=1), TODAY, TODAY + dt.timedelta(days=1))
    for label, lid, kind, label_name, country, season in LEAGUES:
        for endpoint, params in [('eventsseason.php', {'id': lid, 's': season})] + ([('eventsday.php', {'d': day.isoformat(), 'l': lid}) for day in dates] if FETCH_DAY else []):
            item = {'league': label_name, 'id': lid, 'endpoint': endpoint, 'params': params, 'result': 'error'}
            tracking[kind]['requests'].append(item)
            try:
                payload = http_json(endpoint, **params)
                raw_events = payload.get('events') or []
                if not isinstance(raw_events, list): raise ValueError('events[] inválido')
                accepted = 0
                for r in raw_events:
                    if not isinstance(r, dict) or str(r.get('idLeague')) != lid: continue
                    e = normalized(r, kind)
                    if not e: continue
                    e['country'] = country
                    e['source_league_id'] = lid
                    prev = buckets[kind].get(e['id'])
                    buckets[kind][e['id']] = prefer(prev, e) if prev else e
                    tracking[kind]['new'] += 0 if prev else 1
                    accepted += 1
                item.update({'result': 'ok', 'received': len(raw_events), 'accepted': accepted})
                tracking[kind]['received'] += len(raw_events)
                tracking[kind]['accepted'] += accepted
            except (HTTPError, URLError, TimeoutError, ValueError, OSError) as error:
                code = getattr(error, 'code', None)
                msg = f'HTTP {code}' if code else type(error).__name__
                item['error'] = msg
                tracking[kind]['errors'].append(f'{label}: {msg}')
        print(f'{label_name}: {len(buckets[kind])} registros conservados', flush=True)
    # Enriquecimiento limitado y con procedencia: solo 2 encuentros acabados
    # de fútbol que todavía no tengan estadísticas. Nunca inferir faltantes.
    options = sorted((e for e in buckets['football'].values() if e.get('status') == 'FINISHED' and not e.get('statistics')), key=lambda x: x.get('date', ''), reverse=True)
    for e in options[:2]:
        try:
            metrics = stats_for_game(e['source_event_id'])
            if metrics: e['statistics'] = metrics
        except (HTTPError, URLError, TimeoutError, ValueError, OSError) as error:
            tracking['football']['errors'].append(f'Estadísticas {e["id"]}: {type(error).__name__}')
    result = {'generated_at': NOW, 'source': 'TheSportsDB (API pública v1)',
              'source_url': 'https://www.thesportsdb.com/docs/api',
              'provider_key_type': 'public', 'coverage': 'parcial: resultados gratuitos limitados por consulta',
              'sports': {}, 'errors': []}
    for kind, filename in DEST.items():
        valid = sorted(buckets[kind].values(), key=lambda e: (str(e.get('date', '')), str(e.get('id', ''))))[-5000:]
        track = tracking[kind]
        status = ('parcial' if track['errors'] else 'operativo') if track['accepted'] else ('caché anterior' if valid else 'sin datos')
        result['sports'][kind] = {'status': status, 'stored': len(valid), 'new': track['new'],
                                  'received': track['received'], 'accepted': track['accepted'],
                                  'errors': track['errors'], 'requests': track['requests'],
                                  'last_attempt': NOW, 'limitations': 'API pública devuelve máximo 3 registros por día y 15 por temporada en cada consulta; cobertura incompleta'}
        result['errors'].extend(track['errors'])
        if valid:
            atomic(DATA / filename, {'generated_at': NOW, 'source': 'TheSportsDB',
                'source_url': 'https://www.thesportsdb.com/docs/api',
                'coverage': 'parcial', 'events': valid,
                'notes': ['Fuente complementaria; no reemplaza los archivos de fútbol actuales.',
                          'No se puede afirmar que la jornada esté completa.',
                          'Estadísticas ausentes no equivalen a cero.']})
        print(kind, status, 'guardados:', len(valid), 'nuevos:', track['new'], flush=True)
    atomic(DATA / 'multifuente-status.json', result)
    return result


if __name__ == '__main__':
    collect()

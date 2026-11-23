#!/usr/bin/env python3
"""APOSTARDO 4.4: actualización conservadora de resultados por competición.

Prueba 5 ligas europeas y las primeras divisiones de Chile, Argentina y Brasil.
Las ligas sudamericanas pueden estar fuera del plan de la API: una respuesta 403
se registra y NO se confunde con una competición sin partidos.
"""
import datetime as dt
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

TOKEN = os.environ.get('FOOTBALL_DATA_API_KEY', '').strip()
if not TOKEN:
    raise SystemExit('Falta FOOTBALL_DATA_API_KEY; se conserva data/football.json')

NOW = dt.datetime.now(dt.timezone.utc)
START = (NOW - dt.timedelta(days=90)).date().isoformat()
END = (NOW + dt.timedelta(days=14)).date().isoformat()
COMPETITIONS = {
    'PL': ('Premier League', 'Inglaterra'),
    'PD': ('LaLiga', 'España'),
    'BL1': ('Bundesliga', 'Alemania'),
    'SA': ('Serie A', 'Italia'),
    'FL1': ('Ligue 1', 'Francia'),
    'CPD': ('Primera División de Chile', 'Chile'),
    'ASL': ('Liga Profesional Argentina', 'Argentina'),
    'BSA': ('Brasileirão Série A', 'Brasil'),
}

path = Path('data/football.json')
previous = {}
if path.exists():
    try:
        previous = json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, OSError):
        print('Aviso: el JSON anterior no se pudo leer; no se usará como respaldo')

# Conservar históricos disponibles si una liga falla temporalmente.
old_events = [e for e in previous.get('events', []) if isinstance(e, dict) and e.get('id')]
by_id = {str(e['id']): e for e in old_events}
errors = []
coverage = {}
updated_competitions = set()

for code, (fallback, country) in COMPETITIONS.items():
    url = (f'https://api.football-data.org/v4/competitions/{code}/matches'
           f'?dateFrom={START}&dateTo={END}')
    req = Request(url, headers={
        'X-Auth-Token': TOKEN,
        'Accept': 'application/json',
        'User-Agent': 'APOSTARDO/4.4',
    })
    try:
        with urlopen(req, timeout=25) as response:
            data = json.load(response)
        if not isinstance(data.get('matches'), list):
            raise ValueError('La respuesta no tiene una lista de partidos')
    except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
        status = getattr(exc, 'code', None)
        detail = f'HTTP {status}' if status else type(exc).__name__
        errors.append(f'{code} ({country}): {detail}')
        coverage[code] = {'country': country, 'status': 'error', 'detail': detail}
        continue

    updated_competitions.add(code)
    count = 0
    for match in data['matches']:
        home = match.get('homeTeam') or {}
        away = match.get('awayTeam') or {}
        if not home.get('name') or not away.get('name') or not match.get('id'):
            continue
        comp = match.get('competition') or {}
        mid = f'football-{match["id"]}'
        by_id[mid] = {
            'id': mid,
            'sport': 'Fútbol',
            'name': f'{home["name"]} vs {away["name"]}',
            'league': comp.get('name') or fallback,
            'country': country,
            'competition_code': code,
            'date': match.get('utcDate'),
            'status': match.get('status'),
            'home': home['name'],
            'away': away['name'],
            'score': (match.get('score') or {}).get('fullTime'),
            'matchday': match.get('matchday'),
            'source': 'football-data.org',
            'source_url': 'https://www.football-data.org/',
            'updated_at': NOW.isoformat(),
            'quality': 'Datos básicos de una fuente; sin validación cruzada',
        }
        count += 1
    coverage[code] = {'country': country, 'status': 'ok', 'received': count}

if not updated_competitions:
    raise SystemExit('Ninguna competición respondió; archivo anterior intacto. ' + '; '.join(errors))

# Los resultados anteriores se conservan incluso fuera de los últimos 90 días.
# Solo descartamos registros repetidos identificados por su ID de proveedor.
events = sorted(by_id.values(), key=lambda e: (str(e.get('date') or ''), str(e.get('id'))))
out = {
    'generated_at': NOW.isoformat(),
    'source': 'football-data.org',
    'competitions_requested': list(COMPETITIONS),
    'coverage': coverage,
    'errors': errors,
    'events': events,
}
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print('Partidos acumulados:', len(events))
for code, info in coverage.items():
    print(f'{code} ({info["country"]}): {info["status"]} ' + str(info.get('received', info.get('detail', ''))))

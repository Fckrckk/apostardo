#!/usr/bin/env python3
"""Actualiza eventos de fútbol desde football-data.org; sin dependencias externas."""
import datetime as dt
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

TOKEN = os.environ.get('FOOTBALL_DATA_API_KEY', '').strip()
if not TOKEN:
    raise SystemExit('Falta FOOTBALL_DATA_API_KEY; no se modifica data/football.json')
now = dt.datetime.now(dt.timezone.utc)
start = (now - dt.timedelta(days=2)).date().isoformat()
end = (now + dt.timedelta(days=14)).date().isoformat()
competitions = ['PL', 'PD', 'BL1', 'SA', 'FL1']
items = []
errors = []
for code in competitions:
    url = f'https://api.football-data.org/v4/competitions/{code}/matches?dateFrom={start}&dateTo={end}'
    req = Request(url, headers={'X-Auth-Token': TOKEN, 'Accept': 'application/json', 'User-Agent': 'APOSTARDO/3.0'})
    try:
        with urlopen(req, timeout=25) as res:
            payload = json.load(res)
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        errors.append(f'{code}: {type(exc).__name__} ({getattr(exc, "code", "sin código")})')
        continue
    for match in payload.get('matches', []):
        home = match.get('homeTeam') or {}
        away = match.get('awayTeam') or {}
        if not home.get('name') or not away.get('name'):
            continue
        items.append({
            'id': f'football-{match["id"]}',
            'sport': 'Fútbol',
            'name': f'{home["name"]} vs {away["name"]}',
            'league': (match.get('competition') or {}).get('name') or code,
            'date': match.get('utcDate'),
            'status': match.get('status'),
            'home': home.get('name'),
            'away': away.get('name'),
            'score': (match.get('score') or {}).get('fullTime'),
            'matchday': match.get('matchday'),
            'source': 'football-data.org',
            'source_url': 'https://www.football-data.org/',
            'updated_at': now.isoformat(),
            'quality': 'Datos básicos de una fuente; sin validación cruzada',
        })
if not items:
    raise SystemExit('No se recibieron partidos. Se conserva el archivo anterior. ' + '; '.join(errors))
unique = {item['id']: item for item in items}
out = {'generated_at': now.isoformat(), 'source': 'football-data.org', 'competitions_requested': competitions, 'errors': errors, 'events': sorted(unique.values(), key=lambda x: x['date'] or '')}
path = Path('data/football.json')
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(f'Guardados {len(unique)} partidos. Avisos: {errors}')

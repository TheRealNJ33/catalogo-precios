"""Recuperación por página. No contiene selectores ni modifica las esperas del sitio."""
import argparse
import asyncio
import csv
import hashlib
import json
import os
import re
import signal
import sqlite3
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

ES = timezone(timedelta(hours=-6))
FIELDS = ['proveedor', 'nombre', 'sku', 'precio', 'fecha_consulta']


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def run_id():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]


def validate_id(value):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', value):
        raise ValueError('El identificador admite letras, números, guiones y guiones bajos (máximo 80).')
    return value


@contextmanager
def lock(path):
    """Bloqueo del sistema operativo; se libera incluso al morir el proceso."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b'0'); handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError('Ya existe otra ejecución activa: ' + str(path)) from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def atomic_json(path, value):
    temp = path.with_name(path.name + '.tmp')
    with temp.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush(); os.fsync(stream.fileno())
    os.replace(temp, path)


def normalize_rows(rows, supplier, url, page):
    valid, rejected, skus = [], [], set()
    for raw in rows:
        if not isinstance(raw, dict):
            rejected.append({'url': url, 'pagina': page, 'motivo': 'Registro no estructurado', 'datos': str(raw)})
            continue
        sku = str(raw.get('sku', raw.get('Codigo', '')) or '').strip()
        if sku:
            skus.add(sku)
        try:
            name = str(raw.get('nombre', raw.get('Nombre', '')) or '').strip()
            if not sku or not name:
                raise ValueError('Falta SKU o nombre')
            price = Decimal(str(raw.get('precio', raw.get('Precio'))))
            if not price.is_finite() or price < 0 or price >= Decimal('1000000000000'):
                raise ValueError('Precio fuera de rango')
            if price != price.quantize(Decimal('.01')):
                raise ValueError('Precio con más de dos decimales')
            date_text = str(raw.get('fecha_consulta', raw.get('Fecha', '')))
            day = None
            for fmt in ('%d-%m-%Y', '%Y-%m-%d', '%Y-%m-%d %H:%M:%S'):
                try:
                    day = datetime.strptime(date_text, fmt).strftime('%d-%m-%Y')
                    break
                except ValueError:
                    pass
            if day is None:
                raise ValueError('Falta fecha de consulta válida')
            row = dict(zip(FIELDS, (supplier, name, sku, format(price, '.2f'), day)))
            if any('\x00' in value for value in row.values()):
                raise ValueError('Caracteres nulos en el registro')
            valid.append(row)
        except (ValueError, InvalidOperation) as exc:
            rejected.append({'url': url, 'pagina': page, 'motivo': str(exc),
                             'datos': json.dumps({str(k): str(v) for k, v in raw.items()}, ensure_ascii=False)})
    return valid, rejected, skus


class State:
    def __init__(self, folder, supplier, urls, resume):
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / 'estado.sqlite'
        exists = path.exists()
        if exists and not resume:
            raise ValueError('Esta corrida ya existe. Use --reanudar o un identificador nuevo.')
        if resume and not exists:
            raise ValueError('No existe estado para reanudar esta corrida.')
        self.folder = folder
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute('pragma journal_mode=WAL')
        self.db.execute('pragma synchronous=FULL')
        self.db.executescript('''
            create table if not exists meta (key text primary key, value text not null);
            create table if not exists categories (
                url text primary key, idx integer, next_page integer default 1,
                status text default 'pendiente', reason text default '');
            create table if not exists pages (
                url text, page integer, payload text, rejected text, skus text,
                saved_at text, primary key(url,page));
        ''')
        manifest = json.dumps({'version': 1, 'supplier': supplier, 'urls': urls}, ensure_ascii=False)
        if exists:
            previous = self.db.execute("select value from meta where key='manifest'").fetchone()
            if not previous or previous[0] != manifest:
                self.db.close()
                raise ValueError('El proveedor o las URL difieren del estado guardado. Use la selección original.')
        else:
            with self.db:
                self.db.execute('insert into meta values (?,?)', ('manifest', manifest))
                self.db.execute('insert into meta values (?,?)', ('started_at', now()))
                self.db.executemany('insert into categories(url,idx) values (?,?)',
                                    [(u, i) for i, u in enumerate(urls, 1)])

    def close(self):
        self.db.close()

    def categories(self):
        return self.db.execute('select * from categories order by idx').fetchall()

    def seen(self, url):
        result = set()
        for row in self.db.execute('select skus from pages where url=?', (url,)):
            result.update(json.loads(row[0]))
        return result

    def mark(self, url, status, reason):
        with self.db:
            self.db.execute('update categories set status=?, reason=? where url=?', (status, reason, url))

    def page(self, url, page, valid, rejected, skus, done, reason):
        # Datos y posición se confirman juntos: nunca se avanza sin guardar.
        with self.db:
            self.db.execute('insert into pages values (?,?,?,?,?,?)',
                            (url, page, json.dumps(valid, ensure_ascii=False),
                             json.dumps(rejected, ensure_ascii=False), json.dumps(sorted(skus)), now()))
            self.db.execute('update categories set next_page=?, status=?, reason=? where url=?',
                            (page + 1, 'completa' if done else 'pendiente', reason, url))

    def export(self, supplier, interrupted=False, failure=None, xlsx=False):
        products, rejected = {}, []
        # Mantiene la primera aparición por SKU, igual que los scrapers originales.
        for page in self.db.execute('select p.* from pages p join categories c using(url) order by c.idx,p.page'):
            for row in json.loads(page['payload']):
                products.setdefault(row['sku'], row)
            rejected.extend(json.loads(page['rejected']))
        filename = 'productos_' + supplier.lower() + '.csv'
        csv_path = self.folder / filename
        write_csv(csv_path, FIELDS, list(products.values()))
        write_csv(self.folder / 'registros_por_revisar.csv', ['url', 'pagina', 'motivo', 'datos'], rejected)
        if xlsx:
            import pandas as pd
            temp = self.folder / ('temporal_' + supplier.lower() + '.xlsx')
            pd.DataFrame(products.values(), columns=FIELDS).to_excel(temp, index=False, engine='openpyxl')
            os.replace(temp, csv_path.with_suffix('.xlsx'))
        cats = [dict(r) for r in self.categories()]
        pending = [c for c in cats if c['status'] != 'completa']
        if interrupted:
            status = 'interrumpida'
        elif failure:
            status = 'fallida'
        elif pending or not products:
            status = 'parcial'
        else:
            status = 'completa_con_observaciones' if rejected else 'completa'
        report = {'proveedor': supplier, 'estado': status, 'actualizado_en': now(),
                  'productos_validos': len(products), 'registros_por_revisar': len(rejected),
                  'categorias_totales': len(cats), 'categorias_completas': len(cats) - len(pending),
                  'pendientes': pending, 'error': failure, 'archivo_csv': filename,
                  'sha256_csv': hashlib.sha256(csv_path.read_bytes()).hexdigest()}
        atomic_json(self.folder / 'resumen.json', report)
        return report


def write_csv(path, fields, rows):
    temp = path.with_name(path.name + '.tmp')
    with temp.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter=';')
        writer.writeheader(); writer.writerows(rows)
        stream.flush(); os.fsync(stream.fileno())
    os.replace(temp, path)


async def collect(module, state, crawler, max_pages, sleep=asyncio.sleep):
    for category in state.categories():
        if category['status'] == 'completa':
            continue
        url = category['url']
        print(f"Categoría {category['idx']}: {url}; desde página {category['next_page']}", flush=True)
        seen = state.seen(url)
        for page in range(category['next_page'], max_pages + 1):
            rows, status = await module.intentar_pagina(crawler, module.construir_url_pagina(url, page), url, page)
            if status == 'error':
                errors = getattr(module, 'errores_paginas', [])
                reason = errors[-1][2] if errors else 'Página fallida tras reintentos'
                state.mark(url, 'error', reason)
                break
            if status == 'sin_productos':
                state.mark(url, 'completa', 'Sin resultados confirmado por el sitio')
                break
            if status != 'ok' or not rows:
                state.mark(url, 'error', 'Respuesta inesperada o vacía sin confirmación')
                break
            valid, rejected, skus = normalize_rows(rows, module.PROVEEDOR, url, page)
            repeated = bool(skus) and skus.issubset(seen)
            last = len(rows) < module.PRODUCTOS_POR_PAGINA
            reason = 'Página repetida' if repeated else ('Última página por cantidad' if last else '')
            state.page(url, page, valid, rejected, skus, repeated or last, reason)
            seen.update(skus)
            print(f'  Página {page}: {len(valid)} válidos, {len(rejected)} por revisar', flush=True)
            if repeated or last:
                break
            await sleep(1.5)
        else:
            state.mark(url, 'error', f'Límite de {max_pages} páginas sin confirmar fin; aumente --max-paginas al reanudar')
        if category['idx'] % module.GUARDAR_CADA_N_CATEGORIAS == 0:
            state.export(module.PROVEEDOR)
        if module.PROVEEDOR == 'Freund':
            pause = (module.PAUSA_LARGA_SEGUNDOS if category['idx'] % module.PAUSA_LARGA_CADA_N_CATEGORIAS == 0
                     else module.ESPERA_ENTRE_CATEGORIAS)
        else:
            pause = 2
        await sleep(pause)


async def run(module, state, max_pages, xlsx):
    task = asyncio.current_task()
    previous = {}
    for signum in (signal.SIGTERM, signal.SIGINT):
        previous[signum] = signal.getsignal(signum)
        signal.signal(signum, lambda *_: task.cancel())
    failure, interrupted = None, False
    try:
        async with module.AsyncWebCrawler(config=module.browser_config) as crawler:
            await collect(module, state, crawler, max_pages)
    except asyncio.CancelledError:
        interrupted = True
    except Exception as exc:
        failure = f'{type(exc).__name__}: {exc}'[:600]
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    report = state.export(module.PROVEEDOR, interrupted, failure, xlsx)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 130 if interrupted else (1 if failure else (0 if report['estado'].startswith('completa') else 2))


def cli(module):
    parser = argparse.ArgumentParser(description='Scraper con recuperación persistente por página')
    parser.add_argument('--run-id', help='Identificador de corrida. Automático si se omite.')
    parser.add_argument('--reanudar', action='store_true')
    parser.add_argument('--directorio', type=Path, default=Path('datos'))
    parser.add_argument('--inicio', type=int, default=1, help='Primera categoría, desde 1')
    parser.add_argument('--fin', type=int, help='Última categoría, incluida')
    parser.add_argument('--max-paginas', type=int, default=module.MAX_PAGINAS_POR_CATEGORIA)
    parser.add_argument('--xlsx', action='store_true', help='Generar también Excel al finalizar')
    args = parser.parse_args()
    if args.reanudar and not args.run_id:
        parser.error('--reanudar necesita --run-id')
    finish = args.fin if args.fin is not None else len(module.CATEGORY_URLS)
    if not 1 <= args.inicio <= finish <= len(module.CATEGORY_URLS) or args.max_paginas < 1:
        parser.error('Rango de categorías o máximo de páginas inválido')
    state = None
    try:
        identifier = validate_id(args.run_id or run_id())
        folder = args.directorio / identifier / module.PROVEEDOR.lower()
        with lock(args.directorio / (module.PROVEEDOR.lower() + '.lock')):
            state = State(folder, module.PROVEEDOR, module.CATEGORY_URLS[args.inicio-1:finish], args.reanudar)
            print(f'Corrida: {identifier}\nEstado: {folder}', flush=True)
            return asyncio.run(run(module, state, args.max_paginas, args.xlsx))
    except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1
    finally:
        if state is not None:
            state.close()

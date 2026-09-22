"""Importa CSV de Vidri/Freund en las tablas creadas para este proyecto.

Sin opciones: valida archivos localmente. --simular: ejecuta y revierte.
--guardar: confirma toda la carga en una sola transacción.
Requiere Python 3.10+ y psycopg 3 para conectarse a Supabase.
"""
import argparse
import csv
import getpass
import io
import json
import os
import re
import sys
import unicodedata
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path


FIELDS = ('proveedor', 'sku', 'nombre', 'precio', 'fecha_consulta')


def normalize(value):
    return ''.join(c for c in unicodedata.normalize('NFD', value.strip().lower())
                   if unicodedata.category(c) != 'Mn')


def parse_date(value):
    for pattern in ('%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y'):
        try:
            return datetime.strptime(value, pattern).date()
        except ValueError:
            pass
    raise ValueError('fecha inválida; use AAAA-MM-DD, DD-MM-AAAA o DD/MM/AAAA')


def read_csv(path):
    raw = path.read_bytes()
    try:
        content = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        content = raw.decode('cp1252')
    first_line = content.splitlines()[0] if content else ''
    delimiter = ';' if first_line.count(';') > first_line.count(',') else ','
    reader = csv.reader(io.StringIO(content), delimiter=delimiter, strict=True)
    header = next(reader, [])
    header = [normalize(h) for h in header]
    header = ['fecha_consulta' if h == 'fecha' else h for h in header]
    if len(header) != len(FIELDS) or set(header) != set(FIELDS):
        raise ValueError(f'{path.name}: se requieren estas cinco columnas: {", ".join(FIELDS)}')
    products = []
    for cells in reader:
        if not cells or not any(c.strip() for c in cells):
            continue
        try:
            if len(cells) != len(header):
                raise ValueError('cantidad de columnas incorrecta')
            record = dict(zip(header, (c.strip() for c in cells)))
            if any(not record[f] for f in FIELDS):
                raise ValueError('hay campos vacíos')
            if any('\x00' in v for v in record.values()):
                raise ValueError('hay caracteres nulos')
            supplier = {'vidri': 'Vidri', 'freund': 'Freund'}.get(normalize(record['proveedor']))
            if supplier is None:
                raise ValueError('proveedor desconocido; se admite Vidri o Freund')
            price_text = record['precio']
            if not re.fullmatch(r'\d+(?:\.\d{1,2})?', price_text):
                raise ValueError('precio inválido; use punto decimal, sin $ ni separadores de miles')
            price = Decimal(price_text)
            if price >= Decimal('1000000000000'):
                raise ValueError('precio fuera del rango de la tabla')
            products.append((supplier, record['sku'], record['nombre'], price,
                             parse_date(record['fecha_consulta'])))
        except ValueError as exc:
            raise ValueError(f'{path.name}, línea {reader.line_num}: {exc}') from None
    if not products:
        raise ValueError(f'{path.name}: no contiene productos')
    return products


def load_files(paths):
    rows, seen = [], set()
    for path in paths:
        for row in read_csv(path):
            key = row[:2]
            if key in seen:
                raise ValueError(f'Producto repetido en la carga: {key[0]} / {key[1]}. '
                                 'Use un solo registro por proveedor y SKU.')
            seen.add(key)
            rows.append(row)
    return rows


def read_config(path):
    config = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(config, dict) or set(config) != {'host', 'port', 'dbname', 'user'}:
        raise ValueError('conexion.json debe contener solamente host, port, dbname y user')
    for key in ('host', 'dbname', 'user'):
        if not isinstance(config[key], str) or not config[key].strip():
            raise ValueError(f'Falta completar {key} en conexion.json')
        if 'COPIAR' in config[key]:
            raise ValueError('Complete conexion.json con los datos de Connect > Session pooler')
    config['port'] = int(config['port'])
    if not 1 <= config['port'] <= 65535:
        raise ValueError('Puerto inválido')
    return config


STATS_SQL = """
select
 count(*) filter (where p.sku is null) as nuevos,
 count(*) filter (where p.sku is not null and e.fecha_consulta < p.fecha_consulta) as antiguos,
 count(*) filter (where p.sku is not null and e.fecha_consulta >= p.fecha_consulta
                  and e.precio is distinct from p.precio) as cambio_precio,
 count(*) filter (where p.sku is not null and e.fecha_consulta >= p.fecha_consulta
                  and e.precio is not distinct from p.precio
                  and (e.nombre, e.fecha_consulta) is distinct from
                      (p.nombre, p.fecha_consulta)) as otros_cambios,
 count(*) filter (where p.sku is not null and
                  (e.nombre, e.precio, e.fecha_consulta) is not distinct from
                  (p.nombre, p.precio, p.fecha_consulta)) as iguales
from entrada_importador e
left join public.productos p using (proveedor, sku)
"""

UPSERT_SQL = """
insert into public.productos as p (proveedor, sku, nombre, precio, fecha_consulta)
select proveedor, sku, nombre, precio, fecha_consulta
from entrada_importador
order by proveedor, sku
on conflict (proveedor, sku) do update
set nombre = excluded.nombre,
    precio = excluded.precio,
    fecha_consulta = excluded.fecha_consulta
where excluded.fecha_consulta >= p.fecha_consulta
  and (excluded.nombre, excluded.precio, excluded.fecha_consulta)
      is distinct from (p.nombre, p.precio, p.fecha_consulta)
"""


def import_rows(psycopg, config, password, rows, simulate):
    sslmode = os.environ.get('SUPABASE_SSLMODE', 'require')
    if sslmode not in ('require', 'verify-full'):
        raise ValueError('SUPABASE_SSLMODE debe ser require o verify-full')
    ssl_options = {'sslmode': sslmode}
    if os.environ.get('SUPABASE_SSLROOTCERT'):
        ssl_options['sslrootcert'] = os.environ['SUPABASE_SSLROOTCERT']
    with psycopg.connect(**config, password=password, **ssl_options,
                         connect_timeout=15, application_name='importador_catalogo') as conn:
        with conn.cursor() as cur:
            cur.execute("set local lock_timeout = '10s'")
            cur.execute("set local statement_timeout = '120s'")
            # Evita dos cargas simultáneas y mantiene coherentes los recuentos.
            # Las consultas de lectura siguen permitidas.
            cur.execute('lock table public.productos in share row exclusive mode')
            cur.execute("""
                select exists (
                    select 1 from pg_trigger
                    where tgrelid = 'public.productos'::regclass
                      and tgname = 'guardar_historial_precio'
                      and tgenabled in ('O', 'A') and not tgisinternal
                ), to_regclass('public.historial_precios') is not null
            """)
            if cur.fetchone() != (True, True):
                raise ValueError('Falta la tabla del historial o el trigger activo guardar_historial_precio')
            cur.execute("""
                create temporary table entrada_importador (
                    proveedor text, sku text, nombre text,
                    precio numeric(14,2), fecha_consulta date,
                    primary key (proveedor, sku)
                ) on commit drop
            """)
            with cur.copy('copy entrada_importador (proveedor, sku, nombre, precio, fecha_consulta) from stdin') as copy:
                for row in rows:
                    copy.write_row(row)
            cur.execute(STATS_SQL)
            stats = cur.fetchone()
            cur.execute(UPSERT_SQL)
        if simulate:
            conn.rollback()
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archivos', nargs='+', type=Path, help='Uno o varios CSV')
    parser.add_argument('--conexion', type=Path, default=Path(__file__).with_name('conexion.json'))
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--simular', action='store_true', help='Probar en Supabase y revertir la carga')
    modes.add_argument('--guardar', action='store_true', help='Guardar en Supabase')
    args = parser.parse_args()
    try:
        rows = load_files(args.archivos)
        print(f'Validación correcta: {len(rows)} productos.')
        for supplier, count in sorted(Counter(r[0] for r in rows).items()):
            print(f'  {supplier}: {count}')
        if not args.simular and not args.guardar:
            print('Validación local terminada. No se conectó a Supabase.')
            return 0
        config = read_config(args.conexion)
        try:
            import psycopg
        except ImportError:
            raise ValueError('Instale la dependencia: python -m pip install -r requirements.txt') from None
        password = os.environ.get('SUPABASE_DB_PASSWORD')
        if not password:
            if not sys.stdin.isatty():
                raise ValueError('Falta SUPABASE_DB_PASSWORD para la ejecución sin intervención')
            password = getpass.getpass('Contraseña de la base de datos: ')
        if not password:
            raise ValueError('La contraseña está vacía')
        try:
            stats = import_rows(psycopg, config, password, rows, args.simular)
        except psycopg.Error as exc:
            # No imprimir el objeto de conexión, contraseñas ni datos de filas.
            code = exc.sqlstate or 'CONEXION'
            print(f'Error de base de datos ({code}). No se confirmó la carga. '
                  'Revise conexión, contraseña, tablas y permisos.', file=sys.stderr)
            print('Si se perdió la conexión al confirmar, verifique los registros antes de repetir.', file=sys.stderr)
            return 1
        labels = ('Productos nuevos', 'Omitidos por fecha anterior', 'Cambios de precio',
                  'Cambios solo de nombre o fecha', 'Sin cambios')
        for label, count in zip(labels, stats):
            print(f'{label}: {count}')
        print('SIMULACIÓN: cambios revertidos; no se guardaron productos ni precios históricos.'
              if args.simular else 'Carga confirmada correctamente.')
        return 0
    except (ValueError, OSError, csv.Error) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print('\nOperación interrumpida. Si ocurrió durante la confirmación, verifique la base de datos.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())

"""Convierte la lista de precios de un proveedor (.xlsx) al CSV que importa importar_productos.py.

Salida: <proveedor>;<nombre>;<sku>;<precio>;<fecha_consulta> (UTF-8 con BOM, separador ';').
Reglas: el precio se redondea a 2 decimales (mitad hacia arriba); se conserva el precio 0;
se conserva el texto (incluidos '>' y comillas) salvo que los espacios repetidos se colapsan;
el código se lee como texto para no perder los ceros a la izquierda. Las filas con campos
vacíos, precio inválido o código repetido no se importan: van a registros_por_revisar_<proveedor>.csv.
"""
import argparse
import csv
import re
import sys
import unicodedata
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

import openpyxl

SALVADOR = timezone(timedelta(hours=-6))
FIELDS = ['proveedor', 'nombre', 'sku', 'precio', 'fecha_consulta']
MAX_PRECIO = Decimal('1000000000000')


def normalize(value):
    text = unicodedata.normalize('NFD', str(value or '').strip().lower())
    return ''.join(c for c in text if unicodedata.category(c) != 'Mn')


def clean_text(value):
    return re.sub(r'\s+', ' ', str(value)).strip()


def parse_price(value):
    """Devuelve (Decimal con 2 decimales, None) o (None, motivo)."""
    if isinstance(value, bool) or value is None or (isinstance(value, str) and not value.strip()):
        return None, 'precio vacío'
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation:
        return None, f'precio no numérico: {value!r}'
    if not number.is_finite():
        return None, f'precio no numérico: {value!r}'
    if number < 0:
        return None, 'precio negativo'
    number = number.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    if number >= MAX_PRECIO:
        return None, 'precio fuera de rango'
    return number, None


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('xlsx', type=Path, help='Archivo Excel del proveedor')
    parser.add_argument('--proveedor', required=True, help='Nombre con que se guarda, por ejemplo Delpin')
    parser.add_argument('--fecha', help='DD-MM-AAAA; por defecto la fecha de hoy en El Salvador (UTC-6)')
    parser.add_argument('--hoja', help='Nombre de la hoja; por defecto la primera')
    parser.add_argument('--col-codigo', default='CODIGO')
    parser.add_argument('--col-nombre', default='DESCRIPCION')
    parser.add_argument('--col-precio', default='PRECIO')
    parser.add_argument('--salida', type=Path, help='CSV de salida; por defecto productos_<proveedor>.csv junto al .xlsx')
    return parser.parse_args()


def find_columns(header, wanted):
    names = [normalize(h) for h in header]
    missing = [w for w in wanted.values() if normalize(w) not in names]
    if missing:
        raise ValueError('No encuentro estas columnas en la hoja: ' + ', '.join(missing)
                         + '. Columnas encontradas: ' + ', '.join(str(h) for h in header if h))
    return {key: names.index(normalize(name)) for key, name in wanted.items()}


def convert(args):
    day = datetime.strptime(args.fecha, '%d-%m-%Y').date() if args.fecha else datetime.now(SALVADOR).date()
    proveedor = clean_text(args.proveedor)
    if not proveedor:
        raise ValueError('El nombre del proveedor está vacío')
    workbook = openpyxl.load_workbook(args.xlsx, data_only=True, read_only=True)
    sheet = workbook[args.hoja] if args.hoja else workbook.worksheets[0]
    rows = sheet.iter_rows(values_only=True)
    header = next(rows, None)
    if not header:
        raise ValueError('La hoja está vacía')
    cols = find_columns(header, {'sku': args.col_codigo, 'nombre': args.col_nombre, 'precio': args.col_precio})

    products, review, seen = [], [], {}
    total = 0
    for line, row in enumerate(rows, 2):
        if not row or not any(v not in (None, '') for v in row):
            continue
        total += 1
        raw = {k: (row[i] if i < len(row) else None) for k, i in cols.items()}
        sku = clean_text(raw['sku']) if raw['sku'] is not None else ''
        nombre = clean_text(raw['nombre']) if raw['nombre'] is not None else ''
        precio, motivo = parse_price(raw['precio'])
        if not sku:
            motivo = 'código vacío'
        elif not nombre:
            motivo = 'descripción vacía'
        elif motivo is None and sku in seen:
            motivo = f'código repetido (primera aparición en la fila {seen[sku]})'
        if motivo:
            review.append({'fila': line, 'motivo': motivo, 'codigo': sku, 'descripcion': nombre, 'precio': raw['precio']})
            continue
        seen[sku] = line
        products.append([proveedor, nombre, sku, f'{precio:.2f}', day.strftime('%d-%m-%Y')])

    out = args.salida or args.xlsx.with_name(f'productos_{normalize(proveedor)}.csv')
    review_path = out.with_name(f'registros_por_revisar_{normalize(proveedor)}.csv')
    with out.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream, delimiter=';')
        writer.writerow(FIELDS)
        writer.writerows(products)
    if review:
        with review_path.open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=['fila', 'motivo', 'codigo', 'descripcion', 'precio'], delimiter=';')
            writer.writeheader()
            writer.writerows(review)
    elif review_path.exists():
        review_path.unlink()
    return {'filas': total, 'productos': len(products), 'revision': len(review), 'fecha': day,
            'csv': out, 'revision_csv': review_path if review else None,
            'precio_cero': sum(1 for p in products if p[3] == '0.00')}


def main():
    try:
        result = convert(parse_args())
    except (ValueError, OSError, KeyError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1
    print(f"Filas leídas: {result['filas']}\nProductos convertidos: {result['productos']}"
          f" (de ellos con precio 0: {result['precio_cero']})\nPara revisar (no se importan): {result['revision']}\n"
          f"Fecha de consulta: {result['fecha'].strftime('%d-%m-%Y')}\nArchivo: {result['csv']}")
    if result['revision_csv']:
        print(f"Revisión: {result['revision_csv']}")
    return 0


if __name__ == '__main__':
    sys.exit(main())

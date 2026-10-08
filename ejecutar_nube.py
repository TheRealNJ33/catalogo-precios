"""Reanuda proveedores pendientes; importa solo CSV completos y conserva su confirmación.

Salidas: 0 = selección terminada; 2 = parcial reanudable; 1 = fallo o plazo vencido.
La campaña de producción catalogo-AAAA-WNN vence el miércoles a las 01:17 de
El Salvador. Las corridas de prueba (otro ID o rango parcial) no usan ese plazo.
"""
import argparse
import ast
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from ejecucion_persistente import atomic_json, lock, run_id, validate_id, now

SCRIPTS = {'vidri': 'scraper_vidri_2.py', 'freund': 'scraper_freund_V8.py'}
ES = timezone(timedelta(hours=-6))


def read_json(path):
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict):
        raise ValueError(f'JSON inválido: {path.name}')
    return data


def importable(report, csv_path):
    return (report.get('estado') in ('completa', 'completa_con_observaciones')
            and isinstance(report.get('productos_validos'), int)
            and report['productos_validos'] > 0
            and report.get('categorias_totales', 0) > 0
            and report.get('categorias_completas') == report.get('categorias_totales')
            and report.get('pendientes') == [] and not report.get('error')
            and csv_path.is_file()
            and hashlib.sha256(csv_path.read_bytes()).hexdigest() == report.get('sha256_csv'))


def selection(base, supplier, start, finish):
    """Lee las URL sin importar el scraper ni iniciar un navegador."""
    tree = ast.parse((base / SCRIPTS[supplier]).read_text(encoding='utf-8-sig'))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'CATEGORY_URLS'
                                                for t in node.targets):
            urls = ast.literal_eval(node.value)
            break
    else:
        raise ValueError(f'No se encontró CATEGORY_URLS en {SCRIPTS[supplier]}')
    end = finish if finish is not None else len(urls)
    if not 1 <= start <= end <= len(urls):
        raise ValueError(f'Rango de categorías inválido para {supplier}')
    return urls[start - 1:end]


def matches_state(target, supplier, urls):
    path = target / 'estado.sqlite'
    if not path.is_file():
        return False
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        row = db.execute("select value from meta where key='manifest'").fetchone()
    manifest = json.loads(row[0]) if row else {}
    return (manifest.get('supplier', '').lower() == supplier and manifest.get('urls') == urls)


def saved_result(target, supplier, previous, urls):
    """Compatible con pipeline antiguo, siempre que existan resumen, CSV y estado."""
    if previous.get('scraper_exit') != 0 or previous.get('importacion') != 'guardada':
        return None
    report_path = target / 'resumen.json'
    if not report_path.is_file() or not matches_state(target, supplier, urls):
        return None
    report = read_json(report_path)
    if not importable(report, target / f'productos_{supplier}.csv'):
        return None
    if (report.get('proveedor', '').lower() != supplier
            or report['categorias_totales'] != len(urls)
            or previous.get('productos') != report['productos_validos']):
        return None
    recorded_hash = previous.get('sha256_csv')
    if recorded_hash and recorded_hash != report['sha256_csv']:
        return None
    return {**previous, 'sha256_csv': report['sha256_csv'], 'estado_verificado': True}


def weekly_deadline(identifier, hour):
    match = re.fullmatch(r'catalogo-(\d{4})-W(\d{2})', identifier)
    if not match:
        return None
    monday = datetime.fromisocalendar(int(match[1]), int(match[2]), 1).replace(tzinfo=ES)
    hh, mm = map(int, hour.split(':'))
    return monday + timedelta(days=2, hours=hh, minutes=mm)


def run_logged(command, logfile, base, deadline, env=None):
    """Deja margen para que el scraper cierre SQLite y el workflow guarde el caché."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None
    with logfile.open('a', encoding='utf-8') as log:
        log.write(f'\nINICIO {now()}\n'); log.flush()
        with subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, cwd=base, env=env) as process:
            try:
                return process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                log.write('\nPausa por presupuesto de tiempo; se solicita guardar el avance.\n'); log.flush()
                process.terminate()
                try:
                    process.wait(timeout=90)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait()
                return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--proveedor', choices=['ambos', *SCRIPTS], default='ambos')
    parser.add_argument('--run-id')
    parser.add_argument('--reanudar', action='store_true')
    parser.add_argument('--directorio', type=Path, default=Path('datos'))
    parser.add_argument('--conexion', type=Path, default=Path(__file__).with_name('conexion.json'))
    parser.add_argument('--inicio', type=int, default=1)
    parser.add_argument('--fin', type=int)
    parser.add_argument('--max-paginas', type=int, default=100)
    parser.add_argument('--minutos-maximos', type=float, default=315)
    parser.add_argument('--hora-limite-miercoles', default='01:17', metavar='HH:MM')
    parser.add_argument('--xlsx', action='store_true')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--guardar', action='store_true')
    modes.add_argument('--simular-importacion', action='store_true')
    args = parser.parse_args()
    if args.reanudar and not args.run_id:
        parser.error('--reanudar requiere --run-id')
    if args.max_paginas < 1 or not 0 < args.minutos_maximos <= 315:
        parser.error('--max-paginas debe ser positivo y --minutos-maximos debe estar entre 0 y 315')
    if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', args.hora_limite_miercoles):
        parser.error('--hora-limite-miercoles debe ser HH:MM')
    base = Path(__file__).resolve().parent
    try:
        identifier = validate_id(args.run_id or run_id())
        folder = args.directorio.resolve() / identifier
        suppliers = list(SCRIPTS) if args.proveedor == 'ambos' else [args.proveedor]
        # Un rango acotado no puede completar una campaña semanal de producción.
        production = weekly_deadline(identifier, args.hora_limite_miercoles)
        if production and (args.inicio != 1 or args.fin is not None):
            raise ValueError('Use un run-id de prueba para limitar categorías; el ID semanal exige el catálogo completo')
        selected_urls = {s: selection(base, s, args.inicio, args.fin) for s in suppliers}
        with lock(folder.parent / 'pipeline.lock'):
            if folder.exists() and not args.reanudar:
                raise ValueError('Corrida existente: use --reanudar o un --run-id nuevo')
            if not folder.exists() and args.reanudar:
                raise ValueError('No existe la corrida que quiere reanudar')
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / 'pipeline.json'
            previous = read_json(path) if path.exists() else {}
            if previous and previous.get('run_id') != identifier:
                raise ValueError('El pipeline guardado pertenece a otra corrida')
            results = previous.get('resultados', {})
            if not isinstance(results, dict) or any(not isinstance(v, dict) for v in results.values()):
                raise ValueError('Resultados de pipeline inválidos')
            for s in results:
                results[s].pop('omitido_en_esta_ejecucion', None)
            deadline = time.monotonic() + args.minutos_maximos * 60
            hard_error = False
            pending = False
            checked_config = False

            def persist():
                complete = all(results.get(s, {}).get('importacion') == 'guardada'
                               and results.get(s, {}).get('alcance_completo') is True
                               and results.get(s, {}).get('estado_verificado') is True for s in SCRIPTS)
                value = {'run_id': identifier, 'actualizado_en': now(), 'resultados': results,
                         'campana_completa': complete,
                         'limite_guardado': production.isoformat() if production else None}
                atomic_json(path, value)
                return complete

            # Conserva al otro proveedor, incluso cuando esta llamada pide solo uno.
            # Verifica también su evidencia antes de declarar la campaña completa.
            for s in SCRIPTS:
                if s not in results:
                    continue
                urls = selected_urls[s] if s in selected_urls else selection(base, s, 1, None)
                verified = saved_result(folder / s, s, results[s], urls)
                if verified:
                    results[s] = {**verified, 'alcance_completo': args.inicio == 1 and args.fin is None}
                else:
                    results[s]['estado_verificado'] = False

            print(f'Corrida: {identifier}; resultados: {folder}', flush=True)
            for supplier in suppliers:
                target = folder / supplier
                old = results.get(supplier, {})
                if args.guardar and old.get('importacion') == 'guardada' and old.get('estado_verificado'):
                    old['omitido_en_esta_ejecucion'] = True
                    print(f'{supplier}: ya importado en esta campaña; se omite scraping e importación.', flush=True)
                    persist()
                    continue
                if not checked_config and (args.guardar or args.simular_importacion):
                    from importar_productos import read_config
                    read_config(args.conexion)
                    if not os.environ.get('SUPABASE_DB_PASSWORD'):
                        raise ValueError('Defina SUPABASE_DB_PASSWORD antes de iniciar la carga automática')
                    try:
                        import psycopg
                    except ImportError:
                        raise ValueError('Falta psycopg; instale requirements.txt') from None
                    checked_config = True
                status = {'scraper_exit': None, 'importacion': 'pendiente',
                          'alcance_completo': args.inicio == 1 and args.fin is None,
                          'estado_verificado': False}
                results[supplier] = status
                persist()
                resume = args.reanudar and (target / 'estado.sqlite').exists()
                command = [sys.executable, '-u', str(base / SCRIPTS[supplier]),
                           '--run-id', identifier, '--directorio', str(folder.parent),
                           '--inicio', str(args.inicio), '--max-paginas', str(args.max_paginas)]
                if args.fin is not None:
                    command += ['--fin', str(args.fin)]
                if resume:
                    command.append('--reanudar')
                if args.xlsx:
                    command.append('--xlsx')
                env = {**os.environ, 'CATALOGO_DIAGNOSTICO_DIR': str(target / 'diagnostico')}
                code = run_logged(command, folder / f'{supplier}.log', base, deadline, env)
                status['scraper_exit'] = code
                if code is None or code == 2:
                    status['importacion'] = 'omitida_por_scraping_incompleto'
                    status['motivo'] = 'presupuesto_de_tiempo' if code is None else 'categorias_pendientes'
                    pending = True
                elif code != 0:
                    status['importacion'] = 'omitida_por_error_scraper'
                    hard_error = True
                else:
                    report = read_json(target / 'resumen.json')
                    csv_path = target / f'productos_{supplier}.csv'
                    if (not importable(report, csv_path)
                            or report.get('proveedor', '').lower() != supplier
                            or report['categorias_totales'] != len(selected_urls[supplier])
                            or not matches_state(target, supplier, selected_urls[supplier])):
                        status['importacion'] = 'omitida_por_validacion'
                        hard_error = True
                    else:
                        status.update(productos=report['productos_validos'],
                                      por_revisar=report['registros_por_revisar'],
                                      sha256_csv=report['sha256_csv'])
                        status['importacion'] = 'no_solicitada'
                        if args.guardar or args.simular_importacion:
                            if time.monotonic() >= deadline:
                                status['importacion'] = 'pendiente_por_tiempo'
                                pending = True
                                persist()
                                print(f'{supplier}: CSV completo; importación pendiente para la próxima corrida.', flush=True)
                                continue
                            status['importacion'] = 'en_proceso'
                            persist()
                            command = [sys.executable, '-u', str(base / 'importar_productos.py'),
                                       str(csv_path), '--conexion', str(args.conexion.resolve()),
                                       '--guardar' if args.guardar else '--simular']
                            code = run_logged(command, folder / f'{supplier}_importacion.log', base, deadline)
                            status['importacion_exit'] = code
                            if code == 0:
                                status['importacion'] = 'guardada' if args.guardar else 'simulada'
                                status['estado_verificado'] = True
                                status['importado_en'] = now()
                            else:
                                status['importacion'] = 'fallida' if code is not None else 'sin_confirmar_por_tiempo'
                                hard_error = True
                persist()
                print(supplier + ': ' + json.dumps(status, ensure_ascii=False), flush=True)
            complete = persist()
            if args.guardar and production and not complete:
                if datetime.now(ES) >= production:
                    print('ERROR: venció el plazo del miércoles y aún falta confirmar el guardado de ambos proveedores.', file=sys.stderr)
                    return 1
                pending = True
            if hard_error:
                print('ERROR: no se confirmó el guardado; revise los logs.', file=sys.stderr)
                return 1
            return 2 if pending else 0
    except (ValueError, RuntimeError, OSError, sqlite3.Error, SyntaxError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('Interrumpido. Reanude con el mismo --run-id.', file=sys.stderr)
        return 130


if __name__ == '__main__':
    sys.exit(main())

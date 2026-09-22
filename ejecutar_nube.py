"""Ejecuta ambos proveedores por separado y solo importa corridas completas."""
import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from ejecucion_persistente import atomic_json, lock, run_id, validate_id, now

SCRIPTS = {'vidri': 'scraper_vidri_2.py', 'freund': 'scraper_freund_V8.py'}


def importable(report, csv_path):
    if report['estado'] not in ('completa', 'completa_con_observaciones'):
        return False
    return (report['productos_validos'] > 0 and csv_path.is_file()
            and hashlib.sha256(csv_path.read_bytes()).hexdigest() == report['sha256_csv'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--proveedor', choices=['ambos', *SCRIPTS], default='ambos')
    parser.add_argument('--run-id')
    parser.add_argument('--reanudar', action='store_true')
    parser.add_argument('--directorio', type=Path, default=Path('datos'))
    parser.add_argument('--conexion', type=Path, default=Path(__file__).with_name('conexion.json'))
    parser.add_argument('--inicio', type=int, default=1)
    parser.add_argument('--fin', type=int)
    parser.add_argument('--max-paginas', type=int, default=50)
    parser.add_argument('--xlsx', action='store_true')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--guardar', action='store_true')
    modes.add_argument('--simular-importacion', action='store_true')
    args = parser.parse_args()
    if args.reanudar and not args.run_id:
        parser.error('--reanudar requiere --run-id')
    base = Path(__file__).resolve().parent
    results = {}
    try:
        identifier = validate_id(args.run_id or run_id())
        args.directorio = args.directorio.resolve()
        folder = args.directorio / identifier
        suppliers = list(SCRIPTS) if args.proveedor == 'ambos' else [args.proveedor]
        if args.guardar or args.simular_importacion:
            from importar_productos import read_config
            read_config(args.conexion)
            if not os.environ.get('SUPABASE_DB_PASSWORD'):
                raise ValueError('Defina SUPABASE_DB_PASSWORD antes de iniciar la carga automática')
            try:
                import psycopg  # comprobar dependencia antes de pasar horas recolectando
            except ImportError:
                raise ValueError('Falta psycopg; instale requirements.txt') from None
        with lock(args.directorio / 'pipeline.lock'):
            if folder.exists() and not args.reanudar:
                raise ValueError('Corrida existente: use --reanudar o un --run-id nuevo')
            if not folder.exists() and args.reanudar:
                raise ValueError('No existe la corrida que quiere reanudar')
            folder.mkdir(parents=True, exist_ok=True)
            print(f'Corrida: {identifier}; resultados: {folder}', flush=True)
            for supplier in suppliers:
                target = folder / supplier
                resume = args.reanudar and (target / 'estado.sqlite').exists()
                command = [sys.executable, '-u', str(base / SCRIPTS[supplier]),
                           '--run-id', identifier, '--directorio', str(args.directorio),
                           '--inicio', str(args.inicio), '--max-paginas', str(args.max_paginas)]
                if args.fin is not None:
                    command += ['--fin', str(args.fin)]
                if resume:
                    command.append('--reanudar')
                if args.xlsx:
                    command.append('--xlsx')
                # Cada intento conserva sus mensajes incluso si termina abruptamente.
                with (folder / (supplier + '.log')).open('a', encoding='utf-8') as log:
                    log.write(f'\nINICIO {now()}\n'); log.flush()
                    completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, cwd=base)
                status = {'scraper_exit': completed.returncode, 'importacion': 'no_solicitada'}
                results[supplier] = status
                report_path = target / 'resumen.json'
                csv_path = target / ('productos_' + supplier + '.csv')
                if completed.returncode != 0 or not report_path.exists():
                    status['importacion'] = 'omitida_por_scraping_incompleto'
                    print(f'{supplier}: pendiente; revise {supplier}.log y resumen.json', flush=True)
                else:
                    report = json.loads(report_path.read_text(encoding='utf-8'))
                    if not importable(report, csv_path):
                        status['importacion'] = 'omitida_por_validacion'
                    elif args.guardar or args.simular_importacion:
                        import_command = [sys.executable, '-u', str(base / 'importar_productos.py'),
                                          str(csv_path), '--conexion', str(args.conexion.resolve()),
                                          '--guardar' if args.guardar else '--simular']
                        with (folder / (supplier + '_importacion.log')).open('a', encoding='utf-8') as log:
                            log.write(f'\nINICIO {now()}\n'); log.flush()
                            imported = subprocess.run(import_command, stdout=log, stderr=subprocess.STDOUT)
                        status['importacion'] = ('guardada' if args.guardar else 'simulada') if imported.returncode == 0 else 'fallida'
                    status['productos'] = report['productos_validos']
                    status['por_revisar'] = report['registros_por_revisar']
                atomic_json(folder / 'pipeline.json', {'run_id': identifier, 'actualizado_en': now(), 'resultados': results})
                print(supplier + ': ' + json.dumps(status, ensure_ascii=False), flush=True)
            return 0 if all(r['scraper_exit'] == 0 and r['importacion'] in ('no_solicitada', 'guardada', 'simulada')
                            for r in results.values()) else 2
    except (ValueError, RuntimeError, OSError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('Interrumpido. Reanude con el mismo --run-id.', file=sys.stderr)
        return 130


if __name__ == '__main__':
    sys.exit(main())

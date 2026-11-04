"""Procesa listas de proveedores (.xlsx) depositadas en entradas/<proveedor>/ de un repositorio PRIVADO.

Para cada archivo: lo convierte con la configuración del proveedor (proveedores.json), valida el CSV
y, en modo "guardar", simula la importación, la guarda en Supabase y borra el .xlsx. El modo "simular"
llega hasta la simulación contra Supabase (que se revierte) y se detiene sin guardar ni borrar. Si algo
falla, el .xlsx se conserva para reintentar. Los CSV se generan en una carpeta temporal y nunca se versionan.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RUTA_VALIDA = re.compile(r'^([a-z0-9_-]+)/[^/\x00-\x1f]+\.xlsx$')


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('proveedores.json'))
    parser.add_argument('--entradas', type=Path, default=Path('entradas'), help='Carpeta que contiene una subcarpeta por proveedor')
    parser.add_argument('--archivo', help='Un solo .xlsx (ruta dentro de la carpeta de entradas, o completa); por defecto todos')
    parser.add_argument('--modo', choices=['validar', 'simular', 'guardar'], default='validar')
    parser.add_argument('--convertidor', type=Path, default=AQUI / 'convertir_xlsx.py')
    parser.add_argument('--importador', type=Path, default=AQUI / 'importar_productos.py')
    return parser.parse_args()


def listar(entradas, archivo):
    base = entradas.resolve()
    if archivo:
        candidato = Path(archivo)
        ruta = (candidato if candidato.is_absolute() else (Path.cwd() / candidato)).resolve()
        if not ruta.is_file():
            ruta = (base / archivo).resolve()
        if not ruta.is_file():
            raise ValueError(f'No existe el archivo: {archivo}')
        return [ruta]
    return sorted(p.resolve() for p in base.rglob('*.xlsx') if p.is_file())


def proveedor_de(ruta, base):
    try:
        relativa = ruta.relative_to(base).as_posix()
    except ValueError:
        raise ValueError('El archivo debe estar dentro de la carpeta de entradas') from None
    match = RUTA_VALIDA.fullmatch(relativa)
    if not match:
        raise ValueError(f'Ruta no válida: use entradas/<proveedor>/<archivo>.xlsx (recibido: {relativa})')
    return match[1]


def ejecutar(comando, titulo):
    print(f'--- {titulo}', flush=True)
    resultado = subprocess.run(comando, capture_output=True, text=True)
    salida = (resultado.stdout + resultado.stderr).strip()
    if salida:
        print(salida, flush=True)
    return resultado.returncode


def comando_conversion(args, cfg, xlsx, csv):
    comando = [sys.executable, str(args.convertidor), str(xlsx), '--proveedor', cfg['nombre'], '--salida', str(csv)]
    for opcion, clave in (('--col-codigo', 'col_codigo'), ('--col-nombre', 'col_nombre'),
                          ('--col-precio', 'col_precio'), ('--hoja', 'hoja'),
                          ('--prefijo-sin-stock', 'prefijo_sin_stock')):
        if cfg.get(clave):
            comando += [opcion, str(cfg[clave])]
    for sede in cfg.get('sedes', []):
        comando += ['--sede', f"{sede['columna']}={sede['letra']}"]
    for columna in cfg.get('cols_stock', []):
        comando += ['--col-stock', str(columna)]
    return comando


def procesar(args, ruta, base, config):
    clave = proveedor_de(ruta, base)
    if clave not in config:
        raise ValueError(f'El proveedor "{clave}" no está en {args.config.name}. Disponibles: {", ".join(sorted(config))}')
    cfg = config[clave]
    temporal = Path(tempfile.mkdtemp(prefix='entrada_'))
    try:
        csv = temporal / f'productos_{clave}.csv'
        print(f'\n===== {ruta.name} -> proveedor {cfg["nombre"]} ({args.modo}) =====', flush=True)
        if ejecutar(comando_conversion(args, cfg, ruta, csv), 'Conversión') != 0:
            raise ValueError('Falló la conversión')
        revision = temporal / f'registros_por_revisar_{clave}.csv'
        if revision.exists():
            lineas = revision.read_text(encoding='utf-8-sig').splitlines()
            print(f'Hay {len(lineas) - 1} filas para revisar (no se importan). Primeras:')
            print('\n'.join(lineas[:11]))
        if ejecutar([sys.executable, str(args.importador), str(csv)], 'Validación del CSV') != 0:
            raise ValueError('El CSV no pasó la validación')
        if args.modo == 'validar':
            print('Modo validar: no se importó nada ni se borró el archivo.')
            return
        if not os.environ.get('SUPABASE_DB_PASSWORD'):
            raise ValueError('Faltan los secretos de Supabase (SUPABASE_DB_PASSWORD). El archivo se conserva para reintentar.')
        if ejecutar([sys.executable, str(args.importador), str(csv), '--simular'], 'Simulación (se revierte)') != 0:
            raise ValueError('La simulación falló; no se guardó nada')
        if args.modo == 'simular':
            print('Modo simular: la prueba con Supabase salió bien y se revirtió. No se guardó nada ni se borró el archivo.')
            return
        if ejecutar([sys.executable, str(args.importador), str(csv), '--guardar'], 'Guardado en Supabase') != 0:
            raise ValueError('El guardado falló; verifique la base antes de reintentar')
        ruta.unlink()
        print(f'Importado y eliminado: {ruta.name}', flush=True)
    finally:
        shutil.rmtree(temporal, ignore_errors=True)


def main():
    args = parse_args()
    try:
        config = json.loads(args.config.read_text(encoding='utf-8'))
        base = args.entradas.resolve()
        archivos = listar(args.entradas, args.archivo) if base.exists() or args.archivo else []
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1
    if not archivos:
        print('No hay listas .xlsx por procesar.')
        return 0
    fallos = 0
    for ruta in archivos:
        try:
            procesar(args, ruta, base, config)
        except ValueError as exc:
            fallos += 1
            print(f'ERROR en {ruta.name}: {exc}', file=sys.stderr, flush=True)
    print(f'\nResumen: {len(archivos) - fallos} correctos, {fallos} con error.')
    return 1 if fallos else 0


if __name__ == '__main__':
    sys.exit(main())

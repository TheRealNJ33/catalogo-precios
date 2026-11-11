"""Revisa la campaña semanal de scraping (Vidri y Freund) y avisa de lo que no esté bien.

Compara la campaña con la anterior usando los artefactos de logs que sube cada corrida. Solo necesita
la CLI `gh` (usa la API REST) y la biblioteca estándar. Imprime un informe y termina con código 0 si
todo está bien o 1 si hay algo que revisar. No modifica nada: solo lee.

    python revisar_campana.py                      # campaña de la semana actual (hora de El Salvador)
    python revisar_campana.py --campana catalogo-2026-W46
"""
import argparse
import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

SALVADOR = timezone(timedelta(hours=-6))
PRODUCTOS_POR_PAGINA = {'vidri': 15, 'freund': 20}
UMBRAL_CAIDA_TOTAL = 0.03      # caída de productos de un proveedor que se considera alerta
MIN_PRODUCTOS_CATEGORIA = 10   # por debajo de esto una categoría cambia sin que sea alerta
MIN_CAIDA_CORTE = 5            # productos perdidos, con la última página completa, que sugieren un corte
MIN_CAIDA_MITAD = 15           # productos perdidos para que «perdió la mitad» sea alerta (evita ruido en categorías chicas)
SALTO_POR_REVISAR = 30         # aumento de registros por revisar que merece un aviso


def gh(*args, timeout=300):
    resultado = subprocess.run(['gh', *args], capture_output=True, text=True, timeout=timeout)
    if resultado.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])}: {(resultado.stderr or resultado.stdout).strip()[:300]}")
    return resultado.stdout


def api(ruta):
    return json.loads(gh('api', ruta))


def id_campana(dia):
    anio, semana, _ = dia.isocalendar()
    return f'catalogo-{anio}-W{semana:02d}'


def campana_anterior(campana):
    anio, semana = map(int, re.fullmatch(r'catalogo-(\d{4})-W(\d{2})', campana).groups())
    lunes = datetime.fromisocalendar(anio, semana, 1)
    return id_campana(lunes - timedelta(days=7))


def ventana(campana):
    """Lunes 00:00 a lunes siguiente 00:00, hora de El Salvador, en UTC."""
    anio, semana = map(int, re.fullmatch(r'catalogo-(\d{4})-W(\d{2})', campana).groups())
    inicio = datetime.fromisocalendar(anio, semana, 1).replace(tzinfo=SALVADOR)
    return inicio.astimezone(timezone.utc), (inicio + timedelta(days=7)).astimezone(timezone.utc)


def fecha(texto):
    return datetime.fromisoformat(texto.replace('Z', '+00:00'))


def corridas_de_la_semana(repo, workflow, campana):
    inicio, fin = ventana(campana)
    datos = api(f'repos/{repo}/actions/workflows/{workflow}/runs?per_page=100')
    return [r for r in datos['workflow_runs'] if inicio <= fecha(r['created_at']) < fin]


def ultimo_artefacto(repo, campana):
    datos = api(f'repos/{repo}/actions/artifacts?per_page=100')
    candidatos = [a for a in datos['artifacts']
                  if a['name'].startswith(f'logs-{campana}-') and not a['expired']]
    return max(candidatos, key=lambda a: a['created_at']) if candidatos else None


def descargar(repo, artefacto, destino):
    destino.mkdir(parents=True, exist_ok=True)
    gh('run', 'download', str(artefacto['workflow_run']['id']), '--repo', repo, '-n', artefacto['name'],
       '-D', str(destino))
    return destino


def leer_json(ruta):
    return json.loads(ruta.read_text(encoding='utf-8')) if ruta.is_file() else None


def productos_por_categoria(log):
    """{url: (productos, productos de la última página)} a partir de las líneas «Página N: V válidos»."""
    paginas, actual = {}, None
    if not log.is_file():
        return {}
    for linea in log.open(encoding='utf-8', errors='replace'):
        m = re.match(r'Categoría \d+: (\S+); desde página', linea)
        if m:
            actual = m[1]
            paginas.setdefault(actual, {})
            continue
        m = re.match(r'\s+Página (\d+): (\d+) válidos', linea)
        if m and actual:
            paginas[actual][int(m[1])] = int(m[2])
    return {url: (sum(p.values()), p[max(p)] if p else 0) for url, p in paginas.items()}


def nombre_corto(url):
    return re.sub(r'^https?://[^/]+/(categoria|catalogo)/', '', url)[:70]


def revisar_proveedor(proveedor, actual, anterior, alertas, avisos):
    """actual/anterior: carpetas de artefactos descargados (anterior puede ser None)."""
    res = leer_json(actual / proveedor / 'resumen.json')
    pipe = (leer_json(actual / 'pipeline.json') or {}).get('resultados', {}).get(proveedor, {})
    if not res:
        alertas.append(f'{proveedor}: no hay resumen.json en el artefacto de la campaña.')
        return
    print(f"  {proveedor.capitalize():7} {res['estado']}, {res['categorias_completas']}/{res['categorias_totales']} categorías, "
          f"{res['productos_validos']} productos, {res['registros_por_revisar']} por revisar | "
          f"importación: {pipe.get('importacion', '?')} ({pipe.get('importado_en', '-')[:16]})")
    if res['pendientes']:
        alertas.append(f"{proveedor}: {len(res['pendientes'])} categorías pendientes, p. ej. "
                       + ', '.join(nombre_corto(p['url']) for p in res['pendientes'][:3]) + '.')
    if pipe.get('importacion') != 'guardada':
        alertas.append(f"{proveedor}: no está importado a Supabase (estado: {pipe.get('importacion', 'sin dato')}).")
    errores = 0
    log = actual / f'{proveedor}.log'
    if log.is_file():
        errores = sum(1 for l in log.open(encoding='utf-8', errors='replace') if 'Traceback' in l)
    if errores:
        alertas.append(f'{proveedor}: el log del scraper tiene {errores} Traceback.')
    if not anterior:
        avisos.append(f'{proveedor}: no hay artefacto de la campaña anterior; no se pudo comparar.')
        return
    previo = leer_json(anterior / proveedor / 'resumen.json')
    if previo and previo['productos_validos']:
        cambio = (res['productos_validos'] - previo['productos_validos']) / previo['productos_validos']
        print(f"          vs semana anterior: {previo['productos_validos']} -> {res['productos_validos']} ({cambio:+.1%})")
        if cambio < -UMBRAL_CAIDA_TOTAL:
            alertas.append(f"{proveedor}: el total de productos cayó {cambio:.1%} frente a la semana anterior.")
        extra = res['registros_por_revisar'] - previo['registros_por_revisar']
        if extra >= SALTO_POR_REVISAR:
            avisos.append(f"{proveedor}: los registros por revisar subieron de {previo['registros_por_revisar']} "
                          f"a {res['registros_por_revisar']}.")
    ahora = productos_por_categoria(actual / f'{proveedor}.log')
    antes = productos_por_categoria(anterior / f'{proveedor}.log')
    por_pagina = PRODUCTOS_POR_PAGINA[proveedor]
    a_cero, cortes, caidas = [], [], []
    for url, (n_antes, _) in antes.items():
        if url not in ahora:
            continue
        n_ahora, ultima = ahora[url]
        if n_antes >= MIN_PRODUCTOS_CATEGORIA and n_ahora == 0:
            a_cero.append(f'{nombre_corto(url)} ({n_antes} -> 0)')
        elif n_antes - n_ahora >= MIN_CAIDA_MITAD and n_ahora <= n_antes * 0.5:
            caidas.append(f'{nombre_corto(url)} ({n_antes} -> {n_ahora})')
        elif n_antes - n_ahora >= MIN_CAIDA_CORTE and ultima == por_pagina:
            cortes.append(f'{nombre_corto(url)} ({n_antes} -> {n_ahora})')
    if a_cero:
        alertas.append(f'{proveedor}: categorías que pasaron a 0 productos (posible falso vacío): ' + '; '.join(a_cero))
    if caidas:
        alertas.append(f'{proveedor}: categorías que perdieron la mitad o más: ' + '; '.join(caidas))
    if cortes:
        alertas.append(f'{proveedor}: posibles cortes de paginación (última página completa y menos productos): '
                       + '; '.join(cortes))


def revisar_entradas(repo, dias, alertas, avisos):
    try:
        corridas = api(f'repos/{repo}/actions/runs?per_page=30')['workflow_runs']
    except RuntimeError as exc:
        avisos.append(f'No pude leer las importaciones de listas ({repo}): {exc}')
        return
    desde = datetime.now(timezone.utc) - timedelta(days=dias)
    recientes = [r for r in corridas if fecha(r['created_at']) >= desde]
    print(f'  Importaciones de listas de proveedores ({repo}, últimos {dias} días): {len(recientes)}')
    for r in recientes:
        estado = r['conclusion'] or r['status']
        print(f"    #{r['run_number']} {r['created_at'][:16]} {r['event']:<17} {estado}")
        if r['conclusion'] not in ('success', None):
            alertas.append(f"Importación de lista fallida: #{r['run_number']} ({r['html_url']}). El Excel se conserva en el repositorio.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--campana', help='catalogo-AAAA-WNN; por defecto la semana actual en El Salvador')
    parser.add_argument('--repo', default='TheRealNJ33/catalogo-precios')
    parser.add_argument('--repo-entradas', default='TheRealNJ33/catalogo-entradas')
    parser.add_argument('--workflow', default='catalogo.yml')
    args = parser.parse_args()
    campana = args.campana or id_campana(datetime.now(SALVADOR))
    previa = campana_anterior(campana)
    alertas, avisos = [], []
    print(f'REVISIÓN DE LA CAMPAÑA {campana} (anterior: {previa})  {datetime.now(SALVADOR):%Y-%m-%d %H:%M} hora de El Salvador')

    print('\n1. Ejecuciones de la semana')
    corridas = corridas_de_la_semana(args.repo, args.workflow, campana)
    if not corridas:
        alertas.append(f'No hay ninguna corrida de {args.workflow} en la semana de {campana}.')
    else:
        por_estado = {}
        for r in corridas:
            clave = r['conclusion'] or r['status']
            por_estado[clave] = por_estado.get(clave, 0) + 1
        print(f"  {len(corridas)} corridas: " + ', '.join(f'{n} {k}' for k, n in sorted(por_estado.items())))
        largas = sorted(corridas, key=lambda r: (fecha(r['updated_at']) - fecha(r['created_at'])), reverse=True)[:3]
        print('  más largas: ' + ', '.join(f"#{r['run_number']} ({int((fecha(r['updated_at']) - fecha(r['created_at'])).total_seconds() // 60)} min)" for r in largas))
        for r in corridas:
            if r['status'] == 'completed' and r['conclusion'] != 'success':
                alertas.append(f"Corrida #{r['run_number']} terminó en {r['conclusion']}: {r['html_url']}")
        for r in corridas:
            if r['status'] != 'completed':
                avisos.append(f"Corrida #{r['run_number']} sigue {r['status']} ({r['html_url']}).")
        ramas = sorted({r['head_branch'] for r in corridas if r['head_branch'] != 'main'})
        if ramas:
            avisos.append('Hubo corridas manuales desde ramas distintas de main: ' + ', '.join(ramas) + '.')

    print('\n2. Estado de la campaña y comparación con la anterior')
    artefacto = ultimo_artefacto(args.repo, campana)
    if not artefacto:
        alertas.append(f'No encuentro artefactos de logs de {campana} (¿caducaron o no hubo corridas?).')
    else:
        with tempfile.TemporaryDirectory(prefix='revision_') as tmp:
            raiz = Path(tmp)
            actual = descargar(args.repo, artefacto, raiz / 'actual')
            artefacto_previo = ultimo_artefacto(args.repo, previa)
            anterior = descargar(args.repo, artefacto_previo, raiz / 'anterior') if artefacto_previo else None
            pipe = leer_json(actual / 'pipeline.json') or {}
            print(f"  artefacto: {artefacto['name']} ({artefacto['created_at'][:16]}) | campaña completa: {pipe.get('campana_completa')}")
            if not pipe.get('campana_completa'):
                alertas.append(f'La campaña {campana} aún no está completa.')
            for proveedor in ('vidri', 'freund'):
                revisar_proveedor(proveedor, actual, anterior, alertas, avisos)

    print('\n3. Listas de proveedores')
    revisar_entradas(args.repo_entradas, 7, alertas, avisos)

    print('\n' + '=' * 70)
    if alertas:
        print(f'VEREDICTO: REVISAR ({len(alertas)} alerta{"s" if len(alertas) != 1 else ""})')
        for a in alertas:
            print(f'  [ALERTA] {a}')
    else:
        print('VEREDICTO: TODO BIEN')
    for a in avisos:
        print(f'  [aviso] {a}')
    return 1 if alertas else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, subprocess.TimeoutExpired, KeyError, ValueError) as exc:
        print(f'ERROR: no pude completar la revisión: {exc}', file=sys.stderr)
        sys.exit(2)

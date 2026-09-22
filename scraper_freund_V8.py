"""
Scraper de Freund con recuperación por página y salida compatible con Supabase.

Mantiene las categorías, selectores, configuración del navegador, limpieza de
precios y pausas de la versión suministrada por el usuario. La capa común
(ejecucion_persistente.py) guarda avances y evita ejecuciones superpuestas.

Salida CSV: proveedor;nombre;sku;precio;fecha_consulta.
Fecha DD-MM-AAAA en horario de El Salvador (UTC-6).
Consulte LEEME.md para requisitos, comandos, reanudación e instalación en VPS.

No actualice las dependencias sin capturar primero las versiones que funcionan:
    python capturar_versiones.py
"""

import asyncio
import json
import re
import time
import sys
from datetime import datetime, timedelta, timezone
import pandas as pd
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
from crawl4ai import JsonCssExtractionStrategy

PROVEEDOR = "Freund"

# ---------------------------------------------------------------------
# 1. URLs BASE de categoría a scrapear (sin parámetros de página)
# ---------------------------------------------------------------------
CATEGORY_URLS = [
    "https://www.freundferreteria.com/categoria/ARTICULOS-PROMOCIONALES/productos/NVL3-494",
    "https://www.freundferreteria.com/categoria/CERTIFICADOS-Y-TARJETAS-DE-REGALOS/productos/NVL3-496",
    "https://www.freundferreteria.com/categoria/DELIMITACION-Y-SENALIZACION/productos/NVL3-1435",
    "https://www.freundferreteria.com/categoria/EXTINTORES/productos/NVL3-1433",
    "https://www.freundferreteria.com/categoria/SENALIZACION-Y-SEGURIDAD-VIAL/productos/NVL3-1434",
    "https://www.freundferreteria.com/categoria/EQUIPOS-PARA-TRABAJOS-CONFINADOS/productos/NVL3-1432",
    "https://www.freundferreteria.com/categoria/EQUIPOS-PARA-TRABAJOS-EN-ALTURA/productos/NVL3-1431",
    "https://www.freundferreteria.com/categoria/CALZADO-DE-SEGURIDAD/productos/NVL3-1430",
    "https://www.freundferreteria.com/categoria/CAPAS-TRAJES-CHALECOS-DE-SEGURIDAD/productos/NVL3-1429",
    "https://www.freundferreteria.com/categoria/CASCOS-Y-PROTECCION-CABEZA/productos/NVL3-76",
    "https://www.freundferreteria.com/categoria/PRIMEROS-AUXILIOS/productos/NVL3-1522",
    "https://www.freundferreteria.com/categoria/PROTECCION-AUDITIVA/productos/NVL3-1427",
    "https://www.freundferreteria.com/categoria/PROTECCION-DE-MANOS/productos/NVL3-1428",
    "https://www.freundferreteria.com/categoria/PROTECCION-FACIAL/productos/NVL3-1426",
    "https://www.freundferreteria.com/categoria/PROTECCION-RESPIRATORIA/productos/NVL3-1424",
    "https://www.freundferreteria.com/categoria/PROTECCION-VISUAL/productos/NVL3-1425",
    "https://www.freundferreteria.com/categoria/BLOQUE-DE-VIDRIO/productos/NVL3-340",
    "https://www.freundferreteria.com/categoria/MOLDURAS-Y-ESPACIADORES/productos/NVL3-342",
    "https://www.freundferreteria.com/categoria/PISOS-CERAMICA/productos/NVL3-2422",
    "https://www.freundferreteria.com/categoria/PISOS-PARA-EXTERIOR/productos/NVL3-1622",
    "https://www.freundferreteria.com/categoria/PISOS-PORCELANATO/productos/NVL3-2423",
    "https://www.freundferreteria.com/categoria/PISOS-SPC-Y-WPC/productos/NVL3-2424",
    "https://www.freundferreteria.com/categoria/PISOS-FACHALETAS-Y-BALDOSAS-PARA-EXTERIOR/productos/NVL3-341",
    "https://www.freundferreteria.com/categoria/AZULEJOS/productos/NVL3-2428",
    "https://www.freundferreteria.com/categoria/FACHALETAS/productos/NVL3-2426",
    "https://www.freundferreteria.com/categoria/LAMINAS-DECORATIVAS/productos/NVL3-2427",
    "https://www.freundferreteria.com/categoria/PANEL-DECORATIVO-Y-FACHALETAS/productos/NVL3-1222",
    "https://www.freundferreteria.com/categoria/ABRASIVOS/productos/NVL3-228",
    "https://www.freundferreteria.com/categoria/HERRAMIENTAS-PARA-ABRASION/productos/NVL3-229",
    "https://www.freundferreteria.com/categoria/HERRAMIENTA-MEDICION-PINTURA/productos/NVL3-392",
    "https://www.freundferreteria.com/categoria/BROCHAS-Y-RODILLOS/productos/NVL3-241",
    "https://www.freundferreteria.com/categoria/ESCALERAS/productos/NVL3-242",
    "https://www.freundferreteria.com/categoria/ESPATULAS/productos/NVL3-240",
    "https://www.freundferreteria.com/categoria/WIPE-MULTI-USOS/productos/NVL3-243",
    "https://www.freundferreteria.com/categoria/CAJAS-Y-PLASTICOS-PARA-EMBALAJE/productos/NVL3-225",
    "https://www.freundferreteria.com/categoria/CINTAS-ADHESIVAS/productos/NVL3-223",
    "https://www.freundferreteria.com/categoria/COBERTORES/productos/NVL3-609",
    "https://www.freundferreteria.com/categoria/MOZOTES/productos/NVL3-396",
    "https://www.freundferreteria.com/categoria/PEGAMENTOS/productos/NVL3-226",
    "https://www.freundferreteria.com/categoria/ARTES-Y-MANUALIDADES/productos/NVL3-374",
    "https://www.freundferreteria.com/categoria/PAPEL-ADHESIVO/productos/NVL3-391",
    "https://www.freundferreteria.com/categoria/PINTURA-AEROSOL/productos/NVL3-566",
    "https://www.freundferreteria.com/categoria/PIZARRAS/productos/NVL3-389",
    "https://www.freundferreteria.com/categoria/ANTICOROSIVOS-INDUSTRIALES/productos/NVL3-394",
    "https://www.freundferreteria.com/categoria/ANTICORROSIVOS-RESIDENCIALES/productos/NVL3-576",
    "https://www.freundferreteria.com/categoria/ESMALTES-INDUSTRIALES/productos/NVL3-199",
    "https://www.freundferreteria.com/categoria/ESMALTES-RESIDENCIALES/productos/NVL3-574",
    "https://www.freundferreteria.com/categoria/REMOVEDORES-DE-PINTURA/productos/NVL3-395",
    "https://www.freundferreteria.com/categoria/SISTEMAS-PISOS-INDUSTRIALES-COMERCIAL-Y-RESIDENCIAL/productos/NVL3-575",
    "https://www.freundferreteria.com/categoria/BARNICES-PARA-MADERA/productos/NVL3-202",
    "https://www.freundferreteria.com/categoria/LACAS-PARA-MADERA/productos/NVL3-401",
    "https://www.freundferreteria.com/categoria/LIMPIADORES-REMOVEDORES-Y-RESTAURADORES-MADERA/productos/NVL3-203",
    "https://www.freundferreteria.com/categoria/SELLADORES-MASILLAS-Y-PRESERVANTES-MADERA/productos/NVL3-204",
    "https://www.freundferreteria.com/categoria/TINTES-PARA-MADERA/productos/NVL3-205",
    "https://www.freundferreteria.com/categoria/ACABADOS-ARQUITECTONICOS/productos/NVL3-193",
    "https://www.freundferreteria.com/categoria/ACONDICIONADORES-DE-SUPERFICIE-ARQUITECTONICO/productos/NVL3-316",
    "https://www.freundferreteria.com/categoria/IMPERMEABILIZANTES/productos/NVL3-397",
    "https://www.freundferreteria.com/categoria/PINTURAS-AREAS-ESPECIALES/productos/NVL3-569",
    "https://www.freundferreteria.com/categoria/SELLADORES-CONCRETO/productos/NVL3-318",
    "https://www.freundferreteria.com/categoria/ABRILLANTADORES-Y-PULIDORES-AUTOMOTRICES/productos/NVL3-213",
    "https://www.freundferreteria.com/categoria/ACABADOS-AUTOMOTRICES/productos/NVL3-400",
    "https://www.freundferreteria.com/categoria/PREPARADORES-SUPERFICIE-AUTOMOTRIZ/productos/NVL3-209",
    "https://www.freundferreteria.com/categoria/ESPUMAS-EXPANSIVAS/productos/NVL3-403",
    "https://www.freundferreteria.com/categoria/HERRAMIENTAS-PARA-MASILLAS-Y-SELLADORES/productos/NVL3-559",
    "https://www.freundferreteria.com/categoria/MASILLAS-RELLENADORAS/productos/NVL3-402",
    "https://www.freundferreteria.com/categoria/SELLADORES-Y-SILICONES/productos/NVL3-404",
    "https://www.freundferreteria.com/categoria/SOLVENTES-REDUCTORES/productos/NVL3-406",
    "https://www.freundferreteria.com/categoria/SOLVENTES-RETARDADORES/productos/NVL3-407",
    "https://www.freundferreteria.com/categoria/THINNERS/productos/NVL3-219",
    "https://www.freundferreteria.com/categoria/FOCO-FLUORESCENTES/productos/NVL3-541",
    "https://www.freundferreteria.com/categoria/FOCOS-INCANDESCENTES/productos/NVL3-438",
    "https://www.freundferreteria.com/categoria/FOCOS-LED/productos/NVL3-444",
    "https://www.freundferreteria.com/categoria/FOCOS-PARA-LINEA-BLANCA/productos/NVL3-568",
    "https://www.freundferreteria.com/categoria/TUBOS/productos/NVL3-540",
    "https://www.freundferreteria.com/categoria/LINTERNAS/productos/NVL3-88",
    "https://www.freundferreteria.com/categoria/PILAS/productos/NVL3-89",
    "https://www.freundferreteria.com/categoria/DIFUSORES/productos/NVL3-450",
    "https://www.freundferreteria.com/categoria/DRIVERS-Y-BALASTROS/productos/NVL3-448",
    "https://www.freundferreteria.com/categoria/ELECTRODOMESTICOS/productos/NVL3-583",
    "https://www.freundferreteria.com/categoria/ORGANIZADORES-COCINA/productos/NVL3-48",
    "https://www.freundferreteria.com/categoria/UTENSILIOS-COCINA/productos/NVL3-538",
    "https://www.freundferreteria.com/categoria/NAVIDAD/productos/NVL3-111",
    "https://www.freundferreteria.com/categoria/SOMBRILLAS-Y-PARAGUAS/productos/NVL3-113",
    "https://www.freundferreteria.com/categoria/SERVIDORES-Y-ACCESORIOS-MESA/productos/NVL3-417",
    "https://www.freundferreteria.com/categoria/UTENSILIOS-BAR/productos/NVL3-418",
    "https://www.freundferreteria.com/categoria/VAJILLAS-TAZAS-Y-CUBIERTOS/productos/NVL3-586",
    "https://www.freundferreteria.com/categoria/VASOS-PICHELES-Y-COPAS/productos/NVL3-587",
    "https://www.freundferreteria.com/categoria/BEBIDAS/productos/NVL3-584",
    "https://www.freundferreteria.com/categoria/GOLOSINAS/productos/NVL3-585",
    "https://www.freundferreteria.com/categoria/ALFOMBRAS/productos/NVL3-136",
    "https://www.freundferreteria.com/categoria/GALERIAS-Y-CORTINAS/productos/NVL3-137",
    "https://www.freundferreteria.com/categoria/ARTICULOS-LAVANDERIA/productos/NVL3-419",
    "https://www.freundferreteria.com/categoria/PLANCHADORES-GANCHOS-Y-PERCHEROS/productos/NVL3-421",
    "https://www.freundferreteria.com/categoria/PLANCHAS-Y-VAPORIZARES/productos/NVL3-590",
    "https://www.freundferreteria.com/categoria/ACCESORIOS-DECORACION/productos/NVL3-85",
    "https://www.freundferreteria.com/categoria/ALMOHADAS-Y-COJINES/productos/NVL3-558",
    "https://www.freundferreteria.com/categoria/CAMAS/productos/NVL3-556",
    "https://www.freundferreteria.com/categoria/CUIDADO-PERSONAL/productos/NVL3-618",
    "https://www.freundferreteria.com/categoria/ROPA-DE-CAMA/productos/NVL3-557",
    "https://www.freundferreteria.com/categoria/TOALLAS/productos/NVL3-619",
    "https://www.freundferreteria.com/categoria/BASUREROS-Y-DEPOSITOS-LIMPIEZA/productos/NVL3-237",
    "https://www.freundferreteria.com/categoria/HERRAMIENTAS-LIMPIEZA/productos/NVL3-236",
    "https://www.freundferreteria.com/categoria/HIGIENICOS-Y-DISPENSADORES/productos/NVL3-410",
    "https://www.freundferreteria.com/categoria/QUIMICOS-LIMPIEZA/productos/NVL3-591",
    "https://www.freundferreteria.com/categoria/CLOSETS/productos/NVL3-154",
    "https://www.freundferreteria.com/categoria/MUEBLES-MELAMINA/productos/NVL3-155",
    "https://www.freundferreteria.com/categoria/MUEBLES-METALICOS/productos/NVL3-589",
    "https://www.freundferreteria.com/categoria/MUEBLES-PLASTICOS/productos/NVL3-588",
    "https://www.freundferreteria.com/categoria/REPISAS-Y-ESCUADRAS/productos/NVL3-539",
    "https://www.freundferreteria.com/categoria/SOPORTES-TELEVISORES-Y-ENSERES/productos/NVL3-413",
    "https://www.freundferreteria.com/categoria/CAJAS-Y-ORGANIZADORES/productos/NVL3-151",
    "https://www.freundferreteria.com/categoria/ACCESORIOS-PARA-HERRAMIENTA-ROTATIVA/productos/NVL3-605",
    "https://www.freundferreteria.com/categoria/BROCAS/productos/NVL3-177",
    "https://www.freundferreteria.com/categoria/DISCOS-PARA-CORTE/productos/NVL3-174",
    "https://www.freundferreteria.com/categoria/DISCOS-PARA-DESBASTE-Y-PULIDO/productos/NVL3-175",
    "https://www.freundferreteria.com/categoria/PUNTAS-DESTORNILLADORAS/productos/NVL3-176",
    "https://www.freundferreteria.com/categoria/SIERRAS/productos/NVL3-1322",
    "https://www.freundferreteria.com/categoria/ACCESORIOS-Y-ORGANIZADORES/productos/NVL3-91",
    "https://www.freundferreteria.com/categoria/ACEITES-LUBRICANTES-Y-ADITIVOS/productos/NVL3-92",
    "https://www.freundferreteria.com/categoria/BICICLETAS-Y-ACCESORIOS/productos/NVL3-457",
    "https://www.freundferreteria.com/categoria/CARGADORES-Y-COMPRESORES-PARA-VEHICULOS/productos/NVL3-456",
    "https://www.freundferreteria.com/categoria/EMERGENCIA-Y-REPARACION-VEHICULOS/productos/NVL3-95",
    "https://www.freundferreteria.com/categoria/FORROS-Y-PROTECTORES-AUTO/productos/NVL3-93",
    "https://www.freundferreteria.com/categoria/LIMPIADORES-Y-AROMATIZANTES/productos/NVL3-94",
    "https://www.freundferreteria.com/categoria/MOTOCICLETAS-Y-ACCESORIOS/productos/NVL3-564",
    "https://www.freundferreteria.com/categoria/ASPIRADORAS-E-HIDROLAVADORAS/productos/NVL3-464",
    "https://www.freundferreteria.com/categoria/COMPRESORES-Y-ACCESORIOS/productos/NVL3-463",
    "https://www.freundferreteria.com/categoria/EMPAQUES-Y-SELLADORES-INDUSTRIALES/productos/NVL3-610",
    "https://www.freundferreteria.com/categoria/EQUIPOS-DE-CARGA-Y-ACCESORIOS/productos/NVL3-614",
    "https://www.freundferreteria.com/categoria/EQUIPOS-PARA-CONSTRUCCION/productos/NVL3-462",
    "https://www.freundferreteria.com/categoria/EQUIPOS-PARA-SOLDAR/productos/NVL3-68",
    "https://www.freundferreteria.com/categoria/PISTOLA-PARA-PINTAR-Y-ACCESORIOS/productos/NVL3-545",
    "https://www.freundferreteria.com/categoria/CARBONES-Y-BATERIAS-PARA-HERRAMIENTA-ELECTRICA/productos/NVL3-453",
    "https://www.freundferreteria.com/categoria/CEPILLOS-Y-ROUTERS-ALAMBRICOS/productos/NVL3-2125",
    "https://www.freundferreteria.com/categoria/DEMOLEDORES-Y-ROMPE-PAVIMENTO-ALAMBRICOS/productos/NVL3-2129",
    "https://www.freundferreteria.com/categoria/ESMERILES-Y-PULIDORAS/productos/NVL3-171",
    "https://www.freundferreteria.com/categoria/HERRAMIENTA-ROTATIVA-Y-PISTOLAS-DE-CALOR/productos/NVL3-172",
    "https://www.freundferreteria.com/categoria/HERRAMIENTAS-PARA-TABLA-ROCA/productos/NVL3-454",
    "https://www.freundferreteria.com/categoria/LIJADORAS-ALAMBRICOS/productos/NVL3-2126",
    "https://www.freundferreteria.com/categoria/PISTOLAS-DE-CALOR-ALAMBRICOS/productos/NVL3-2123",
    "https://www.freundferreteria.com/categoria/PROMOCIONES-HERRAMIENTAS-ELECTRICAS-ALAMBRICAS/productos/NVL3-2130",
    "https://www.freundferreteria.com/categoria/ROTOMARTILLOS-ALAMBRICOS/productos/NVL3-2128",
    "https://www.freundferreteria.com/categoria/SIERRAS-ALAMBRICAS/productos/NVL3-2124",
    "https://www.freundferreteria.com/categoria/SIERRAS-LIJADORAS-Y-CEPILLOS/productos/NVL3-170",
    "https://www.freundferreteria.com/categoria/SOPLADORAS-Y-ROCIADORAS-ALAMBRICAS/productos/NVL3-2322",
    "https://www.freundferreteria.com/categoria/TALADROS-ALAMBRICOS/productos/NVL3-2127",
    "https://www.freundferreteria.com/categoria/TALADROS-ROTOMARTILLOS-Y-DEMOLEDORES/productos/NVL3-169",
    "https://www.freundferreteria.com/categoria/TRONZADORAS-TALADROS-BANCO-SIERRAS-MESA-ALAMBRICOS/productos/NVL3-2122",
    "https://www.freundferreteria.com/categoria/ATORNILLADORES-IMPACTO-INALAMBRICOS/productos/NVL3-2022",
    "https://www.freundferreteria.com/categoria/CARGADORES-Y-BATERIAS/productos/NVL3-2027",
    "https://www.freundferreteria.com/categoria/CEPILLOS-Y-ROUTERS-INALAMBRICOS/productos/NVL3-2037",
    "https://www.freundferreteria.com/categoria/CLAVADORAS-Y-ENGRAPADORAS-INALAMBRICAS/productos/NVL3-2036",
    "https://www.freundferreteria.com/categoria/DEMOLEDORES-INALAMBRICOS/productos/NVL3-2026",
    "https://www.freundferreteria.com/categoria/ESMERILES-Y-PULIDORAS-INALAMBRICAS/productos/NVL3-2028",
    "https://www.freundferreteria.com/categoria/HERRAMIENTAS-ESPECIALES-INALAMBRICAS/productos/NVL3-2038",
    "https://www.freundferreteria.com/categoria/LAMPARAS-DE-TRABAJO-Y-PARLANTES-INALAMBRICOS/productos/NVL3-2034",
    "https://www.freundferreteria.com/categoria/LAVADORAS-PRESION-INALAMBRICAS/productos/NVL3-2032",
    "https://www.freundferreteria.com/categoria/LIJADORAS-INALAMBRICAS/productos/NVL3-2039",
    "https://www.freundferreteria.com/categoria/LLAVES-IMPACTO-Y-RACHET-INALAMBRICOS/productos/NVL3-2023",
    "https://www.freundferreteria.com/categoria/PISTOLAS-DE-CALOR-INALAMBRICAS/productos/NVL3-2030",
    "https://www.freundferreteria.com/categoria/PROMOCIONES-INALAMBRICAS/productos/NVL3-2035",
    "https://www.freundferreteria.com/categoria/ROTATIVOS-DETALLE-Y-RECTIFICADORES-INALAMBRICOS/productos/NVL3-2033",
    "https://www.freundferreteria.com/categoria/ROTOMARTILLOS-INALAMBRICOS/productos/NVL3-2025",
    "https://www.freundferreteria.com/categoria/SIERRAS-INALAMBRICAS/productos/NVL3-2029",
    "https://www.freundferreteria.com/categoria/SOPLADORAS-Y-ROCIADORAS-INALAMBRICAS/productos/NVL3-2031",
    "https://www.freundferreteria.com/categoria/TALADROS-INALAMBRICOS/productos/NVL3-2024",
    "https://www.freundferreteria.com/categoria/CAJAS-DE-HERRAMIENTAS-Y-ORGANIZADORES/productos/NVL3-65",
    "https://www.freundferreteria.com/categoria/CEPILLOS-Y-LIMAS/productos/NVL3-60",
    "https://www.freundferreteria.com/categoria/CINTAS-METRICAS-NIVELES-Y-MEDIDORES/productos/NVL3-63",
    "https://www.freundferreteria.com/categoria/CUCHILLAS-ALICATES-Y-SIERRAS/productos/NVL3-59",
    "https://www.freundferreteria.com/categoria/DESTORNIILLADORES-CUBOS-Y-LLAVES/productos/NVL3-61",
    "https://www.freundferreteria.com/categoria/HERRAMIENTA-EXTRACCION/productos/NVL3-461",
    "https://www.freundferreteria.com/categoria/HERRAMIENTA-MULTI-FUNCION/productos/NVL3-460",
    "https://www.freundferreteria.com/categoria/HERRAMIENTA-PARA-DOBLAR/productos/NVL3-56",
    "https://www.freundferreteria.com/categoria/HERRAMIENTAS-TABLA-ROCA/productos/NVL3-459",
    "https://www.freundferreteria.com/categoria/MACHUELOS-Y-TARRAJAS/productos/NVL3-64",
    "https://www.freundferreteria.com/categoria/MARTILLO-CINCELES-Y-ALMADANAS/productos/NVL3-62",
    "https://www.freundferreteria.com/categoria/PALAS-PIOCHAS-Y-CARRETILLAS/productos/NVL3-458",
    "https://www.freundferreteria.com/categoria/CADENAS-Y-CABLES/productos/NVL3-115",
    "https://www.freundferreteria.com/categoria/CAJAS-FUERTES-Y-CAJAS-DE-VALORES/productos/NVL3-606",
    "https://www.freundferreteria.com/categoria/CANDADOS/productos/NVL3-116",
    "https://www.freundferreteria.com/categoria/CERRADURAS/productos/NVL3-348",
    "https://www.freundferreteria.com/categoria/GANCHOS/productos/NVL3-343",
    "https://www.freundferreteria.com/categoria/HERRAJES-MUEBLES/productos/NVL3-117",
    "https://www.freundferreteria.com/categoria/HERRAJES-PUERTAS/productos/NVL3-118",
    "https://www.freundferreteria.com/categoria/LETRAS-Y-NUMEROS/productos/NVL3-119",
    "https://www.freundferreteria.com/categoria/LLAVES-Y-ACCESORIOS/productos/NVL3-607",
    "https://www.freundferreteria.com/categoria/ALEROS/productos/NVL3-613",
    "https://www.freundferreteria.com/categoria/HIERRO-FORJADO/productos/NVL3-145",
    "https://www.freundferreteria.com/categoria/MOLDURAS-Y-ZOCALOS/productos/NVL3-350",
    "https://www.freundferreteria.com/categoria/PUERTAS-Y-COMPLEMENTOS/productos/NVL3-146",
    "https://www.freundferreteria.com/categoria/ANCLAS/productos/NVL3-30",
    "https://www.freundferreteria.com/categoria/ARGOLLAS-Y-GANCHOS/productos/NVL3-31",
    "https://www.freundferreteria.com/categoria/CLAVOS/productos/NVL3-32",
    "https://www.freundferreteria.com/categoria/IMANES/productos/NVL3-307",
    "https://www.freundferreteria.com/categoria/PERNOS-ARANDELAS-Y-TUERCAS/productos/NVL3-277",
    "https://www.freundferreteria.com/categoria/REMACHES-TACHUELAS-Y-CHAVETAS/productos/NVL3-33",
    "https://www.freundferreteria.com/categoria/RESORTES/productos/NVL3-34",
    "https://www.freundferreteria.com/categoria/TORNILLOS/productos/NVL3-35",
    "https://www.freundferreteria.com/categoria/VENTANAS/productos/NVL3-565",
    "https://www.freundferreteria.com/categoria/VIDRIOS-OPERADORES-Y-ACCESORIOS-VENTANAS/productos/NVL3-608",
    "https://www.freundferreteria.com/categoria/BOMBAS-DE-AGUA-Y-REPUESTOS/productos/NVL3-5",
    "https://www.freundferreteria.com/categoria/CALENTADORES-AGUA-Y-DUCHAS-ELECTRICAS/productos/NVL3-326",
    "https://www.freundferreteria.com/categoria/ABASTO-DRENAJE-Y-DESTAPADORES/productos/NVL3-4",
    "https://www.freundferreteria.com/categoria/ACCESORIOS-CONTRA-INCENDIO/productos/NVL3-563",
    "https://www.freundferreteria.com/categoria/ACCESORIOS-PARA-TUBO-GALVANIZADO-Y-PVC/productos/NVL3-475",
    "https://www.freundferreteria.com/categoria/CANALETAS-PVC/productos/NVL3-477",
    "https://www.freundferreteria.com/categoria/EXTRACTORES-DE-AIRE-PARA-BANO/productos/NVL3-327",
    "https://www.freundferreteria.com/categoria/FREGADEROS-Y-FILTROS-DE-AGUA/productos/NVL3-3",
    "https://www.freundferreteria.com/categoria/MANTENIMIENTO-PISCINA/productos/NVL3-8",
    "https://www.freundferreteria.com/categoria/PEGAMENTOS-Y-SELLADORES-PARA-TUBERIA/productos/NVL3-10",
    "https://www.freundferreteria.com/categoria/REPUESTOS-LOZA-SANITARIA-Y-GRIFERIA/productos/NVL3-7",
    "https://www.freundferreteria.com/categoria/TANQUES-DE-AGUA-CISTERNAS-PILAS-Y-FOSAS-SEPTICAS/productos/NVL3-345",
    "https://www.freundferreteria.com/categoria/TUBERIA-PVC-Y-COBRE/productos/NVL3-476",
    "https://www.freundferreteria.com/categoria/VALVULAS-Y-LLAVES-DE-CHORRO/productos/NVL3-9",
    "https://www.freundferreteria.com/categoria/BANERAS-E-HIDROMASAJES/productos/NVL3-331",
    "https://www.freundferreteria.com/categoria/INODOROS/productos/NVL3-332",
    "https://www.freundferreteria.com/categoria/LAVAMANOS/productos/NVL3-822",
    "https://www.freundferreteria.com/categoria/LOZA-SANITARIA-INSTITUCIONAL/productos/NVL3-333",
    "https://www.freundferreteria.com/categoria/PUERTAS-Y-DIVISIONES-DE-BANO/productos/NVL3-330",
    "https://www.freundferreteria.com/categoria/GABINETES-DE-BANO/productos/NVL3-923",
    "https://www.freundferreteria.com/categoria/MUEBLES-DE-BANO/productos/NVL3-922",
    "https://www.freundferreteria.com/categoria/MUEBLES-PARA-FREGADEROS/productos/NVL3-1022",
    "https://www.freundferreteria.com/categoria/ACCESORIOS-CONDUCTORES-ELECTRICOS/productos/NVL3-432",
    "https://www.freundferreteria.com/categoria/ACCESORIOS-TUBERIA-ELECTRICA/productos/NVL3-431",
    "https://www.freundferreteria.com/categoria/CANALIZACION-Y-CABLEADO-ESTRUCTURADO/productos/NVL3-430",
    "https://www.freundferreteria.com/categoria/CONDUCTORES-ELECTRICOS/productos/NVL3-148",
    "https://www.freundferreteria.com/categoria/TUBERIA-Y-DUCTOS-CABLEADO-ELECTRICO/productos/NVL3-149",
    "https://www.freundferreteria.com/categoria/ACCESORIOS-COMPUTADORA/productos/NVL3-435",
    "https://www.freundferreteria.com/categoria/CABLEADO-ESTRUCTURADO/productos/NVL3-434",
    "https://www.freundferreteria.com/categoria/EXTENSIONES-Y-PROTECTORES-VOLTAJE/productos/NVL3-128",
    "https://www.freundferreteria.com/categoria/HERRAMIENTAS-ACCESORIOS-ELECTRICIDAD-Y-ELECTRONICA/productos/NVL3-129",
    "https://www.freundferreteria.com/categoria/PROCTECTORES-Y-LUBRICANTES-PARA-MOTORES/productos/NVL3-528",
    "https://www.freundferreteria.com/categoria/REPUESTOS-COCINA/productos/NVL3-131",
    "https://www.freundferreteria.com/categoria/SISTEMAS-DE-SEGURIDAD/productos/NVL3-542",
    "https://www.freundferreteria.com/categoria/VIDEO-Y-AUDIO/productos/NVL3-130",
    "https://www.freundferreteria.com/categoria/CAJAS-DE-CONEXION-Y-RECEPTACULOS/productos/NVL3-580",
    "https://www.freundferreteria.com/categoria/CAPACITORES-Y-FUSIBLES/productos/NVL3-485",
    "https://www.freundferreteria.com/categoria/CENTROS-DE-CARGA-Y-SWITCH-DE-PROTECCION/productos/NVL3-581",
    "https://www.freundferreteria.com/categoria/CONTROL-Y-AUTOMATIZACION/productos/NVL3-426",
    "https://www.freundferreteria.com/categoria/MATERIAL-ELECTRICO-BAJO-VOLTAJE/productos/NVL3-582",
    "https://www.freundferreteria.com/categoria/MATERIAL-ELECTRICO-MEDIA-TENSION/productos/NVL3-1122",
    "https://www.freundferreteria.com/categoria/TOMAS-INDUSTRIALES/productos/NVL3-427",
    "https://www.freundferreteria.com/categoria/CONTROLADORES-ELECTRICOS/productos/NVL3-424",
    "https://www.freundferreteria.com/categoria/PLACAS-Y-ACCESORIOS/productos/NVL3-124",
    "https://www.freundferreteria.com/categoria/SISTEMAS-DE-SEGURIDAD/productos/NVL3-578",
    "https://www.freundferreteria.com/categoria/ADHESIVOS-PARA-PISO/productos/NVL3-594",
    "https://www.freundferreteria.com/categoria/ADHESIVOS-Y-SELLADORES-PARA-CONCRETO/productos/NVL3-472",
    "https://www.freundferreteria.com/categoria/ADITIVOS-PARA-CEMENTO/productos/NVL3-471",
    "https://www.freundferreteria.com/categoria/MEZCLAS-Y-REPELLOS/productos/NVL3-26",
    "https://www.freundferreteria.com/categoria/ALAMBRES-AMARRE-ESPIGADO-Y-RAZOR/productos/NVL3-106",
    "https://www.freundferreteria.com/categoria/MALLAS/productos/NVL3-107",
    "https://www.freundferreteria.com/categoria/PLASTICO-NEGRO/productos/NVL3-108",
    "https://www.freundferreteria.com/categoria/ARENA-Y-GRAVA/productos/NVL3-533",
    "https://www.freundferreteria.com/categoria/LADRILLOS-Y-BLOQUES/productos/NVL3-469",
    "https://www.freundferreteria.com/categoria/CAL/productos/NVL3-536",
    "https://www.freundferreteria.com/categoria/CEMENTOS/productos/NVL3-535",
    "https://www.freundferreteria.com/categoria/HIERRO-CUADRADO-Y-ENTORCHADO/productos/NVL3-133",
    "https://www.freundferreteria.com/categoria/HIERRO-REDONDO-CORRUGADO-Y-LISO/productos/NVL3-599",
    "https://www.freundferreteria.com/categoria/LAMINAS-DE-FIBROCEMENTO/productos/NVL3-478",
    "https://www.freundferreteria.com/categoria/LAMINAS-FIBRA-DE-VIDRIO-Y-POLICARBONATO/productos/NVL3-601",
    "https://www.freundferreteria.com/categoria/LAMINAS-GALVANIZADAS-Y-ALUMINIO/productos/NVL3-602",
    "https://www.freundferreteria.com/categoria/LOSETAS-Y-DURAPAS/productos/NVL3-603",
    "https://www.freundferreteria.com/categoria/PASTAS/productos/NVL3-480",
    "https://www.freundferreteria.com/categoria/PERFILES-PAREDES-Y-ENCIELADOS/productos/NVL3-479",
    "https://www.freundferreteria.com/categoria/TABLA-DE-YESO/productos/NVL3-600",
    "https://www.freundferreteria.com/categoria/LAMINA-DESPLEGADA-Y-LAGRIMADA/productos/NVL3-466",
    "https://www.freundferreteria.com/categoria/LAMINA-NEGRA/productos/NVL3-467",
    "https://www.freundferreteria.com/categoria/COMPLEMENTOS-TECHO/productos/NVL3-598",
    "https://www.freundferreteria.com/categoria/LAMINAS-FIBRA-DE-VIDRIO-Y-POLICARBONATO/productos/NVL3-597",
    "https://www.freundferreteria.com/categoria/LAMINAS-TECHO-FIBROCEMENTO/productos/NVL3-38",
    "https://www.freundferreteria.com/categoria/LAMINAS-TECHO-GALVANIZADAS-Y-ALUMINIO/productos/NVL3-595",
    "https://www.freundferreteria.com/categoria/TEJAS-Y-SHINGLE/productos/NVL3-596",
    "https://www.freundferreteria.com/categoria/FORMICA-Y-MELAMINA/productos/NVL3-158",
    "https://www.freundferreteria.com/categoria/MADERA-CEPILLADA/productos/NVL3-468",
    "https://www.freundferreteria.com/categoria/MADERA-CONSTRUCCION/productos/NVL3-159",
    "https://www.freundferreteria.com/categoria/MADERA-PLYWOOD/productos/NVL3-161",
    "https://www.freundferreteria.com/categoria/MADERA-TRATADA/productos/NVL3-160",
    "https://www.freundferreteria.com/categoria/MADERAS-AGLOMERADAS/productos/NVL3-157",
    "https://www.freundferreteria.com/categoria/ANGULO-Y-PLATINA/productos/NVL3-43",
    "https://www.freundferreteria.com/categoria/CANERIA/productos/NVL3-465",
    "https://www.freundferreteria.com/categoria/POLIN-TUBO-Y-VIGAS/productos/NVL3-45",
    "https://www.freundferreteria.com/categoria/ACCESORIOS-DE-AIRES-ACONDICIONADOS/productos/NVL3-1924",
    "https://www.freundferreteria.com/categoria/EQUIPOS-DE-AIRES-ACONDICIONADOS/productos/NVL3-1923",
    "https://www.freundferreteria.com/categoria/AIRE-ACONDICIONADO/productos/NVL3-103",
    "https://www.freundferreteria.com/categoria/VENTILADORES/productos/NVL3-104",
    "https://www.freundferreteria.com/categoria/VENTILADORES-PORTATILES/productos/NVL3-1822",
    "https://www.freundferreteria.com/categoria/VENTILADORES-TECHO/productos/NVL3-1922",
    "https://www.freundferreteria.com/categoria/ACCESORIOS-BANO/productos/NVL3-17",
    "https://www.freundferreteria.com/categoria/ACCESORIOS-DE-SEGURIDAD-PARA-BANO/productos/NVL3-323",
    "https://www.freundferreteria.com/categoria/ASIENTOS-DE-INODORO/productos/NVL3-1722",
    "https://www.freundferreteria.com/categoria/BASCULAS-DE-BANO/productos/NVL3-324",
    "https://www.freundferreteria.com/categoria/CORTINAS-Y-ALFOMBRAS-DE-BANO/productos/NVL3-20",
    "https://www.freundferreteria.com/categoria/ESPEJOS/productos/NVL3-322",
    "https://www.freundferreteria.com/categoria/PORTAROLLO-TOALLEROS-Y-PERCHAS-PARA-BANO/productos/NVL3-18",
    "https://www.freundferreteria.com/categoria/PROMOCIONES-DECORACION-DE-BANO/productos/NVL3-516",
    "https://www.freundferreteria.com/categoria/SECADORES-MANO-Y-ACCESORIOS-INSTITUCIONAL/productos/NVL3-325",
    "https://www.freundferreteria.com/categoria/DUCHAS/productos/NVL3-303",
    "https://www.freundferreteria.com/categoria/GRIFERIA-INSTITUCIONAL/productos/NVL3-13",
    "https://www.freundferreteria.com/categoria/LLAVES-PARA-FREGADERO/productos/NVL3-2223",
    "https://www.freundferreteria.com/categoria/LLAVES-PARA-LAVAMANOS-Y-FREGADEROS/productos/NVL3-12",
    "https://www.freundferreteria.com/categoria/MEZCLADORES-DUCHAS-LAVAMANOS-Y-FREGADEROS/productos/NVL3-328",
    "https://www.freundferreteria.com/categoria/MEZCLADORES-FREGADERO/productos/NVL3-2222",
    "https://www.freundferreteria.com/categoria/BOMBAS-Y-ROCIADORES-JARDIN/productos/NVL3-51",
    "https://www.freundferreteria.com/categoria/EQUIPOS-AGRICOLA-Y-JARDIN/productos/NVL3-52",
    "https://www.freundferreteria.com/categoria/EQUIPOS-ELECTRICOS-AGRICOLA-Y-JARDIN/productos/NVL3-620",
    "https://www.freundferreteria.com/categoria/EQUIPOS-INALAMBRICOS-AGRICOLA-Y-JARDIN/productos/NVL3-617",
    "https://www.freundferreteria.com/categoria/EQUIPOS-MANUALES-AGRICOLA-Y-JARDIN/productos/NVL3-722",
    "https://www.freundferreteria.com/categoria/HERRAMIENTAS-MANUAL-JARDIN/productos/NVL3-53",
    "https://www.freundferreteria.com/categoria/RIEGO-JARDIN/productos/NVL3-54",
    "https://www.freundferreteria.com/categoria/CUBRESUELOS/productos/NVL3-337",
    "https://www.freundferreteria.com/categoria/ELIMINADORES-DE-PLAGAS/productos/NVL3-71",
    "https://www.freundferreteria.com/categoria/FERTILIZANTES/productos/NVL3-72",
    "https://www.freundferreteria.com/categoria/SEMILLAS-PARA-PLANTAS/productos/NVL3-338",
    "https://www.freundferreteria.com/categoria/ACCESORIOS-PARA-MASCOTAS/productos/NVL3-546",
    "https://www.freundferreteria.com/categoria/ALIMENTOS-PARA-MASCOTAS/productos/NVL3-547",
    "https://www.freundferreteria.com/categoria/DECORACION-JARDIN/productos/NVL3-97",
    "https://www.freundferreteria.com/categoria/MACETAS-CANASTAS-Y-ACCESORIOS/productos/NVL3-593",
    "https://www.freundferreteria.com/categoria/MUEBLES-JARDIN/productos/NVL3-98",
    "https://www.freundferreteria.com/categoria/BARBACOAS-Y-ACCESORIOS/productos/NVL3-79",
    "https://www.freundferreteria.com/categoria/HIELERAS-Y-TERMOS/productos/NVL3-80",
    "https://www.freundferreteria.com/categoria/INFLABLES/productos/NVL3-592",
    "https://www.freundferreteria.com/categoria/REPELENTES-Y-CAMPING/productos/NVL3-81",
    "https://www.freundferreteria.com/categoria/FLORES/productos/NVL3-266",
]

MAX_PAGINAS_POR_CATEGORIA = 50
PRODUCTOS_POR_PAGINA = 20  # Freund muestra 20 productos por página
MAX_INTENTOS_POR_PAGINA = 3
ESPERA_BASE_ENTRE_REINTENTOS = 4  # segundos; se multiplica por el número de intento
GUARDAR_CADA_N_CATEGORIAS = 20
ESPERA_ENTRE_CATEGORIAS = 5  # segundos
PAUSA_LARGA_CADA_N_CATEGORIAS = 50
PAUSA_LARGA_SEGUNDOS = 30

TEXTO_SIN_RESULTADOS = "No se encontraron resultados"

# ---------------------------------------------------------------------
# 2. Esquema de extracción: nombre, sku (código) y precio
# ---------------------------------------------------------------------
schema = {
    "name": "Productos Freund",
    "baseSelector": "div.product-item-box",
    "fields": [
        {"name": "Nombre", "selector": "h3.prod-name a", "type": "text"},
        {"name": "Codigo", "selector": "h3.prod-name a", "type": "attribute", "attribute": "data-product-id", "default": ""},
        {"name": "Precio", "selector": ".prod-price strong.price", "type": "text", "default": ""},
    ],
}

extraction_strategy = JsonCssExtractionStrategy(schema, verbose=False)

run_config = CrawlerRunConfig(
    cache_mode=CacheMode.BYPASS,
    extraction_strategy=extraction_strategy,
    # La página puede mantener solicitudes de fondo aun mostrando productos.
    # Esperar el contenido que necesita la extracción, no el silencio de red.
    wait_until="domcontentloaded",
    wait_for="""js:() => {
        const productos = Array.from(document.querySelectorAll('div.product-item-box'));
        if (productos.length > 0) {
            return productos.every(producto => {
                const enlace = producto.querySelector('h3.prod-name a');
                const precio = producto.querySelector('.prod-price strong.price');
                return Boolean(
                    enlace && enlace.textContent.trim() &&
                    (enlace.getAttribute('data-product-id') || '').trim() &&
                    precio && /[0-9]/.test(precio.textContent)
                );
            });
        }
        const texto = document.body ? document.body.innerText : '';
        return Boolean(document.querySelector('.ais-Hits--empty')) ||
            texto.includes('No se encontraron resultados');
    }""",
    delay_before_return_html=6.0,
    page_timeout=90000,
)

browser_config = BrowserConfig(headless=True, verbose=False, text_mode=True)

categorias_sin_productos = []
errores_paginas = []  # (url_categoria, numero_pagina, mensaje_error)


def limpiar_precio(texto):
    if not texto:
        return None
    solo_numeros = re.sub(r"[^\d.]", "", texto)
    try:
        return float(solo_numeros) if solo_numeros else None
    except ValueError:
        return None


def construir_url_pagina(url_base, pagina):
    return f"{url_base}?products-produccion%5Bpage%5D={pagina}"


def procesar_datos(data):
    fecha_consulta = datetime.now(timezone(timedelta(hours=-6))).strftime("%d-%m-%Y")
    for item in data:
        if item.get("Nombre"):
            item["Nombre"] = item["Nombre"].strip()
        item["Precio"] = limpiar_precio(item.get("Precio", ""))
        item["Proveedor"] = PROVEEDOR
        item["Fecha"] = fecha_consulta
    return data


def confirma_sin_resultados(html):
    if not html:
        return False
    return "ais-Hits--empty" in html or TEXTO_SIN_RESULTADOS in html


def guardar_resultados(todos_los_productos, es_final=False):
    if not todos_los_productos:
        return
    columnas = ["proveedor", "nombre", "sku", "precio", "fecha_consulta"]
    df = pd.DataFrame(todos_los_productos).rename(columns={
        "Proveedor": "proveedor", "Nombre": "nombre", "Codigo": "sku",
        "Precio": "precio", "Fecha": "fecha_consulta"})
    df = df.reindex(columns=columnas)
    df.drop_duplicates(subset=["sku"], inplace=True)
    df.to_excel("productos_freund.xlsx", index=False, engine="openpyxl")
    df.to_csv("productos_freund.csv", index=False, sep=";", encoding="utf-8-sig")
    etiqueta = "Guardado final" if es_final else "Guardado incremental"
    print(f"  [{etiqueta}] {len(df)} filas únicas en productos_freund.xlsx / .csv")


async def intentar_pagina(crawler, url, url_categoria, numero_pagina):
    for intento in range(1, MAX_INTENTOS_POR_PAGINA + 1):
        try:
            result = await crawler.arun(url=url, config=run_config)
        except Exception as e:
            mensaje = str(e)
            espera = ESPERA_BASE_ENTRE_REINTENTOS * intento
            print(f"    [Intento {intento}/{MAX_INTENTOS_POR_PAGINA}] Error real en página {numero_pagina}: {mensaje[:120]}")
            if intento < MAX_INTENTOS_POR_PAGINA:
                print(f"    Esperando {espera}s antes de reintentar...")
                await asyncio.sleep(espera)
                continue
            print("    -> Superado el límite de intentos, saltando a siguiente categoría.")
            errores_paginas.append((url_categoria, numero_pagina, mensaje[:200]))
            return [], "error"

        if not result.success or (getattr(result, "status_code", 200) or 200) >= 400:
            mensaje = result.error_message or f"HTTP {getattr(result, 'status_code', 'desconocido')}"
            espera = ESPERA_BASE_ENTRE_REINTENTOS * intento
            print(f"    [Intento {intento}/{MAX_INTENTOS_POR_PAGINA}] Error real en página {numero_pagina}: {mensaje[:120]}")
            if intento < MAX_INTENTOS_POR_PAGINA:
                print(f"    Esperando {espera}s antes de reintentar...")
                await asyncio.sleep(espera)
                continue
            print("    -> Superado el límite de intentos, saltando a siguiente categoría.")
            errores_paginas.append((url_categoria, numero_pagina, mensaje[:200]))
            return [], "error"

        try:
            data = json.loads(result.extracted_content)
        except (json.JSONDecodeError, TypeError):
            data = []

        if data:
            return procesar_datos(data), "ok"

        if confirma_sin_resultados(result.html):
            return [], "sin_productos"

        mensaje = "Sin productos y sin aviso de 'sin resultados' explícito (posible carga incompleta/antibot)."
        espera = ESPERA_BASE_ENTRE_REINTENTOS * intento
        print(f"    [Intento {intento}/{MAX_INTENTOS_POR_PAGINA}] {mensaje}")
        if intento < MAX_INTENTOS_POR_PAGINA:
            print(f"    Esperando {espera}s antes de reintentar...")
            await asyncio.sleep(espera)
            continue
        print("    -> Superado el límite de intentos, saltando a siguiente categoría.")
        errores_paginas.append((url_categoria, numero_pagina, mensaje))
        return [], "error"

    return [], "error"


async def scrape_categoria_completa(crawler, url_base):
    productos_categoria = []
    skus_vistos = set()

    for pagina in range(1, MAX_PAGINAS_POR_CATEGORIA + 1):
        url = construir_url_pagina(url_base, pagina)
        print(f"  Página {pagina}: {url}")
        productos, estado = await intentar_pagina(crawler, url, url_base, pagina)

        if estado == "sin_productos":
            if pagina == 1:
                print("    -> Categoría sin productos (confirmado por el sitio).")
                categorias_sin_productos.append(url_base)
            else:
                print(f"    -> Sin productos (confirmado). Fin de la categoría ({pagina - 1} páginas con datos).")
            break

        if estado == "error":
            break

        skus_pagina = {p.get("Codigo") for p in productos}
        if skus_pagina and skus_pagina.issubset(skus_vistos):
            print("    -> Productos repetidos de la página anterior. Fin de la categoría.")
            break

        skus_vistos |= skus_pagina
        print(f"    -> {len(productos)} productos encontrados")
        productos_categoria.extend(productos)

        if len(productos) < PRODUCTOS_POR_PAGINA:
            print(f"    -> Menos de {PRODUCTOS_POR_PAGINA} productos: última página de esta categoría.")
            break

        await asyncio.sleep(1.5)

    return productos_categoria


def main():
    from ejecucion_persistente import cli
    return cli(sys.modules[__name__])


if __name__ == "__main__":
    sys.exit(main())
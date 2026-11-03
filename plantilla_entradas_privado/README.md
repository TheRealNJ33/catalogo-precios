# catalogo-entradas (repositorio PRIVADO)

Aquí se depositan las listas de precios de proveedores en `.xlsx`. Al subir un archivo a
`entradas/<proveedor>/`, GitHub Actions lo convierte, simula la importación, la guarda en Supabase
y elimina el archivo. El código está en el repositorio público `catalogo-precios`.

- Carpeta por proveedor: `entradas/delpin/<archivo>.xlsx` (la carpeta se crea al subir el primer archivo). Cada proveedor necesita su entrada en `proveedores.json`.
- Secretos necesarios (Settings > Secrets and variables > Actions): `SUPABASE_DB_PASSWORD` y `SUPABASE_CA_CERT_B64`.
- Probar sin guardar nada: pestaña Actions > "Importar lista de proveedor" > Run workflow > modo `validar` (solo convierte) o `simular` (prueba también contra Supabase y revierte).
- Si algo falla, el `.xlsx` se conserva; se reintenta con Run workflow > modo `guardar`.
- Un archivo borrado sigue en el historial de este repositorio. Es privado; no lo vuelvas público.

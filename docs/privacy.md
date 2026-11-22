# Datos y publicación

El historial doméstico permanece en cada instalación. Este repositorio publica únicamente código,
configuración vacía y pruebas sintéticas. No hay archivos de ejemplo obtenidos de cuentas reales.

No añadir bases SQLite, exportaciones, informes, facturas, PDF, capturas de pantalla, trazas,
registros, sesiones, contraseñas, certificados privados, identificadores de dispositivos, dominios
privados de acceso remoto, coordenadas de la vivienda ni documentos sobre su funcionamiento real.
Las rutas de ejecución y los archivos de autorización se resuelven localmente y no se versionan.

`PUBLIC_FILES.txt` es una lista explícita de archivos revisados. El control de publicación rechaza
archivos versionados fuera de esa lista y patrones comunes de credenciales o ubicaciones privadas.
No reemplaza una revisión humana: una lectura real o un identificador de formato desconocido puede
parecer un dato ordinario. No copiar automáticamente archivos de una instalación al repositorio.

Para cambios posteriores, revisar `git diff --cached`, ejecutar las pruebas y el control de publicación.
La primera versión se preparó en una carpeta separada, sin importar el historial Git ni los archivos
de ejecución de la instalación privada.

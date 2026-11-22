# Datos y publicación

El historial doméstico permanece en cada instalación. Este repositorio publica únicamente código,
configuración vacía y pruebas sintéticas. No hay archivos de ejemplo obtenidos de cuentas reales.

No añadir bases SQLite, exportaciones, informes, facturas, PDF, capturas de pantalla, trazas,
registros, sesiones, contraseñas, certificados privados, identificadores de dispositivos, dominios
privados de acceso remoto, coordenadas de la vivienda ni documentos sobre su funcionamiento real.
Las rutas de ejecución y los archivos de autorización se resuelven localmente y no se versionan.

La demo utiliza habitaciones `Demo A` a `Demo D`, con identificadores 101–104 independientes de una
instalación. Sus lecturas se calculan a partir de fórmulas; no se extraen de bases privadas.

`PUBLIC_FILES.txt` es una lista explícita de archivos revisados. El control de publicación rechaza
archivos versionados fuera de esa lista y patrones comunes de credenciales o ubicaciones privadas.
No reemplaza una revisión humana: una lectura real o un identificador de formato desconocido puede
parecer un dato ordinario. No copiar automáticamente archivos de una instalación al repositorio.

El control también revisa autores, committers y mensajes del historial de la rama, además de la
identidad del siguiente commit. Usar un alias y el correo `noreply` de GitHub, y activar la privacidad
del correo para operaciones realizadas desde GitHub. CI añade un escaneo de secretos del historial
de ramas. Un resultado correcto no demuestra la retirada de objetos archivados en PR o cachés.

Para cambios posteriores, revisar `git diff --cached`, ejecutar las pruebas y el control de publicación.
La primera versión se preparó en una carpeta separada, sin importar el historial Git ni los archivos
de ejecución de la instalación privada.

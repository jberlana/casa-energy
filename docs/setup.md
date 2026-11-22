# Setup and operation

## Conectar una instalación propia

La vinculación inicial de cuentas y puentes no está automatizada. Cada usuario debe obtener sus
propias autorizaciones y mantenerlas fuera del repositorio. No es una integración oficial.

- **Tado X:** configurar `home_id`, `client_id` y `auth_path` en el archivo privado `config.json` del
  recolector. Este código puede renovar una autorización existente; no incluye el inicio OAuth.
  La configuración por defecto no contiene ningún identificador de vivienda ni cliente.
- **Hue:** el archivo privado `~/.config/tado-experiment/hue-auth.json` necesita `ip`, `host`,
  `username`, `certificate_pem`, `temperature_resource_id` y `connectivity_resource_id`.
  Se valida el certificado del puente emparejado.
- **Tiempo:** configurar `latitude` y `longitude` en el `config.json` privado del directorio de
  `TadoEnvironment`. No hay coordenadas preconfiguradas. La zona horaria del prototipo es
  `Europe/Madrid`; adaptarla de forma consistente si se utiliza en otra zona.
- **Octopus España:** cliente experimental de consultas y descarga de documentos. La sesión se
  almacena fuera del proyecto. El acceso antiguo con contraseña puede dejar de estar disponible;
  comprobar los métodos admitidos por el proveedor antes de utilizarlo. No generar claves ni
  modificar contratos desde el cliente. El modelo de consumo estructurado requiere validación
  frente a los documentos originales; no se considera automáticamente consumo medido.

La forma mínima de configuración se documenta en [examples/collector-config.example.json](../examples/collector-config.example.json).
Los valores vacíos deben sustituirse únicamente en un archivo local ignorado por Git.

## Servicios y límites

Los módulos de instalación de `scripts/macos/`
crean LaunchAgents del usuario. Revisarlos y configurar las fuentes antes de ejecutarlos.
`python3 -m scripts.macos.prepare_wake_install` prepara un instalador separado de despertares que requiere administrador;
no lo ejecuta automáticamente. Revisar los horarios de ejemplo para cada instalación.

El Mac necesita estar encendido y con sesión iniciada. La pantalla bloqueada no detiene los servicios;
la suspensión impide servir la web. No se garantiza despertar mediante Tailscale.

El recolector Tado limita peticiones y almacenamiento y conserva el historial ante errores.
La actualización desde la web tiene un máximo adicional de cuatro intentos diarios, enfriamiento,
reserva de cuota para las recogidas programadas y exclusión entre pestañas. Nunca envía órdenes
para cambiar temperaturas. Los recolectores son procesos separados de la API web.
Consultar [scripts/README.md](../scripts/README.md) para los comandos de instalación.

## Actualizar desde el menú

El menú de tres puntos de la barra superior permite actualizar Tado, Hue u Octopus por separado
en «Mi casa». En demo las acciones están desactivadas. El resultado se muestra dentro del menú;
al terminar se vuelven a consultar los datos locales sin cambiar el zoom ni la habitación.

- **Tado:** consulta aunque los datos estén recientes, pero conserva la pausa de 30 minutos, el
  máximo compartido de cuatro intentos diarios desde la web y la reserva para recogidas programadas.
  Los límites del proveedor y la exclusión con la calibración siguen vigentes.
- **Hue:** consulta solo el sensor emparejado, sin solicitar meteorología. Tiene una pausa de un
  minuto y comparte el bloqueo del recolector de entorno. Una lectura desconectada no es un éxito.
- **Octopus:** usa la sesión privada existente en `~/.config/octopus-experiment` (`OCTOPUS_ROOT`
  permite otra ubicación privada). Descubre una cuenta única y un ledger de gas inequívoco. Si hay
  varios, definir `account` y `ledger` en el `config.json` privado de ese directorio; nunca en Git.
  Las sesiones caducadas se renuevan solo si el proveedor permite hacerlo; no hay login en la web.

Octopus consulta una página de hasta 20 facturas y descarga hasta tres PDF pendientes por acción.
El catálogo privado indica si hay más histórico o documentos pendientes. Otra actualización continúa
los documentos de esa página sin duplicarlos. No se considera una importación completa del histórico.
Se conserva un catálogo mínimo y documentos en `catalog.json` y `documents/` dentro de su directorio
privado. No se guardan enlaces firmados. Cada ejecución limita las consultas GraphQL (incluida
autenticación) a 12, cada PDF a 8 MiB y el almacenamiento total a 64 MiB, sin borrar archivos al llenarse.
La pausa entre acciones de Octopus es de 15 minutos.

**Descargar una factura no verifica sus importes.** Las facturas verificadas que utiliza la gráfica
se conservan intactas; el menú indica que los documentos quedan pendientes de verificación. No se
modifican contratos, lecturas del contador, pagos ni consignas de calefacción.

Todas las acciones comparten un único trabajo con la actualización automática. El proceso manual
tiene un máximo de 190 segundos; los fallos y pausas se muestran sin exponer errores privados del
proveedor. Tras actualizar el código de una instalación, reinstalar el servicio web para copiar
también los nuevos módulos; no es necesario reinstalar los recolectores existentes.

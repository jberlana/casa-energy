# Casa Energy

Prototipo local para explorar el historial de calefacción: temperatura, objetivo, demanda conjunta,
contexto exterior y facturas de gas. React, TypeScript y Highcharts Stock sobre una API FastAPI y SQLite.

**Este repositorio contiene código y pruebas sintéticas. No incluye lecturas reales, facturas,
credenciales, identificadores de instalaciones ni direcciones privadas. La interfaz arranca en demo.**

## Qué permite

- Línea temporal continua con desplazamiento, pinch para zoom y consulta de lecturas.
- Sesiones calculadas como unión de demanda de habitaciones, sin duplicar solapamientos.
- Contexto meteorológico opcional mediante Open-Meteo y sensor local Hue.
- Facturas verificadas importadas localmente, con desglose entre consumo y cargos fijos.
- Actualización silenciosa de datos desfasados mientras la web está visible, con límites y bloqueo compartido.
- Acceso privado opcional por Tailscale; el servidor escucha en loopback.
- Posibles ventilaciones en la gráfica, con confirmaciones discretas y [calibración guiada](docs/ventilation.md).

Demanda no confirma llama ni mide consumo de gas. Los costes de la demo son ejemplos hipotéticos;
no son estimaciones calibradas para una vivienda. Los huecos y periodos incompletos se conservan.

## Inicio rápido

Python 3.11+ y Node.js 22+. Desde la raíz:

```sh
make setup
make demo
```

Abrir `http://127.0.0.1:8765`. La demo no necesita cuentas y utiliza `.local/` para su estado.
Si el puerto está ocupado, usar `TADO_WEB_PORT=8767 make demo`. El endpoint de salud del histórico
real requiere una base configurada; la demo funciona sin ella.

## Organización

| Directorio | Responsabilidad |
|---|---|
| [backend/](backend/README.md) | Recolectores, API local y cálculos |
| [web/](web/README.md) | Interfaz organizada por funcionalidades |
| [scripts/](scripts/README.md) | Publicación e instalación en macOS |
| [tests/](tests/README.md) | Pruebas con datos sintéticos |
| [examples/](examples/README.md) | Configuración vacía de ejemplo |
| [docs/](docs/README.md) | Arquitectura, operación y privacidad |

## Continuar el desarrollo

Leer [CONTRIBUTING.md](CONTRIBUTING.md), [AGENTS.md](AGENTS.md) y la
[arquitectura](docs/architecture.md). `make check` ejecuta pruebas y build; después de revisar y
preparar los archivos en Git, `make check-publication` comprueba el contenido del índice frente a
`PUBLIC_FILES.txt`. Solo se publican archivos revisados explícitamente.

La configuración de proveedores y los límites operativos están en [setup](docs/setup.md).
Los datos de una instalación nunca se trasladan al repositorio. Los cambios de código no actualizan
por sí solos los servicios ya instalados.

## Dependencias y alcance

Highcharts conserva sus créditos y tiene sus propios términos de licencia. Consultar la
[licencia de Highcharts](https://www.highcharts.com/license) antes de distribuir o explotar este
prototipo. Publicar este código no concede una licencia para sus dependencias comerciales.

No se incluye una licencia de software para este proyecto por ahora. Las API de los proveedores
pueden cambiar; no se garantiza compatibilidad ni disponibilidad futura.

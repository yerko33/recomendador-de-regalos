# RegalaCL — Recomendador Inteligente de Regalos en Chile

Aplicación web para recomendar regalos buscando en tiempo real en grandes tiendas de Chile (**Falabella, Ripley, Paris, Jumbo, Santa Isabel** y más) según intereses, presupuesto en CLP, destinatario, ocasión y referencias en lenguaje natural.

## Características principales

- **Scraping en vivo multi-tienda (Chile):** Extrae título, precio en pesos chilenos, enlace directo y foto oficial de cada tienda en paralelo.
- **Limpieza de lenguaje natural (español):** Interpreta frases como *"le gusta maquillarse"*, *"le gustan las parrilladas"* o *"le gusta preparar cosas dulces"* y filtra resultados irrelevantes (alimentos no regalables, juguetes infantiles para adultos, etc.).
- **Ranking Top 3 + Más sugerencias:** Muestra primero un podio con las 3 mejores opciones y luego alternativas adicionales ordenables por coincidencia, precio o evaluación.
- **Cuentas de usuario y favoritos (SQLite):** Registro e inicio de sesión con contraseñas cifradas (`PBKDF2`), guardado de regalos con notas personalizadas e historial de búsquedas.

## Cómo ejecutarlo localmente

No requiere instalar dependencias externas (usa únicamente la biblioteca estándar de Python 3).

```bash
python server.py
```

Luego abre en tu navegador: `http://localhost:8000`  
*(En Windows también puedes hacer doble clic en `iniciar_regalacl.bat`).*

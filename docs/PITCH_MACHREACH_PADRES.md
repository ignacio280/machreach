# Guión — Presentación de Machreach a Inversionistas (mamá y papá)

*Duración estimada: 8–10 minutos hablando tranquilo.*
*Monedas en CLP. Tipo de cambio referencial: 1 USD ≈ 950 CLP.*

---

## 1. Arranque (1 min)

> "Hola, gracias por escucharme. Quiero mostrarles algo en lo que llevo trabajando y que ya está en producción: **Machreach**. No les vengo a pedir plata de regalo — les vengo a proponer una inversión chica que, con números conservadores, se recupera en menos de un año."

## 2. Qué es Machreach (2 min)

> "Machreach es una plataforma web para universitarios. Se llama **Machreach Student**. Lo que hace:
>
> - **Se conecta a Canvas** (el LMS que usan casi todas las universidades) y baja automáticamente los cursos y materiales del alumno.
> - **Modo Focus** tipo Pomodoro: el alumno dice qué ramo y qué prueba está estudiando, le da start, y la plataforma le cuenta el tiempo.
> - **Analítica de estudio**: cuántas horas lleva esta semana, qué día rinde más, cuánto tiempo le ha dedicado a cada ramo y a cada examen — con gráficos de tiempo por día, por ramo y por prueba.
> - **Training**: quizzes compartidos entre alumnos de la misma universidad. Si alguien sube un cuestionario de Cálculo I en mi misma universidad, yo puedo practicar con él.
> - **Rankings (leaderboards)** por nivel global, país, universidad y carrera. Se cierran cada semana y cada mes con premios en monedas virtuales.
> - **XP, badges, rachas, ligas** — el sistema se siente como un juego. El alumno gana XP por estudiar y sube de liga.
> - **Monedas virtuales** para comprar banners y cosméticos de perfil.
> - **Suscripción PLUS** ($4.99/mes USD ≈ 4.700 CLP/mes) que da monedas extras cada mes y desbloquea cosméticos exclusivos.
> - **Paquetes de monedas** (compra única, desde $0.99 a $34.99 USD) para quienes quieran comprar cosméticos directo sin esperar.
>
> En resumen: **es una mezcla de Notion + Duolingo + Canvas, pensado para que estudiar no sea una lata.**"

## 3. Cómo funciona por dentro — explicación técnica simple (2 min)

> "Para que se imaginen lo que hay abajo:
>
> - **Backend** en Python con Flask — es el cerebro que maneja los usuarios, las sesiones de estudio, los ranking, y los pagos.
> - **Base de datos** PostgreSQL en la nube — ahí viven los usuarios, los cursos bajados de Canvas, el tiempo estudiado, el XP, los rankings.
> - **IA** — uso la API de OpenAI, específicamente el modelo `gpt-4o-mini` (la variante más barata, ~60 veces más económica que GPT-4 estándar). Sirve para tres cosas puntuales: **(1)** parsear material desordenado de Canvas (syllabus en PDF, programas de curso) y convertirlo en datos estructurados (nombres de pruebas, fechas, unidades); **(2)** generar **quizzes** a partir de ese material para el módulo Training; **(3)** generar **flashcards** para estudio. No hay tutor conversacional ni chat abierto — cada llamada a IA es una tarea corta, estructurada y con prompt optimizado, así que el costo por usuario es de centavos.
> - **Frontend** servido desde el mismo backend (HTML + JS responsive) — funciona en celular y notebook sin necesidad de instalar una app.
> - **Pagos** con **Lemon Squeezy** (cobra mundial, emite factura, se queda con 5%). La plata me llega en USD a una cuenta y de ahí se convierte a CLP.
> - **Hosting** en Render.com — un servicio web + un worker de background + la base de datos. Auto-escala.
> - **Despliegue continuo** — cada vez que hago un push a GitHub, en 2 minutos está en producción.
> - **Integración con Canvas** vía OAuth — el alumno se conecta una vez y la plataforma sincroniza todo solo.
>
> Esto **no es un prototipo**: está corriendo en un servidor real, tiene usuarios reales usándolo, y la pasarela de pagos está funcionando."

## 4. Estado actual (1 min)

> "Hoy mismo:
>
> - Código: aprox 20.000 líneas de Python, ~1.000 horas de desarrollo.
> - La plataforma completa está en producción.
> - Sistema de pagos con Lemon Squeezy integrado y probado.
> - [Aquí agregar: tu número real de usuarios registrados, cuántos en PLUS hoy, ingresos del último mes].
>
> La parte cara — construir el producto — **ya está hecha y pagada por mí mismo**. Lo que sigue es **conseguir usuarios**."

## 5. Costos mensuales reales (1–2 min)

> "Estos son los costos fijos que tengo hoy para mantener la plataforma funcionando:
>
> | Concepto | USD / mes | CLP / mes |
> |---|---|---|
> | Hosting Render (web + worker + base de datos) | $35 | ~33.000 |
> | API de OpenAI (quizzes + flashcards + parsing de Canvas, todo con gpt-4o-mini) | $15 | ~14.000 |
> | Envío de correos (recordatorios diarios, emails de ranking — tier bajo de Resend) | $10 | ~9.500 |
> | Dominio + SSL + herramientas varias | $5 | ~5.000 |
> | **Total fijo** | **~$65 USD** | **~62.000 CLP** |
>
> Fee de Lemon Squeezy (5%) es costo variable sobre lo que vendo — no aparece si no vendo nada.
>
> El costo de IA es bajo porque uso `gpt-4o-mini` y las llamadas son cortas y estructuradas. Aunque los usuarios se dupliquen, la factura de IA apenas sube a $25–30/mes. No es un producto tipo ChatGPT donde cada conversación cuesta; acá la IA dispara puntualmente cuando alguien sincroniza un curso o pide un quiz."

## 6. Ingresos potenciales (2 min)

> "La plataforma tiene tres fuentes de ingreso:
>
> **a) Suscripción PLUS** — $4.99 USD/mes (~4.700 CLP/mes) o $39.99 USD/año.
> **b) Suscripción PRO** — $9.99 USD/mes (~9.500 CLP/mes) o $79.99 USD/año.
> **c) Paquetes de monedas** (compra única) — desde $0.99 hasta $34.99 USD.
>
> **Escenario 1 — primer hito: 15 suscriptores PLUS pagando.**
> - 15 × $4.99 = $74,85/mes ≈ **71.000 CLP/mes**.
> - Más un 20% que compra un paquete de monedas promedio de $3: +$9/mes ≈ 8.500 CLP.
> - **Ingreso bruto: ~$84 USD ≈ 80.000 CLP/mes.**
> - Costos fijos: 62.000 CLP + fee Lemon (5%) ≈ 4.000 CLP = **~66.000 CLP**
> - **Ganancia neta: ~14.000 CLP/mes.** Chico pero **ya arriba de break-even con solo 15 usuarios**. De ahí todo lo que sume es ganancia casi pura, porque los costos fijos no crecen con cada usuario nuevo.
>
> **Escenario 2 — objetivo realista a 6 meses: 150 suscriptores PLUS + compras de monedas.**
> - 150 × $4.99 = $748/mes ≈ **711.000 CLP/mes** (PLUS)
> - Coin packs (20% compra, promedio $5): $150/mes ≈ **142.000 CLP**
> - Algunos PRO (asume 10 usuarios a $9.99): $100/mes ≈ **95.000 CLP**
> - **Ingreso bruto: ~$1.000 USD ≈ 950.000 CLP/mes**
> - Costos fijos: 62.000 CLP
> - Costos variables de IA (se duplican con más uso): +$15 ≈ 14.000 CLP
> - Fee Lemon Squeezy (5%): $50 ≈ 48.000 CLP
> - **Ganancia neta mensual: ~825.000 CLP** (~$870 USD)
>
> **Escenario 3 — a 12 meses con 400 PLUS + buen volumen de coin packs**: ~2.600.000 CLP netos/mes."

## 7. La pregunta — qué necesito (1 min)

> "Para pasar del hito 1 al hito 2 (de 15 a 150 usuarios pagando), necesito plata para **adquisición**. Construir el producto ya lo hice yo; lo que no puedo pagar de mi bolsillo es la publicidad.
>
> | Concepto | Total |
> |---|---|
> | Publicidad en Instagram + TikTok + Google ($150 USD/mes × 6 meses) | ~850.000 CLP |
> | Cubrir hosting y APIs los primeros meses (62.000 CLP × 6 meses, antes de tener ingresos suficientes) | ~400.000 CLP |
> | Gastos legales (constituir la sociedad, términos y condiciones, aspectos tributarios) | ~300.000 CLP |
> | Colchón para imprevistos | ~200.000 CLP |
> | **Total que les pido** | **~1.750.000 CLP** (aprox $1.850 USD) |
>
> Menos que un auto usado y menos que un viaje familiar. Con esto tengo 6 meses de runway garantizado para llegar a los 150 usuarios pagando."

## 8. Qué reciben a cambio (1 min)

> "Les propongo dos opciones — la que más les acomode:
>
> **Opción A — Préstamo con interés fijo.**
> Me prestan 1.750.000 CLP. Les devuelvo **2.275.000 CLP en 18 meses** (eso es 20% anual — mejor rendimiento que cualquier depósito a plazo del banco). Si el negocio despega rápido, les pago antes.
>
> **Opción B — Participación (equity).**
> Me dan los 1.750.000 CLP a cambio del **10% de Machreach**. Si al año la empresa está generando 825.000 CLP netos/mes como proyecto, su 10% vale 82.500 CLP/mes de utilidad. A los 21 meses ya recuperaron la inversión **y siguen cobrando** el 10% mientras la empresa exista. Si un día vendo el proyecto, les corresponde el 10% del precio de venta.
>
> La Opción A es segura. La Opción B es más riesgosa pero con techo mucho más alto. Ustedes eligen."

## 9. Cierre (30 seg)

> "No les estoy pidiendo que confíen en una idea — les estoy pidiendo que inviertan en un producto que **ya existe, ya corre en producción, y ya procesa pagos**. Lo único que no tengo solo es el capital para acelerar la adquisición. Si creen en mí y en el proyecto, cerramos. Si prefieren pensarlo, no hay apuro. ¿Qué preguntas tienen?"

---

## Apéndice — Preguntas que seguramente les van a hacer

**"¿Y si no llegas a los 150 usuarios pagando?"**
> "El break-even está en **14 suscriptores PLUS** (14 × $4.99 ≈ $70/mes ≈ 66.000 CLP, contra 62.000 CLP de costos fijos + fee). Con **30 usuarios** ya estoy duplicando los costos. El escenario de 150 es el **objetivo de crecimiento**, no el punto de supervivencia. Si llego solo a 50, la plataforma ya se sostiene sola mientras sigo empujándola. Si llego a cero — que no va a pasar porque la plataforma ya tiene tracción — la plata se gastó en publicidad que generó datos útiles para el siguiente intento."

**"¿Por qué un estudiante te pagaría 4.700 CLP al mes?"**
> "Un alumno que pierde un ramo paga arancel doble — unos 600.000 CLP fácilmente. Que la app te ayude a organizarte, medir tus horas y competir sanamente con compañeros por 5.000 CLP al mes es trivial. Además, el tier gratuito ya es potente — quien paga PLUS lo hace por los cosméticos y las monedas extras, que es el mismo modelo que hizo rica a Duolingo y Supercell."

**"¿Qué pasa si OpenAI sube los precios?"**
> "Los modelos de IA son cada vez **más baratos**, no más caros — GPT-4 hoy cuesta 10 veces menos que al lanzamiento. Mis márgenes suben con el tiempo sin hacer nada."

**"¿Qué pasa si descuidas la U por esto?"**
> "La plataforma corre sola el 95% del tiempo — mantenimiento real son ~5 horas a la semana. Lo que consume tiempo es adquirir usuarios, y gran parte de eso se automatiza con ads pagados — que es justamente lo que les estoy pidiendo financiar."

**"¿Hay competencia?"**
> "Notion, Forest App y Quizlet son los referentes, pero ninguno integra las 5 cosas que Machreach integra a la vez (Canvas + Focus + analítica + quizzes comunitarios + leaderboards con economía de monedas). El nicho específico — universitario latinoamericano con conexión directa a Canvas — está libre."

**"¿Y si te quitan Canvas la API?"**
> "La integración con Canvas es OAuth estándar usado por cientos de apps terceras. Canvas gana dinero con la API. Pero incluso sin Canvas, los alumnos pueden subir sus cursos manualmente — la app seguiría funcionando, solo perdería un diferencial."

---

## Notas para ti (no leer en voz alta)

- Antes de la reunión, **actualiza los números reales**: cuántos usuarios registrados tienes hoy, cuántos en PLUS, cuánto ingresó en el último mes, y cuánto llevas invertido de tu propia plata.
- Imprime una hoja con la tabla de costos, la tabla de ingresos proyectados, y las dos opciones de inversión — a los papás les da mucha más confianza ver las cifras escritas.
- Si te preguntan por competencia, nombra concretos: **Notion** (gratis pero sin analítica de estudio ni gamificación), **Forest** (solo focus, sin Canvas ni quizzes), **Quizlet** (flashcards, sin tiempo ni rankings).
- Ofreceles ver el **dashboard de admin en vivo** — que vean usuarios reales, pagos reales, métricas reales. Nada de fe ciega.
- Valor de cambio usado: 1 USD ≈ 950 CLP. Si cuando presentas el valor es muy distinto, recalculá las columnas de CLP.

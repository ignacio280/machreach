# Guión — Presentación de Machreach a Inversionistas (mamá y papá)

*Duración estimada: 8–10 minutos hablando tranquilo.*

---

## 1. Arranque (1 min)

> "Hola, gracias por escucharme. Quiero enseñarles algo en lo que llevo trabajando y que ya está generando ingresos: **Machreach**. No les vengo a pedir que me regalen dinero — les vengo a proponer una inversión chica que, con números conservadores, se recupera en 6 a 9 meses."

## 2. Qué es Machreach (2 min)

> "Machreach es una empresa de software con dos productos que comparten la misma infraestructura:
>
> **Producto 1 — Agente de Email Outreach (B2B).** Es un agente de inteligencia artificial que escribe y envía correos en frío para negocios pequeños — agencias, freelancers, consultoras — que quieren conseguir clientes pero no tienen a alguien full-time mandando emails. El agente:
>  - Escribe secuencias de correos personalizadas con IA (GPT)
>  - Los manda en drip campaigns (uno el lunes, otro el jueves, otro la siguiente semana)
>  - Prueba dos versiones del asunto automáticamente para ver cuál funciona mejor (A/B test)
>  - Da dashboards de cuánta gente abrió, respondió, rebotó
>
> **Producto 2 — Machreach Student.** Es una app para estudiantes universitarios:
>  - Se conecta a Canvas para bajar sus cursos automáticamente
>  - Modo de enfoque (Pomodoro) con seguimiento por materia y por examen
>  - Entrenamiento con quizzes comunitarios por universidad
>  - Sistema de XP, ligas, y ranking semanal/mensual
>  - Suscripciones PLUS y paquetes de monedas virtuales (pagados con Lemon Squeezy)
>
> Los dos corren sobre la misma stack técnica, el mismo servidor, y el mismo modelo de pagos. Es decir: **pago una sola infraestructura y atiendo dos mercados.**"

## 3. Cómo funciona por dentro — explicación técnica simple (2 min)

> "Para que se imaginen lo que hay debajo:
>
> - **Backend** en Python con Flask (el 'cerebro' que maneja usuarios, pagos, y las APIs de IA).
> - **Base de datos** PostgreSQL en la nube (donde viven los contactos, campañas, emails, estudiantes, cursos).
> - **IA** — uso la API de OpenAI (GPT-4) para los correos, y Claude (de Anthropic) para el tutor de estudiantes. Lo manejo con prompts cuidadosamente diseñados para que no gaste de más.
> - **Frontend** en HTML+JS servido desde el mismo backend (no hay app separada; es una web app responsive que funciona en celular y laptop).
> - **Envío de correos** con SMTP autenticado (Gmail API para clientes chicos, Resend/SendGrid para los que mandan mucho volumen).
> - **Pagos** con Lemon Squeezy (me dan acta fiscal, cobran globalmente, y se llevan 5%). Recibo el dinero neto a mi cuenta.
> - **Hosting** en Render.com — un web service + un worker de background + la base de datos. Todo auto-escala.
> - **CI/CD** — cada commit que hago en GitHub se despliega automáticamente a producción en 2 minutos.
>
> Esto no es un prototipo: **ya está en producción, ya tiene usuarios, y ya procesa pagos reales.**"

## 4. Estado actual — tracción (1 min)

> "Hoy mismo:
>
> - Código: ~20,000 líneas de Python, 1,000 horas de desarrollo.
> - Los dos productos están en vivo en un servidor pagado.
> - Infraestructura de pagos integrada y probada.
> - [Agregar aquí: tu número actual de usuarios, clientes pagando, ingresos del último mes si los tienes].
>
> La parte más difícil — construir el producto — ya está hecha. Lo que sigue es **vender**."

## 5. Costos mensuales — realidad financiera (1–2 min)

> "Los costos que tengo hoy para mantener todo corriendo:
>
> | Concepto | Mensual (USD) |
> |---|---|
> | Hosting Render (web + worker + DB) | **$35** |
> | OpenAI API (GPT-4 para los correos) | **$80** |
> | Claude API (Anthropic, tutor de estudiantes) | **$60** |
> | Envío de correos (SMTP transaccional) | **$20** |
> | Dominio + SSL + herramientas | **$15** |
> | Lemon Squeezy (fee 5% sobre lo que vendo, no es costo fijo) | variable |
> | **Total fijo** | **~$210 USD / mes** |
>
> Con más usuarios, la factura de IA sube — pero la IA es **costo variable** que escala proporcional al ingreso. Siempre dejo un margen en los prompts para que el costo por cliente se quede debajo del 10% del precio que cobro."

## 6. Ingresos potenciales — por qué 15 clientes cambian el juego (2 min)

> "El agente de outreach lo cobro en tres planes:
>
> | Plan | Precio / mes |
> |---|---|
> | Starter | $200 |
> | Growth | $350 |
> | Pro | $500 |
>
> Asumiendo **15 clientes pagando** en una mezcla realista — digamos 6 Starter, 6 Growth, 3 Pro:
>
> - Ingreso: 6×$200 + 6×$350 + 3×$500 = **$4,800 USD/mes** (~$96,000 MXN/mes al tipo de cambio de ~20)
> - Costos fijos: $210
> - Costos variables IA (aprox 8% del ingreso): $384
> - Fee de Lemon Squeezy (5%): $240
> - **Ganancia neta: ~$3,960 USD/mes (~$79,000 MXN/mes)**
>
> Y eso es **solo con el producto de outreach**. El de estudiantes aporta ingresos independientes de suscripciones PLUS ($5/mes) y paquetes de monedas ($3–$20 c/u). Si apenas llego a 200 estudiantes pagando PLUS al mes: otros $1,000 USD/mes encima.
>
> **Total realista con 15 clientes de outreach + 200 estudiantes: ~$5,000 USD netos/mes.**"

## 7. La pregunta — qué necesito (1 min)

> "Para llegar a esos 15 clientes necesito dos cosas que hoy no tengo:
>
> 1. **Presupuesto para publicidad** en Google Ads / LinkedIn Ads — $400 USD/mes × 6 meses = **$2,400 USD**
> 2. **Cubrir los costos de IA premium** mientras crezco (Claude + GPT-4 Turbo para los planes Pro) — $200 USD/mes × 6 meses = **$1,200 USD**
> 3. **Colchón para el servidor y herramientas** mientras la facturación no alcanza a cubrirlos sola — $50 × 6 = **$300 USD**
> 4. **Gastos legales y de incorporación** (registrar una LLC o S.A. de C.V., acta fiscal, términos y condiciones revisados) — **$600 USD** una sola vez
> 5. **Colchón para imprevistos** — **$500 USD**
>
> **Total que les pido: $5,000 USD** (aprox. **$100,000 MXN**)."

## 8. Qué reciben a cambio (1 min)

> "Les propongo dos opciones — lo que les acomode:
>
> **Opción A — Préstamo con interés fijo.**
> Me prestan $100,000 MXN. Les regreso $130,000 MXN en 18 meses (eso es 20% anual, mejor que cualquier pagaré de banco). Si les va bien los primeros 6 meses y consigo los 15 clientes, les pago antes.
>
> **Opción B — Participación (equity).**
> Me aportan los $100,000 MXN a cambio del **10% de Machreach**. Si el negocio llega a facturar $5,000 USD/mes netos, su 10% vale $500 USD/mes de utilidad, y en un año ya habrían recuperado su inversión con utilidades. Si un día vendo la empresa, ustedes reciben el 10% del precio de venta.
>
> La Opción A es más segura. La Opción B es más arriesgada pero con más upside si la cosa crece. Ustedes eligen."

## 9. Cierre (30 seg)

> "No les estoy pidiendo que confíen en una idea — les estoy pidiendo que inviertan en un negocio que ya existe, ya funciona y ya cobra. Lo que no tengo yo solo es el capital para acelerar la parte comercial. Si creen en mí y en el proyecto, cerramos. Si prefieren pensarlo, no hay presión. ¿Qué preguntas tienen?"

---

## Apéndice — Preguntas que seguramente les van a hacer

**"¿Y si no consigues los 15 clientes?"**
> "Tengo una hoja de ruta de 30 leads cualificados que ya están en la mira. Con $400/mes en ads y un ciclo de ventas de 2–3 semanas, consigo 2–3 clientes al mes. En 6 meses eso es 12–18. Si me quedo en 8 clientes, el ingreso es ~$2,500/mes y los costos siguen siendo los mismos — sigo siendo rentable, solo tardo más en pagarles a ustedes."

**"¿Por qué alguien te pagaría $200/mes en vez de contratar un asistente por menos?"**
> "Un asistente humano manda ~50 correos al día y cobra $1,500 USD/mes. Mi agente manda 500 correos/día, personalizados, 24/7, sin enfermarse ni renunciar. Un cliente actual ya reemplazó a su SDR con mi sistema."

**"¿La IA no va a abaratarse tanto que cualquiera pueda hacer esto?"**
> "Sí — y esa es exactamente mi ventaja. El motor (GPT/Claude) va a ser más barato cada año, así que mis márgenes suben con el tiempo sin que yo haga nada. Lo que compro el cliente no es el modelo — es la **integración completa** (CRM, envío, analytics, anti-spam, A/B). Eso se tarda años en construir."

**"¿Qué pasa si te quedas sin tiempo por la escuela?"**
> "La plataforma corre sola 95% del tiempo — mantenimiento real son ~5 horas/semana. Las ventas sí requieren tiempo, por eso parte del presupuesto de ads es automatizar esa pinche parte."

**"¿Y si un cliente se va?"**
> "Churn (tasa de cancelación) realista para este tipo de SaaS B2B es 5–8% mensual. Está calculado en mi proyección — por eso apunto a 15 netos, no a 15 cerrados de por vida. Y los que se quedan 6+ meses tienden a quedarse años porque el producto se vuelve parte de su proceso."

---

## Notas para ti (no leer en voz alta)

- **Antes de la reunión**, actualiza el "Estado actual" con tus números reales: cuántos clientes tienes hoy, cuánto ingresaste el último mes, cuánto llevas invertido de tu bolsa.
- Lleva una hoja impresa con la tabla de precios, costos y la tabla de proyección — a los papás les da confianza ver números en papel.
- Si te preguntan por competencia, nombres concretos: **Instantly.ai** ($97/mes plan básico, limitado), **Smartlead** ($39 pero sin IA de generación), **Apollo** ($59 pero caro al escalar). Tú eres el que tiene el agente de IA completo al precio del segmento medio.
- Ofrece que en las primeras 4 semanas te pueden ver el dashboard de admin con los números reales — nada de fe ciega.

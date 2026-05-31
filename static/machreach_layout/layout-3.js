  (function(){
    var T = {
      // Compound phrases first — longer keys win the longest-match regex.
      // Without these, "Study Tools" rendered as "Estudiar Tools" because
      // only the bare word "Study" was in the map.
      "Study Tools": "Herramientas de Estudio",
      "Grade Sheet": "Planilla de Notas",
      "Tools": "Herramientas",
      "Social": "Social",
      "Pro": "Pro",
      "Study Analytics": "Analítica de Estudio",
      "Your study analytics": "Tu analítica de estudio",
      "How much, when, and on what — all time.": "Cuánto, cuándo y en qué — histórico.",
      "Study time per course": "Tiempo de estudio por curso",
      "Click any bar to see the day-by-day breakdown.": "Haz clic en una barra para ver el desglose día a día.",
      "Total hours": "Horas totales",
      "Pages read": "Páginas leídas",
      "Current streak": "Racha actual 🔥",
      "Best day": "Mejor día",
      "Last 14 days": "Últimos 14 días",
      "Day-of-week (this week)": "Día de la semana (esta semana)",
      "this week": "esta semana",
      "No course-tagged focus sessions yet.": "Aún no hay sesiones etiquetadas con un curso.",
      "Previous week": "Semana anterior",
      "Next week": "Semana siguiente",
      "Back": "Volver",
      // Nav
      "Dashboard": "Panel", "Courses": "Cursos", "Plan": "Plan", "Flashcards": "Tarjetas",
      "Quizzes": "Exámenes", "Notes": "Apuntes", "Tutor": "Tutor", "XP": "XP",
      "Mail": "Correo", "Focus Mode": "Modo Enfoque", "Exams": "Exámenes",
      "GPA Calculator": "Calculadora GPA", "Schedule": "Horario", "Weak Topics": "Temas Débiles",
      "Settings": "Ajustes", "Leaderboard": "Clasificación",
      // Achievements page
      "Achievements & Progress": "Logros y Progreso", "Level": "Nivel",
      "XP to next level": "XP para el siguiente nivel", "Day Streak": "Racha de Días 🔥",
      "Badges Earned": "Insignias Obtenidas", "Your Badges": "Tus Insignias",
      "All Badges": "Todas las Insignias", "Recent Activity": "Actividad Reciente",
      "No badges yet — keep studying!": "¡Aún no tienes insignias — sigue estudiando!",
      "No activity yet.": "Aún no hay actividad.",
      "Earned!": "¡Obtenida!", "Not yet earned": "Aún no obtenida",
      // Badge names
      "Welcome!": "¡Bienvenido!", "Quiz Rookie": "Novato en Exámenes",
      "Quiz Master": "Maestro de Exámenes", "Flashcard Fan": "Fan de Tarjetas",
      "On Fire!": "¡En Llamas!", "Unstoppable": "¡Imparable!",
      "Diamond Student": "Estudiante Diamante", "Note Taker": "Tomador de Apuntes",
      "Rising Star": "Estrella Naciente", "Shining Star": "Estrella Brillante",
      "Superstar": "Superestrella", "Focused": "Enfocado", "Deep Focus": "Enfoque Profundo",
      "Focus Master": "Maestro del Enfoque", "Page Turner": "Lector Ávido",
      "Quiz Pro": "Pro de Exámenes",
      // Badge descriptions
      "Logged in for the first time": "Iniciaste sesión por primera vez",
      "Completed your first quiz": "Completaste tu primer examen",
      "Scored 100% on a quiz": "Obtuviste 100% en un examen",
      "Reviewed 100 flashcards": "Revisaste 100 tarjetas",
      "3-day study streak": "Racha 🔥 de estudio de 3 días",
      "7-day study streak": "Racha 🔥 de estudio de 7 días",
      "30-day study streak": "Racha 🔥 de estudio de 30 días",
      "Created 10 notes": "Creaste 10 apuntes",
      "Earned 100 XP": "Ganaste 100 XP", "Earned 500 XP": "Ganaste 500 XP",
      "Earned 1000 XP": "Ganaste 1000 XP",
      "1 hour of total focus time": "1 hora de tiempo de enfoque total",
      "10 hours of total focus time": "10 horas de tiempo de enfoque total",
      "50 hours of total focus time": "50 horas de tiempo de enfoque total",
      "Read 100 pages": "Leíste 100 páginas",
      "Completed 10 quizzes": "Completaste 10 exámenes",
      // Levels
      "Freshman": "Novato", "Sophomore": "Aprendiz", "Junior": "Intermedio",
      "Senior": "Avanzado", "Scholar": "Erudito", "Master": "Maestro", "Professor": "Profesor",
      // Focus page
      "Study Timer": "Temporizador de Estudio", "Pomodoro": "Pomodoro",
      "Page Method": "Método de Páginas", "Custom": "Personalizado",
      "Work (min)": "Trabajo (min)", "Break (min)": "Descanso (min)",
      "Long break (min)": "Descanso largo (min)",
      "Long break after every 4 sessions.": "Descanso largo después de cada 4 sesiones.",
      "Target pages": "Páginas objetivo", "Page Completed!": "¡Página Completada!",
      "Ready to focus": "Listo para enfocarte", "Start": "Iniciar", "Pause": "Pausar",
      "Reset": "Reiniciar", "Study Music": "Música de Estudio",
      "Quick Flashcards": "Tarjetas Rápidas", "Quick Notes": "Notas Rápidas",
      "Hours Focused": "Horas Enfocado", "Sessions": "Sesiones",
      "Pages Read": "Páginas Leídas",
      // Settings
      "Profile": "Perfil", "Name": "Nombre", "Email": "Correo",
      "Email cannot be changed.": "El correo no se puede cambiar.",
      "Save Changes": "Guardar Cambios",
      "University & Studies": "Universidad y Estudios",
      "University": "Universidad", "Field of Study": "Carrera",
      "View Leaderboard": "Ver Clasificación",
      "Canvas LMS": "Canvas LMS", "Conectado": "Conectado",
      "Sin conectar": "No conectado", "Manage Connection": "Administrar Conexión",
      "Conectar Canvas": "Conectar Canvas",
      "Email Accounts": "Cuentas de Correo", "Manage in Mail Hub": "Administrar en Correo",
      "Daily Study Email": "Email Diario de Estudio",
      "Get a morning email with your study plan, upcoming exams, and weak topics to review.":
        "Recibe un email matutino con tu plan de estudio, próximos exámenes y temas a repasar.",
      "Enable daily study email": "Activar email diario de estudio",
      "Send at (hour)": "Enviar a las (hora)", "Timezone": "Zona Horaria",
      "Save Preferences": "Guardar Preferencias", "Saved!": "¡Guardado!",
      "Error saving.": "Error al guardar.",
      // Leaderboard
      "Student Rankings": "Clasificación de Estudiantes",
      "Compete with other students! Earn XP from focus sessions, quizzes, and flashcards.":
        "¡Compite con otros estudiantes! Gana XP con sesiones de enfoque, exámenes y tarjetas.",
      "Your Rank": "Tu Posición", "Total XP": "XP Total", "All Students": "Todos",
      "Rank": "Posición", "Student": "Estudiante",
      "No students on the leaderboard yet. Start earning XP!":
        "Aún no hay estudiantes en la clasificación. ¡Empieza a ganar XP!",
      // Smart Import
      "Smart Import": "Importación Inteligente",
      "Drop a PDF or DOCX — we'll auto-generate notes, flashcards, and a quiz":
        "Sube un PDF o DOCX — generaremos apuntes, tarjetas y un examen automáticamente",
      "Drag & Drop your file here": "Arrastra y suelta tu archivo aquí",
      "or click to browse": "o haz clic para buscar",
      "Generate Notes + Flashcards + Quiz": "Generar Apuntes + Tarjetas + Examen",
      "Processing your document...": "Procesando tu documento...",
      "Study materials created!": "¡Materiales de estudio creados!",
      "Import": "Importar",
      // Study Exchange
      "Study Exchange": "Intercambio de Apuntes",
      "Browse & share study notes with other students":
        "Navega y comparte apuntes con otros estudiantes",
      "My Shared Notes": "Mis Apuntes Compartidos",
      "Search notes...": "Buscar apuntes...", "Subject/Course": "Materia/Curso",
      "Share": "Compartir", "Unpublish": "Despublicar",
      "Public": "Público", "Private": "Privado",
      "Fork to My Notes": "Copiar a Mis Apuntes", "Exchange": "Intercambio",
      "No shared notes yet. Be the first to share!":
        "Aún no hay apuntes compartidos. ¡Sé el primero en compartir!",
      // Exam Simulator
      "Exam Simulator": "Simulador de Examen",
      "Start Exam": "Iniciar Examen",
      "Exam Rules:": "Reglas del Examen:",
      "Lock In Answer": "Confirmar Respuesta",
      "Exam Complete!": "¡Examen Completado!",
      "Question Review": "Revisión de Preguntas",
      "Retake Exam": "Repetir Examen",
      "Analytics": "Análisis",
      "Avg per question": "Promedio por pregunta",
      "Fastest answer": "Respuesta más rápida",
      "Slowest answer": "Respuesta más lenta",
      // SRS
      "Spaced Repetition": "Repetición Espaciada",
      "due": "pendientes", "Again": "Otra vez", "Hard": "Difícil",
      "Good": "Bien", "Easy": "Fácil",
      // Dashboard
      "Today's Plan": "Plan de Hoy", "Upcoming Exams": "Próximos Exámenes",
      "Study Stats": "Estadísticas de Estudio", "Quick Actions": "Acciones Rápidas",
      // Quizzes
      "Generate Quiz": "Generar Examen", "Take Quiz": "Hacer Examen",
      "Your Quizzes": "Tus Exámenes", "Score": "Puntuación", "Attempts": "Intentos",
      "Best Score": "Mejor Puntuación", "Delete": "Eliminar",
      // Flashcards
      "Your Flashcard Decks": "Tus Mazos de Tarjetas", "Study": "Estudiar",
      "cards": "tarjetas", "Generate Flashcards": "Generar Tarjetas",
      // Notes
      "Your Notes": "Tus Apuntes", "Generate Notes": "Generar Apuntes",
      // Common
      "Cargando...": "Cargando...", "Error": "Error", "Success": "Éxito",
      "Cancel": "Cancelar", "Confirm": "Confirmar", "Save": "Guardar",
      "Back": "Volver", "Next": "Siguiente", "Previous": "Anterior",
      "Search": "Buscar", "Filter": "Filtrar", "Sort": "Ordenar",
      "Select a course": "Selecciona un curso", "No courses yet": "Aún no hay cursos",

      // ── Extended UI vocabulary ──
      // Generic actions
      "Edit": "Editar", "Update": "Actualizar", "Add": "Agregar", "Create": "Crear",
      "Remove": "Quitar", "Submit": "Enviar", "Send": "Enviar", "Close": "Cerrar",
      "Open": "Abrir", "Continue": "Continuar", "Finish": "Finalizar", "Done": "Listo",
      "Apply": "Aplicar", "Reload": "Recargar", "Refresh": "Actualizar", "Generate": "Generar",
      "Analyze": "Analizar", "Upload": "Subir", "Download": "Descargar",
      "Browse": "Examinar", "Choose": "Elegir", "Select": "Seleccionar",
      "Yes": "Sí", "No": "No", "OK": "OK", "Got it": "Entendido",
      "Logout": "Cerrar Sesión", "Login": "Iniciar Sesión", "Sign in": "Iniciar Sesión",
      "Sign up": "Registrarse", "Register": "Registrarse",
      "Free": "Gratis", "Pro": "Pro", "Premium": "Premium", "Upgrade": "Mejorar",
      "Active": "Activo", "Inactive": "Inactivo", "Pending": "Pendiente",
      "Completed": "Completado", "Failed": "Falló", "Sent": "Enviado",
      "Draft": "Borrador", "Archive": "Archivar", "Archived": "Archivado",
      "All": "Todos", "None": "Ninguno", "Other": "Otro",
      "Today": "Hoy", "Yesterday": "Ayer", "Tomorrow": "Mañana",
      "This Week": "Esta Semana", "This Month": "Este Mes",
      "Date": "Fecha", "Time": "Hora", "Duration": "Duración",
      "Created": "Creado", "Updated": "Actualizado", "Last Updated": "Última Actualización",
      "Type": "Tipo", "Title": "Título", "Description": "Descripción",
      "Notes": "Apuntes", "Tags": "Etiquetas", "Category": "Categoría",
      "Public": "Público", "Private": "Privado",

      // Drag & drop / files
      "Drop a PDF / DOCX / TXT here": "Suelta un PDF / DOCX / TXT aquí",
      "or click to browse": "o haz clic para buscar",
      "Drag & drop PDF or DOCX files here": "Arrastra y suelta PDF o DOCX aquí",
      "we'll generate flashcards directly from the file (no course needed)":
        "generaremos tarjetas directamente del archivo (no se necesita curso)",
      "we'll generate quiz questions directly from the file (no course needed)":
        "generaremos preguntas directamente del archivo (no se necesita curso)",
      "— or pick from your courses —": "— o elige de tus cursos —",
      "multi-chapter PDFs fully supported": "PDFs con múltiples capítulos totalmente soportados",
      "AI-summarize into structured notes (recommended for textbooks & multi-chapter PDFs)":
        "Resumir con IA en apuntes estructurados (recomendado para libros y PDFs con varios capítulos)",
      "Drop a PDF / DOCX / TXT": "Suelta un PDF / DOCX / TXT",
      "we'll extract the text into the editor below": "extraeremos el texto en el editor de abajo",
      "Attach a file (PDF/DOCX/TXT)": "Adjuntar un archivo (PDF/DOCX/TXT)",
      "Ask your tutor... (or drag a PDF onto the chat)":
        "Pregúntale a tu tutor... (o arrastra un PDF al chat)",
      "Drag & Drop Anywhere": "Arrastra y Suelta en Cualquier Lugar",
      "Drop a PDF onto Notes, Flashcards, or Quizzes — instant study material from your files.":
        "Suelta un PDF en Apuntes, Tarjetas o Exámenes — material de estudio al instante.",

      // Course / Exam / Quiz / Flashcard / Notes shared labels
      "Course": "Curso", "Courses": "Cursos", "Exam": "Examen", "Topic": "Tema", "Topics": "Temas",
      "Question": "Pregunta", "Questions": "Preguntas", "Answer": "Respuesta", "Answers": "Respuestas",
      "Number of cards": "Cantidad de tarjetas", "Number of questions": "Cantidad de preguntas",
      "Custom title (optional)": "Título personalizado (opcional)",
      "Auto-generated if empty": "Generado automáticamente si está vacío",
      "Difficulty": "Dificultad",
      "Easy — Basic recall": "Fácil — Recuerdo básico",
      "Medium — Exam-level": "Medio — Nivel de examen",
      "Hard — Challenge": "Difícil — Desafío",
      "Generate AI Flashcards": "Generar Tarjetas con IA",
      "Generate AI Quiz": "Generar Examen con IA",
      "Generate AI Notes": "Generar material de estudio con IA",
      "AI Flashcards": "Tarjetas IA",
      "AI Study Tutor": "Herramientas de estudio IA",
      "Practice Quizzes": "Exámenes de Práctica",
      "Smart spaced repetition · Generated from your course materials":
        "Repetición espaciada · Generadas desde tus materiales de curso",
      "Unlimited AI-generated questions · Adjustable difficulty":
        "Preguntas ilimitadas con IA · Dificultad ajustable",
      "Generate study tools from your own notes and course material.":
        "Genera herramientas de estudio desde tus propios apuntes y materiales.",
      "General (no specific course)": "General (sin curso específico)",
      "Up to 100. Large quizzes generate in batches — give it a few seconds.":
        "Hasta 100. Los exámenes grandes se generan por lotes — dale unos segundos.",
      "All topics": "Todos los temas",
      "Exam (optional)": "Examen (opcional)",
      "Not taken": "No realizado",
      "attempts": "intentos", "attempt": "intento",
      "questions": "preguntas", "question": "pregunta",
      "due": "pendientes",
      "Drop a file or select a course": "Suelta un archivo o selecciona un curso",
      "Generated %d flashcards!": "¡%d tarjetas generadas!",
      "Error al generar": "Falló la generación",
      "Error de red": "Error de red",
      "Failed to add card": "Error al agregar tarjeta",
      "Failed to delete": "Error al eliminar",
      "Delete this flashcard deck?": "¿Eliminar este mazo de tarjetas?",
      "Delete this card?": "¿Eliminar esta tarjeta?",
      "Delete this note?": "¿Eliminar este apunte?",
      "Delete this quiz?": "¿Eliminar este examen?",
      "Clear chat history?": "¿Borrar historial de chat?",
      "No flashcard decks yet. Generate your first set from a course!":
        "Aún no tienes mazos. ¡Genera el primero desde un curso!",
      "No quizzes yet. Generate your first practice quiz from a course!":
        "Aún no tienes exámenes. ¡Genera el primero desde un curso!",
      "Hi! I'm your AI study tutor. Ask me anything about your course material! 📚":
        "¡Hola! Soy tu tutor IA. ¡Pregúntame lo que sea sobre tus materiales! 📚",
      "Please summarize and explain the attached document.":
        "Por favor resume y explica el documento adjunto.",
      "PDF, DOCX, or TXT only": "Solo PDF, DOCX, o TXT",
      "File too large (max 15MB)": "Archivo demasiado grande (máx 15MB)",
      "Only PDF, DOCX, and TXT files": "Solo archivos PDF, DOCX y TXT",

      // Notes page
      "Generated notes": "Apuntes generados",
      "AI Study Notes": "Apuntes de Estudio IA",

      // Mail Hub / Inbox common
      "Inbox": "Bandeja", "Sent": "Enviados", "Outbox": "Salida",
      "Trash": "Papelera", "Spam": "Spam", "Drafts": "Borradores",
      "Reply": "Responder", "Reply All": "Responder a Todos", "Forward": "Reenviar",
      "Compose": "Redactar", "New Email": "Nuevo Correo",
      "From": "De", "To": "Para", "Cc": "Cc", "Bcc": "Cco", "Subject": "Asunto",
      "Body": "Cuerpo", "Attachments": "Adjuntos",
      "Mail Hub": "Centro de Correo",

      // Contacts
      "Contacts": "Contactos", "Add Contact": "Agregar Contacto",
      "First Name": "Nombre", "Last Name": "Apellido",
      "Company": "Empresa", "Phone": "Teléfono", "Notes": "Notas",

      "Templates": "Plantillas", "Sequence": "Secuencia",
      "Open Rate": "Tasa de Apertura", "Reply Rate": "Tasa de Respuesta",
      "Sent at": "Enviado a las", "Scheduled": "Programado",
      "Send Now": "Enviar Ahora", "Schedule": "Programar",

      "Plan": "Plan", "Current Plan": "Plan Actual",
      "Upgrade Plan": "Mejorar Plan", "Downgrade": "Bajar Plan",
      "Cancel Subscription": "Cancelar Suscripción",
      "per month": "por mes", "per year": "por año",
      "Free Forever": "Gratis Para Siempre",
      "Most Popular": "Más Popular",

      // Dashboard widgets
      "Today's Tasks": "Tareas de Hoy", "Recent Activity": "Actividad Reciente",
      "Quick Stats": "Estadísticas Rápidas", "Performance": "Rendimiento",
      "Welcome back": "Bienvenido de vuelta",

      // GPA / Schedule / Weak topics
      "GPA Calculator": "Calculadora GPA", "Add Course": "Agregar Curso",
      "Weight": "Peso", "Grade": "Nota", "Credit": "Crédito",
      "Total GPA": "GPA Total", "Semester": "Semestre",
      "Class Schedule": "Horario de Clases",
      "Add to Schedule": "Agregar al Horario",
      "Weak Topics": "Temas Débiles",
      "Topics you've struggled with": "Temas con los que has tenido dificultad",

      // Achievements
      "Achievements": "Logros", "XP & Badges": "XP e Insignias",
      "Earn XP by studying!": "¡Gana XP estudiando!",

      // Settings sections
      "Theme": "Tema", "Language": "Idioma", "Currency": "Moneda",
      "Notifications": "Notificaciones", "Privacy": "Privacidad",
      "Account": "Cuenta", "Danger Zone": "Zona de Peligro",

      // Empty states
      "Nothing here yet.": "Nada por aquí todavía.",
      "Get started by creating one": "Empieza creando uno",

      // ── Dashboard headings (whole phrases — must come BEFORE word-level keys) ──
      "Today's Study Plan": "Plan de Estudio de Hoy",
      "Today's Plan": "Plan de Hoy",
      "Upcoming Exams": "Próximos Exámenes",
      "Upcoming Examens": "Próximos Exámenes",
      "Plan Progress": "Progreso del Plan",
      "Hours Focused": "Horas de Estudio",
      "Focus Hours": "Horas de Estudio",
      "day streak": "días de racha 🔥",
      "Day Streak": "Racha de Días 🔥",
      "XP to next level": "XP para el siguiente nivel",
      "What can I do here?": "¿Qué puedo hacer aquí?",
      "A visual map of every feature — click any card to jump there.":
        "Un mapa visual de cada función — haz clic en cualquier tarjeta para ir.",
      "Show": "Mostrar", "Hide": "Ocultar",
      "No study sessions yet": "Aún no hay sesiones de estudio",
      "Sync your courses and generate a plan to get a personalized study schedule for today.":
        "Sincroniza tus cursos y genera un plan para obtener un horario de estudio personalizado para hoy.",
      "No upcoming exams": "No hay exámenes próximos",
      "Sync your courses to automatically detect exam dates from Canvas.":
        "Sincroniza tus cursos para detectar automáticamente las fechas de examen desde Canvas.",
      "Conectar Canvas": "Conectar Canvas",
      "Generate Plan": "Generar Plan",
      "Sync Canvas": "Sincronizar Canvas",
      "Mark Today Complete": "Marcar Hoy Como Completo",
      "AI Recommendations": "Recomendaciones de IA",
      "Starting sync...": "Iniciando sincronización...",
      "Syncing...": "Sincronizando...",
      "Take a break — this may take a while depending on how many files your courses have.":
        "Tómate un descanso — esto puede tardar dependiendo de cuántos archivos tengan tus cursos.",
      "Sync complete!": "¡Sincronización completada!",
      "Sync failed": "La sincronización falló",
      "Error de red": "Error de red",
      "Stats at a glance": "Estadísticas de un vistazo",
      "Your Student Dashboard": "Tu Panel de Estudiante",
      "Exams Dashboard": "Panel de Exámenes",
      "Every upcoming exam, sorted by urgency.": "Todos los exámenes próximos, ordenados por urgencia.",

      // ── Courses page ──
      "My Courses": "Mis Cursos", "Canvas Integration": "Integración con Canvas",
      "Course Sync": "Sincronización de Cursos", "New Course": "Nuevo Curso",
      "Create Course": "Crear Curso", "Create a course": "Crear un curso",
      "Create course manually": "Crear curso manualmente",
      "Send to Canvas": "Enviar a Canvas",
      "Sync Now": "Sincronizar Ahora", "View Materials": "Ver Materiales",
      "Course name": "Nombre del curso", "Code": "Código", "Term": "Periodo",
      "Last Synced": "Última sincronización",
      "Files": "Archivos", "Grading": "Calificación",
      "No courses yet": "Aún no tienes cursos",
      "No courses synced yet": "Aún no se han sincronizado cursos",
      "Sync your courses first": "Sincroniza tus cursos primero",
      "No files uploaded": "No hay archivos subidos",

      // ── Study Plan page ──
      "Study Plan": "Plan de Estudio",
      "Weekly Schedule": "Horario Semanal",
      "Course Difficulty": "Dificultad del Curso",
      "Edit Schedule": "Editar Horario",
      "Free day": "Día libre",
      "Check off each assignment as you complete it":
        "Marca cada tarea a medida que la completes",
      "No study plan yet": "Aún no hay plan de estudio",
      "Sync your Canvas courses first to generate a plan.":
        "Sincroniza tus cursos de Canvas primero para generar un plan.",
      "Complete": "Completar", "Remaining": "Restante",

      // ── Focus Mode page ──
      "Focus Mode": "Modo Enfoque", "Focus Guard": "Guardián de Enfoque",
      "Quick Access": "Acceso Rápido", "Studying for:": "Estudiando para:",
      "Long break after every 4 sessions": "Descanso largo cada 4 sesiones",
      "Space flip": "Espacio para voltear",
      "1 incorrect": "1 incorrecto", "2 correct": "2 correcto",
      "Pages Read": "Páginas Leídas",

      // ── Flashcards page ──
      "Smart spaced repetition": "Repetición espaciada inteligente",
      "Study Mode": "Modo de Estudio", "Edit Cards": "Editar Tarjetas",
      "Add Card": "Agregar Tarjeta", "Study Again": "Estudiar de Nuevo",
      "Start Studying": "Comenzar a Estudiar",
      "Undo last": "Deshacer último",
      "Click to flip": "Haz clic para voltear",
      "Incorrect": "Incorrecto", "Correct": "Correcto",
      "Reviewing again tomorrow": "Repasando de nuevo mañana",
      "Good learning pace": "Buen ritmo de aprendizaje",
      "No flashcard decks yet": "Aún no tienes mazos de tarjetas",
      "Generate your first set from a course!":
        "¡Genera tu primer conjunto desde un curso!",
      "Exam (optional)": "Examen (opcional)",
      "Custom title": "Título personalizado",

      // ── Quizzes page ──
      "Ready to start?": "¿Listo para empezar?",
      "Quiz complete": "Examen completado",
      "Start Quiz": "Iniciar Examen", "See Results": "Ver Resultados",
      "Retake quiz": "Repetir examen", "Retake wrong only": "Repetir solo errores",
      "Back to quizzes": "Volver a exámenes",
      "Enable timer": "Activar temporizador", "Mode": "Modo",
      "Total time for whole quiz": "Tiempo total para el examen",
      "Time per question": "Tiempo por pregunta",
      "60s / question": "60s / pregunta", "90s / question": "90s / pregunta",
      "2m / question": "2m / pregunta", "Realistic exam": "Examen realista",
      "Total time": "Tiempo total", "Avg / question": "Prom. / pregunta",
      "Fastest": "Más rápida", "Slowest": "Más lenta",
      "Mastery": "Dominio", "Solid": "Sólido", "Shaky": "Inestable",
      "Struggling": "Con dificultad",
      "Strengths": "Fortalezas", "Needs work": "Necesita trabajo",
      "Mistake patterns": "Patrones de error", "Do this next": "Haz esto a continuación",
      "30-minute follow-up plan": "Plan de seguimiento de 30 minutos",
      "Question-by-question review": "Revisión pregunta por pregunta",
      "Analyzing...": "Analizando...", "Topic breakdown": "Desglose por tema",

      // ── Exam Simulator ──
      "Time Limit (minutes)": "Tiempo Límite (minutos)",
      "You cannot go back to previous questions":
        "No puedes regresar a preguntas anteriores",
      "Timer runs continuously — no pausing":
        "El temporizador corre sin parar — sin pausas",
      "Answers are final once submitted":
        "Las respuestas son finales al enviarse",
      "Detailed analytics provided at the end":
        "Análisis detallado al finalizar",
      "Back to Quizzes": "Volver a Exámenes",

      // ── Notes page ──
      "AI Study Notes": "Apuntes de Estudio con IA",
      "Comprehensive notes generated from your course materials":
        "Apuntes completos generados desde tus materiales de curso",
      "Generate AI Study Notes": "Generar material de estudio con IA",
      "Export PDF": "Exportar PDF", "Print": "Imprimir",
      "Uploading...": "Subiendo...", "AI-summarizing...": "Resumiendo con IA...",
      "No notes yet": "Aún no hay apuntes",
      "Generate AI study notes from your course materials!":
        "¡Genera apuntes con IA desde tus materiales de curso!",
      "Back to Notes": "Volver a Apuntes",
      "Bold (B)": "Negrita (B)", "Italic (I)": "Cursiva (I)",
      "Underline (U)": "Subrayado (U)",
      "Heading 2 (H2)": "Encabezado 2 (H2)", "Heading 3 (H3)": "Encabezado 3 (H3)",
      "Paragraph (P)": "Párrafo (P)",
      "Bullet list": "Lista con viñetas", "Numbered list": "Lista numerada",
      "Clear formatting": "Quitar formato",

      "Attached:": "Adjuntado:", "No file attached": "Sin archivo adjunto",
      "Hi! I'm your AI study tutor. Ask me anything about your course material!":
        "¡Hola! Soy tu tutor de estudio con IA. ¡Pregúntame lo que quieras sobre tus materiales!",
      "Thinking...": "Pensando...",
      "Please summarize and explain the attached document":
        "Por favor resume y explica el documento adjunto",

      // ── Weak Topics ──
      "Weak Topic Detector": "Detector de Temas Débiles",
      "Based on your flashcard accuracy and quiz scores, here are the topics that need more attention":
        "Basado en tu precisión en tarjetas y exámenes, estos son los temas que necesitan más atención",
      "Recommendations": "Recomendaciones",
      "Next steps to improve": "Próximos pasos para mejorar",
      "Not enough data yet": "Aún no hay suficientes datos",
      "Complete some quizzes and review flashcards to see your weak spots":
        "Completa algunos exámenes y repasa tarjetas para ver tus puntos débiles",

      // ── Achievements ──
      "XP / Total": "XP / Total",

      // ── Leaderboard ──
      "Personal Leaderboards": "Clasificaciones Personales",
      "Fair-play": "Juego Limpio", "Fair-play group": "Grupo de juego limpio",
      "Everyone starts at 0 XP": "Todos comienzan en 0 XP",
      "Create a Group": "Crear un Grupo", "Join with Code": "Unirse con Código",
      "Group Name": "Nombre del Grupo", "Enter group name": "Ingresa el nombre del grupo",
      "Invite Code": "Código de Invitación", "Members": "Miembros",
      "Copy Invite": "Copiar Invitación", "Delete Group": "Eliminar Grupo",
      "Leave": "Salir", "Join": "Unirse",

      // ── Study Exchange ──
      "Share to Exchange": "Compartir al Intercambio",
      "Unpublish from Exchange": "Despublicar del Intercambio",
      "Back to Exchange": "Volver al Intercambio",
      "No notes to share": "No hay apuntes para compartir",
      "Create notes first!": "¡Crea apuntes primero!",

      // ── Settings page ──
      "Mail Sorting Rules": "Reglas de Clasificación de Correo",
      "Interactive Tutorial": "Tutorial Interactivo",
      "Account Security": "Seguridad de la Cuenta",
      "Add Email Account": "Agregar Cuenta de Correo",
      "Save Rules": "Guardar Reglas",
      "Restart Tutorial": "Reiniciar Tutorial",
      "Change password": "Cambiar contraseña",
      "Update Password": "Actualizar Contraseña",
      "Delete My Account": "Eliminar Mi Cuenta",
      "Conectado": "Conectado", "Sin conectar": "No conectado",
      "Your account is secure": "Tu cuenta está segura",
      "mailboxes": "buzones",
      "Write your mail sorting rules here...":
        "Escribe aquí tus reglas de clasificación de correo...",
      "e.g. MIT, Stanford, UNAM...": "ej. MIT, Stanford, UNAM...",
      "e.g. Computer Science, Medicine...": "ej. Ingeniería, Medicina...",
      "Permanently delete your account and all associated data (courses, exams, notes, flashcards, quizzes, chat history, XP, badges). This action cannot be undone":
        "Eliminar permanentemente tu cuenta y todos los datos asociados (cursos, exámenes, apuntes, tarjetas, exámenes, historial de chat, XP, insignias). Esta acción no se puede deshacer",
      "Permanently Delete Account": "Eliminar Cuenta Permanentemente",
      "Current Password": "Contraseña Actual",
      "New Password": "Contraseña Nueva",
      "Confirm Password": "Confirmar Contraseña",
      "Minimum 6 characters": "Mínimo 6 caracteres",
      "Replay the guided walkthrough to rediscover all the features available to you":
        "Reproduce el recorrido guiado para redescubrir todas las funciones disponibles",
      "Emails from my professors are always urgent":
        "Los correos de mis profesores siempre son urgentes",
      "Meeting invites from @university.edu are important":
        "Las invitaciones de reuniones desde @university.edu son importantes",
      "Newsletters and marketing emails are always low priority":
        "Los boletines y correos de marketing siempre son de baja prioridad",

      // ── Canvas settings ──
      "Canvas Connection": "Conexión con Canvas",
      "Canvas LMS Integration": "Integración con Canvas LMS",
      "Canvas URL": "URL de Canvas",
      "API Access Token": "Token de Acceso API",
      "Desconectar": "Desconectar", "Test Connection": "Probar Conexión",

      // ── GPA Calculator ──
      "Your GPA": "Tu GPA", "What-If": "Simulador",
      "Credits": "Créditos", "Calculate GPA": "Calcular GPA",
      "GPA Scale": "Escala de GPA",

      // ── Practice / Schedule ──
      "Practice Problems": "Ejercicios de Práctica",
      "AI-Generated Exercises": "Ejercicios Generados por IA",
      "Schedule & Study Time": "Horario y Tiempo de Estudio",
      "Weekly Availability": "Disponibilidad Semanal",
      "Time slots for study": "Bloques de tiempo para estudiar",
      "Days of the week": "Días de la semana",
      "Hours per day": "Horas por día",

      // ── Headers / brand ──
      "MachReach Student": "MachReach Estudiante",
      "AI-powered study planner · Canvas integration":
        "Planificador de estudio con IA · Integración con Canvas",
      "View All": "Ver Todos",

      // ── Themes ──
      "Default": "Predeterminado", "Midnight": "Medianoche", "Forest": "Bosque",
      "Ocean": "Océano", "Rose": "Rosa", "Sunset": "Atardecer",
      "Mono": "Monocromo", "Light": "Claro", "Lavender": "Lavanda",
      "Mint": "Menta", "Peach": "Durazno", "Sky": "Cielo",
      "Butter": "Mantequilla", "Lilac": "Lila", "Blush": "Rubor",
      "Sand": "Arena", "Cotton Candy": "Algodón de Azúcar", "Seafoam": "Espuma",
    };

    // EXACT-match only. We used to fall back to a partial-word regex which
    // produced "Activo duels", "Todos-time", "Estudiar marathon invites",
    // "Academic Perfil" etc. — bare entries like "Active": "Activo" would
    // grab one word and leave the rest English. Now if a phrase isn't in
    // T verbatim it stays English (and we translate it at the source).
    function translate(el) {
      if (el.childElementCount === 0) {
        var raw = el.textContent;
        var txt = raw.trim();
        if (!txt) return;
        if (T[txt]) {
          el.textContent = raw.replace(txt, T[txt]);
        }
      }
      if (el.placeholder && T[el.placeholder]) el.placeholder = T[el.placeholder];
      if (el.title && T[el.title]) el.title = T[el.title];
    }

    function runTranslate(){
      var root = document.querySelector('.container') || document.body;
      var walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT, null, false);
      while(walker.nextNode()) translate(walker.currentNode);
      // Belt-and-suspenders pass for common containers
      document.querySelectorAll('h1,h2,h3,h4,h5,label,button,a,th,td,li,p,span,div,option,summary,figcaption,small,strong,em,b,i').forEach(translate);
      // Translate <input type=button|submit value="...">
      document.querySelectorAll('input[type="button"],input[type="submit"]').forEach(function(el){
        if (el.value && T[el.value]) el.value = T[el.value];
      });
    }
    runTranslate();
    setTimeout(runTranslate, 400);
    setTimeout(runTranslate, 1200);
    setTimeout(runTranslate, 3000);
    // Re-translate when DOM changes (modals, async loads, tab switches)
    try {
      var _mo = new MutationObserver(function(muts){
        var any = false;
        for (var i=0; i<muts.length; i++){
          if (muts[i].addedNodes && muts[i].addedNodes.length){ any = true; break; }
        }
        if (any) { clearTimeout(window._mrTrTimer); window._mrTrTimer = setTimeout(runTranslate, 150); }
      });
      _mo.observe(document.body, {childList:true, subtree:true});
    } catch(_){}

    var origAlert = window.alert;
    window.alert = function(msg) {
      var raw = String(msg || '').trim();
      origAlert(T[raw] || raw);
    };
  })();
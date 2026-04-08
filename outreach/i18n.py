"""
Internationalization — EN/ES translations for MachReach.
Usage:  from outreach.i18n import t
        t("nav.dashboard")  # returns translated string based on session lang
"""
from __future__ import annotations
from flask import session

TRANSLATIONS = {
    # ── Navigation ──────────────────────────────────────────────
    "nav.dashboard": {"en": "Dashboard", "es": "Panel"},
    "nav.new_campaign": {"en": "New Campaign", "es": "Nueva Campaña"},
    "nav.inbox": {"en": "Inbox", "es": "Bandeja"},
    "nav.ab_tests": {"en": "A/B Tests", "es": "Pruebas A/B"},
    "nav.send_times": {"en": "Send Times", "es": "Horarios"},
    "nav.calendar": {"en": "Calendar", "es": "Calendario"},
    "nav.export": {"en": "Export", "es": "Exportar"},
    "nav.mail_hub": {"en": "Mail Hub", "es": "Correo"},
    "nav.contacts": {"en": "Contacts", "es": "Contactos"},
    "nav.billing": {"en": "Billing", "es": "Facturación"},
    "nav.settings": {"en": "Settings", "es": "Ajustes"},
    "nav.logout": {"en": "Logout", "es": "Salir"},
    "nav.pricing": {"en": "Pricing", "es": "Precios"},
    "nav.login": {"en": "Login", "es": "Iniciar Sesión"},
    "nav.get_started": {"en": "Get Started", "es": "Comenzar"},

    # ── Landing page ────────────────────────────────────────────
    "landing.hero_title": {"en": "Cold email outreach,<br><span>powered by AI</span>", "es": "Outreach por email,<br><span>impulsado por IA</span>"},
    "landing.hero_desc": {"en": "Generate personalized email sequences, A/B test subject lines, track opens and replies &mdash; all on autopilot.", "es": "Genera secuencias de email personalizadas, prueba asuntos A/B, rastrea aperturas y respuestas &mdash; todo en automático."},
    "landing.start_free": {"en": "Start Free", "es": "Empezar Gratis"},
    "landing.see_pricing": {"en": "See Pricing", "es": "Ver Precios"},
    "landing.ai_emails": {"en": "AI-Written Emails", "es": "Emails con IA"},
    "landing.ai_emails_desc": {"en": "GPT generates entire sequences tailored to your audience and tone.", "es": "GPT genera secuencias completas adaptadas a tu audiencia y tono."},
    "landing.mail_hub": {"en": "Mail Hub", "es": "Centro de Correo"},
    "landing.mail_hub_desc": {"en": "AI-triaged inbox management. Auto-classify, prioritize, snooze &amp; schedule replies.", "es": "Gestión de bandeja con IA. Clasifica, prioriza, pospone y programa respuestas."},
    "landing.track": {"en": "Track Everything", "es": "Rastrea Todo"},
    "landing.track_desc": {"en": "Open tracking, reply detection, sentiment analysis, and per-campaign analytics.", "es": "Rastreo de aperturas, detección de respuestas, análisis de sentimiento y analíticas por campaña."},
    "landing.automated": {"en": "Fully Automated", "es": "Totalmente Automatizado"},
    "landing.automated_desc": {"en": "Follow-ups, A/B tests, smart send times &mdash; all on autopilot.", "es": "Follow-ups, pruebas A/B, horarios inteligentes &mdash; todo en automático."},
    "landing.free_forever": {"en": "Free forever for small teams. Plans start at <b>$8.000 CLP/month</b>.", "es": "Gratis para siempre para equipos pequeños. Planes desde <b>$8.000 CLP/mes</b>."},

    # ── Auth ────────────────────────────────────────────────────
    "auth.create_account": {"en": "Create your account", "es": "Crea tu cuenta"},
    "auth.create_subtitle": {"en": "Start sending smarter cold emails in minutes.", "es": "Empieza a enviar cold emails más inteligentes en minutos."},
    "auth.full_name": {"en": "Full Name", "es": "Nombre Completo"},
    "auth.email": {"en": "Email", "es": "Correo Electrónico"},
    "auth.password": {"en": "Password", "es": "Contraseña"},
    "auth.business_name": {"en": "Business Name", "es": "Nombre de Empresa"},
    "auth.optional": {"en": "optional", "es": "opcional"},
    "auth.create_btn": {"en": "Create Account", "es": "Crear Cuenta"},
    "auth.have_account": {"en": "Already have an account?", "es": "¿Ya tienes cuenta?"},
    "auth.log_in": {"en": "Log in", "es": "Iniciar sesión"},
    "auth.welcome_back": {"en": "Welcome back", "es": "Bienvenido de vuelta"},
    "auth.sign_in_desc": {"en": "Sign in to manage your campaigns.", "es": "Inicia sesión para gestionar tus campañas."},
    "auth.sign_in": {"en": "Sign In", "es": "Iniciar Sesión"},
    "auth.no_account": {"en": "Don't have an account?", "es": "¿No tienes cuenta?"},
    "auth.sign_up_free": {"en": "Sign up free", "es": "Regístrate gratis"},
    "auth.all_required": {"en": "All fields are required.", "es": "Todos los campos son obligatorios."},
    "auth.email_exists": {"en": "An account with that email already exists.", "es": "Ya existe una cuenta con ese correo."},
    "auth.invalid_creds": {"en": "Invalid email or password.", "es": "Correo o contraseña inválidos."},
    "auth.forgot_password": {"en": "Forgot password?", "es": "¿Olvidaste tu contraseña?"},
    "auth.reset_title": {"en": "Reset Password", "es": "Recuperar Contraseña"},
    "auth.reset_desc": {"en": "Enter your email and we'll send you a reset link.", "es": "Ingresa tu correo y te enviaremos un enlace de recuperación."},
    "auth.send_reset": {"en": "Send Reset Link", "es": "Enviar Enlace"},
    "auth.reset_sent": {"en": "If an account exists with that email, a reset link has been sent.", "es": "Si existe una cuenta con ese correo, se ha enviado un enlace de recuperación."},
    "auth.new_password": {"en": "New Password", "es": "Nueva Contraseña"},
    "auth.confirm_password": {"en": "Confirm Password", "es": "Confirmar Contraseña"},
    "auth.reset_btn": {"en": "Reset Password", "es": "Cambiar Contraseña"},
    "auth.reset_success": {"en": "Password updated! You can now log in.", "es": "¡Contraseña actualizada! Ya puedes iniciar sesión."},
    "auth.reset_invalid": {"en": "This reset link is invalid or has expired.", "es": "Este enlace es inválido o ha expirado."},
    "auth.passwords_no_match": {"en": "Passwords do not match.", "es": "Las contraseñas no coinciden."},

    # ── Security / Settings ─────────────────────────────────────
    "settings.security": {"en": "Security", "es": "Seguridad"},
    "settings.change_password": {"en": "Change Password", "es": "Cambiar Contraseña"},
    "settings.current_password": {"en": "Current Password", "es": "Contraseña Actual"},
    "settings.new_password": {"en": "New Password", "es": "Nueva Contraseña"},
    "settings.confirm_password": {"en": "Confirm New Password", "es": "Confirmar Nueva Contraseña"},
    "settings.update_password": {"en": "Update Password", "es": "Actualizar Contraseña"},
    "settings.wrong_password": {"en": "Current password is incorrect.", "es": "La contraseña actual es incorrecta."},
    "settings.password_updated": {"en": "Password updated successfully.", "es": "Contraseña actualizada exitosamente."},
    "settings.active_sessions": {"en": "Active Sessions", "es": "Sesiones Activas"},
    "settings.logout_all": {"en": "Log Out All Other Sessions", "es": "Cerrar Todas las Otras Sesiones"},
    "settings.delete_account": {"en": "Delete Account", "es": "Eliminar Cuenta"},
    "settings.delete_warning": {"en": "This will permanently delete your account and all data. This cannot be undone.", "es": "Esto eliminará permanentemente tu cuenta y todos los datos. No se puede deshacer."},
    "settings.delete_confirm": {"en": "Type DELETE to confirm", "es": "Escribe ELIMINAR para confirmar"},
    "settings.account_deleted": {"en": "Account deleted.", "es": "Cuenta eliminada."},

    # ── Dashboard ───────────────────────────────────────────────
    "dash.title": {"en": "Dashboard", "es": "Panel"},
    "dash.campaigns": {"en": "Campaigns", "es": "Campañas"},
    "dash.emails_sent": {"en": "Emails Sent", "es": "Emails Enviados"},
    "dash.replies": {"en": "Replies", "es": "Respuestas"},
    "dash.open_rate": {"en": "Open Rate", "es": "Tasa de Apertura"},
    "dash.recent_campaigns": {"en": "Recent Campaigns", "es": "Campañas Recientes"},
    "dash.name": {"en": "Name", "es": "Nombre"},
    "dash.status": {"en": "Status", "es": "Estado"},
    "dash.sent": {"en": "Sent", "es": "Enviados"},
    "dash.opened": {"en": "Opened", "es": "Abiertos"},
    "dash.replied": {"en": "Replied", "es": "Respondidos"},
    "dash.actions": {"en": "Actions", "es": "Acciones"},
    "dash.view": {"en": "View", "es": "Ver"},
    "dash.no_campaigns": {"en": "No campaigns yet.", "es": "Aún no hay campañas."},
    "dash.create_first": {"en": "Create your first campaign to get started.", "es": "Crea tu primera campaña para comenzar."},
    "dash.new_campaign": {"en": "New Campaign", "es": "Nueva Campaña"},
    "dash.upgrade": {"en": "Upgrade", "es": "Mejorar Plan"},

    # ── Onboarding ──────────────────────────────────────────────
    "onboard.welcome": {"en": "Welcome to MachReach!", "es": "¡Bienvenido a MachReach!"},
    "onboard.subtitle": {"en": "Get started in 3 easy steps:", "es": "Empieza en 3 simples pasos:"},
    "onboard.step1_title": {"en": "Connect Your Email", "es": "Conecta Tu Email"},
    "onboard.step1_desc": {"en": "Go to Settings and add your Gmail or SMTP account.", "es": "Ve a Ajustes y agrega tu cuenta Gmail o SMTP."},
    "onboard.step2_title": {"en": "Create a Campaign", "es": "Crea una Campaña"},
    "onboard.step2_desc": {"en": "Hit 'New Campaign' to build your first outreach sequence.", "es": "Haz clic en 'Nueva Campaña' para crear tu primera secuencia."},
    "onboard.step3_title": {"en": "Track Replies", "es": "Rastrea Respuestas"},
    "onboard.step3_desc": {"en": "Check your Inbox and Mail Hub for replies with AI sentiment analysis.", "es": "Revisa tu Bandeja y Centro de Correo para respuestas con análisis de sentimiento IA."},
    "onboard.go_settings": {"en": "Go to Settings", "es": "Ir a Ajustes"},

    # ── Settings ────────────────────────────────────────────────
    "settings.title": {"en": "Settings", "es": "Ajustes"},
    "settings.profile": {"en": "Profile", "es": "Perfil"},
    "settings.save": {"en": "Save Changes", "es": "Guardar Cambios"},
    "settings.email_accounts": {"en": "Email Accounts", "es": "Cuentas de Email"},
    "settings.add_email": {"en": "Add Email Account", "es": "Agregar Cuenta de Email"},
    "settings.email_addr": {"en": "Email Address", "es": "Dirección de Email"},
    "settings.app_password": {"en": "App Password", "es": "Contraseña de Aplicación"},
    "settings.imap_host": {"en": "IMAP Host", "es": "Servidor IMAP"},
    "settings.imap_port": {"en": "IMAP Port", "es": "Puerto IMAP"},
    "settings.smtp_host": {"en": "SMTP Host", "es": "Servidor SMTP"},
    "settings.smtp_port": {"en": "SMTP Port", "es": "Puerto SMTP"},
    "settings.label": {"en": "Label", "es": "Etiqueta"},
    "settings.connect": {"en": "Connect Account", "es": "Conectar Cuenta"},
    "settings.connecting": {"en": "Testing connection...", "es": "Probando conexión..."},
    "settings.default": {"en": "Default", "es": "Predeterminado"},
    "settings.make_default": {"en": "Make Default", "es": "Hacer Predeterminado"},
    "settings.remove": {"en": "Remove", "es": "Eliminar"},
    "settings.daily_limit": {"en": "Daily limits scale with your plan", "es": "Los límites diarios escalan con tu plan"},
    "settings.saved": {"en": "Settings saved.", "es": "Ajustes guardados."},

    # ── New Campaign ────────────────────────────────────────────
    "campaign.new_title": {"en": "New Campaign", "es": "Nueva Campaña"},
    "campaign.name": {"en": "Campaign Name", "es": "Nombre de Campaña"},
    "campaign.target_audience": {"en": "Target Audience", "es": "Audiencia Objetivo"},
    "campaign.tone": {"en": "Tone", "es": "Tono"},
    "campaign.tone_professional": {"en": "Professional", "es": "Profesional"},
    "campaign.tone_friendly": {"en": "Friendly", "es": "Amigable"},
    "campaign.tone_casual": {"en": "Casual", "es": "Casual"},
    "campaign.tone_urgent": {"en": "Urgent", "es": "Urgente"},
    "campaign.sequence_count": {"en": "Number of Emails", "es": "Cantidad de Emails"},
    "campaign.product": {"en": "Product / Service", "es": "Producto / Servicio"},
    "campaign.contacts_csv": {"en": "Contacts (CSV)", "es": "Contactos (CSV)"},
    "campaign.csv_format": {"en": "CSV format: name,email per line", "es": "Formato CSV: nombre,email por línea"},
    "campaign.create_btn": {"en": "Create Campaign", "es": "Crear Campaña"},
    "campaign.select_account": {"en": "Send From", "es": "Enviar Desde"},

    # ── Campaign View ───────────────────────────────────────────
    "campaign.sequences": {"en": "Sequences", "es": "Secuencias"},
    "campaign.contacts": {"en": "Contacts", "es": "Contactos"},
    "campaign.activity": {"en": "Activity", "es": "Actividad"},
    "campaign.overview": {"en": "Overview", "es": "Resumen"},
    "campaign.start": {"en": "Start Campaign", "es": "Iniciar Campaña"},
    "campaign.pause": {"en": "Pause", "es": "Pausar"},
    "campaign.resume": {"en": "Resume", "es": "Reanudar"},
    "campaign.duplicate": {"en": "Duplicate", "es": "Duplicar"},
    "campaign.delete": {"en": "Delete", "es": "Eliminar"},
    "campaign.edit": {"en": "Edit", "es": "Editar"},
    "campaign.add_contacts": {"en": "Add Contacts", "es": "Agregar Contactos"},
    "campaign.subject": {"en": "Subject", "es": "Asunto"},
    "campaign.delay": {"en": "Delay", "es": "Retraso"},
    "campaign.preview": {"en": "Preview", "es": "Vista Previa"},
    "campaign.no_sequences": {"en": "No sequences yet.", "es": "Aún no hay secuencias."},

    # ── Mail Hub ────────────────────────────────────────────────
    "mail.title": {"en": "Mail Hub", "es": "Centro de Correo"},
    "mail.all": {"en": "All", "es": "Todos"},
    "mail.unread": {"en": "Unread", "es": "No Leídos"},
    "mail.starred": {"en": "Starred", "es": "Destacados"},
    "mail.snoozed": {"en": "Snoozed", "es": "Pospuestos"},
    "mail.archived": {"en": "Archived", "es": "Archivados"},
    "mail.sync_now": {"en": "Sync Now", "es": "Sincronizar"},
    "mail.compose": {"en": "Compose", "es": "Redactar"},
    "mail.reply": {"en": "Reply", "es": "Responder"},
    "mail.archive": {"en": "Archive", "es": "Archivar"},
    "mail.snooze": {"en": "Snooze", "es": "Posponer"},
    "mail.delete": {"en": "Delete", "es": "Eliminar"},
    "mail.mark_read": {"en": "Mark Read", "es": "Marcar Leído"},
    "mail.mark_unread": {"en": "Mark Unread", "es": "Marcar No Leído"},
    "mail.no_emails": {"en": "No emails found.", "es": "No se encontraron emails."},
    "mail.from": {"en": "From", "es": "De"},
    "mail.to": {"en": "To", "es": "Para"},
    "mail.date": {"en": "Date", "es": "Fecha"},
    "mail.search": {"en": "Search emails...", "es": "Buscar emails..."},
    "mail.send": {"en": "Send", "es": "Enviar"},
    "mail.sending": {"en": "Sending...", "es": "Enviando..."},
    "mail.ai_draft": {"en": "AI Draft", "es": "Borrador IA"},
    "mail.generating": {"en": "Generating...", "es": "Generando..."},

    # ── Contacts ────────────────────────────────────────────────
    "contacts.title": {"en": "Contacts Book", "es": "Libro de Contactos"},
    "contacts.add_new": {"en": "Add Contact", "es": "Agregar Contacto"},
    "contacts.search": {"en": "Search contacts...", "es": "Buscar contactos..."},
    "contacts.name": {"en": "Name", "es": "Nombre"},
    "contacts.email": {"en": "Email", "es": "Correo"},
    "contacts.company": {"en": "Company", "es": "Empresa"},
    "contacts.phone": {"en": "Phone", "es": "Teléfono"},
    "contacts.tags": {"en": "Tags", "es": "Etiquetas"},
    "contacts.notes": {"en": "Notes", "es": "Notas"},
    "contacts.relationship": {"en": "Relationship", "es": "Relación"},
    "contacts.save": {"en": "Save Contact", "es": "Guardar Contacto"},
    "contacts.no_contacts": {"en": "No contacts yet.", "es": "Aún no hay contactos."},

    # ── Billing ─────────────────────────────────────────────────
    "billing.title": {"en": "Billing & Plan", "es": "Facturación y Plan"},
    "billing.current_usage": {"en": "Current Usage (This Month)", "es": "Uso Actual (Este Mes)"},
    "billing.choose_plan": {"en": "Choose Your Plan", "es": "Elige Tu Plan"},
    "billing.current_plan": {"en": "Current Plan", "es": "Plan Actual"},
    "billing.upgrade_to": {"en": "Upgrade to", "es": "Mejorar a"},
    "billing.switch_to": {"en": "Switch to", "es": "Cambiar a"},
    "billing.downgrade": {"en": "Downgrade", "es": "Bajar Plan"},
    "billing.free": {"en": "Free", "es": "Gratis"},
    "billing.growth": {"en": "Growth", "es": "Crecimiento"},
    "billing.pro": {"en": "Pro", "es": "Pro"},
    "billing.unlimited": {"en": "Unlimited", "es": "Ilimitado"},
    "billing.emails_month": {"en": "Emails Sent", "es": "Emails Enviados"},
    "billing.hub_syncs": {"en": "Mail Hub Syncs", "es": "Sincronizaciones"},
    "billing.payment_success": {"en": "Payment successful! Your plan has been upgraded.", "es": "¡Pago exitoso! Tu plan ha sido mejorado."},
    "billing.checkout_canceled": {"en": "Checkout canceled. No changes made.", "es": "Pago cancelado. Sin cambios."},
    "billing.not_configured": {"en": "Billing is not configured yet. Add your Lemon Squeezy keys to .env to enable payments.", "es": "Facturación no configurada. Agrega tus claves de Lemon Squeezy en .env."},

    # ── Inbox (Reply Inbox) ─────────────────────────────────────
    "inbox.title": {"en": "Reply Inbox", "es": "Bandeja de Respuestas"},
    "inbox.all": {"en": "All", "es": "Todas"},
    "inbox.positive": {"en": "Positive", "es": "Positivas"},
    "inbox.neutral": {"en": "Neutral", "es": "Neutral"},
    "inbox.negative": {"en": "Negative", "es": "Negativas"},
    "inbox.no_replies": {"en": "No replies yet.", "es": "Aún no hay respuestas."},

    # ── Export ──────────────────────────────────────────────────
    "export.title": {"en": "Export Data", "es": "Exportar Datos"},
    "export.download": {"en": "Download CSV", "es": "Descargar CSV"},

    # ── Calendar ────────────────────────────────────────────────
    "calendar.title": {"en": "Send Calendar", "es": "Calendario de Envíos"},

    # ── Smart Times ─────────────────────────────────────────────
    "smart.title": {"en": "Smart Send Times", "es": "Horarios Inteligentes"},

    # ── A/B Tests ───────────────────────────────────────────────
    "ab.title": {"en": "A/B Test Results", "es": "Resultados Pruebas A/B"},

    # ── Common ──────────────────────────────────────────────────
    "common.save": {"en": "Save", "es": "Guardar"},
    "common.cancel": {"en": "Cancel", "es": "Cancelar"},
    "common.delete": {"en": "Delete", "es": "Eliminar"},
    "common.edit": {"en": "Edit", "es": "Editar"},
    "common.back": {"en": "Back", "es": "Volver"},
    "common.search": {"en": "Search", "es": "Buscar"},
    "common.loading": {"en": "Loading...", "es": "Cargando..."},
    "common.active": {"en": "Active", "es": "Activo"},
    "common.paused": {"en": "Paused", "es": "Pausado"},
    "common.draft": {"en": "Draft", "es": "Borrador"},
    "common.completed": {"en": "Completed", "es": "Completado"},
    "common.error": {"en": "An error occurred.", "es": "Ocurrió un error."},
    "common.success": {"en": "Operation completed successfully.", "es": "Operación completada exitosamente."},

    # ── Pricing (public) ────────────────────────────────────────
    "pricing.title": {"en": "Simple, Transparent Pricing", "es": "Precios Simples y Transparentes"},
    "pricing.subtitle": {"en": "Start free. Upgrade when you need more volume.", "es": "Empieza gratis. Mejora cuando necesites más volumen."},
    "pricing.per_month": {"en": "/mo", "es": "/mes"},
    "pricing.emails_month": {"en": "emails/month", "es": "emails/mes"},
    "pricing.emails_day": {"en": "emails/day", "es": "emails/día"},
    "pricing.campaigns": {"en": "campaigns", "es": "campañas"},
    "pricing.mailboxes": {"en": "mailboxes", "es": "buzones"},
    "pricing.unlimited": {"en": "Unlimited", "es": "Ilimitado"},
    "pricing.most_popular": {"en": "MOST POPULAR", "es": "MÁS POPULAR"},
}


def get_lang() -> str:
    """Get current language from session, default to English."""
    return session.get("lang", "en")


def t(key: str) -> str:
    """Get translated string for current language."""
    lang = get_lang()
    entry = TRANSLATIONS.get(key)
    if entry is None:
        return key
    return entry.get(lang, entry.get("en", key))


def t_dict(prefix: str) -> dict:
    """Get all translations for a prefix as a flat dict.
    t_dict("nav") returns {"dashboard": "Panel", "inbox": "Bandeja", ...}
    """
    lang = get_lang()
    result = {}
    prefix_dot = prefix + "."
    for key, val in TRANSLATIONS.items():
        if key.startswith(prefix_dot):
            short_key = key[len(prefix_dot):]
            result[short_key] = val.get(lang, val.get("en", key))
    return result

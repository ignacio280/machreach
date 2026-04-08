"""
Flask web dashboard — client-facing campaign management.
"""
from __future__ import annotations

import hashlib
import html as html_module
import os

from flask import Flask, flash, jsonify, redirect, render_template_string, request, session, url_for, Response
from markupsafe import Markup

from outreach.ai import generate_sequence, personalize_email, generate_reply_draft, get_optimal_send_hour
from outreach.config import SECRET_KEY, SENDER_NAME
from outreach.i18n import t, t_dict
from outreach.db import (
    add_contacts,
    create_campaign,
    create_client,
    delete_campaign,
    delete_contact,
    delete_sequence,
    duplicate_campaign,
    get_campaign,
    get_campaign_stats,
    get_campaigns,
    get_client,
    get_client_by_email,
    get_campaign_contacts,
    get_contacts,
    get_export_data,
    get_global_stats,
    get_reply_context,
    get_sent_emails,
    get_sequences,
    init_db,
    save_sequence,
    update_campaign_status,
    update_client,
    update_sequence,
)

app = Flask(__name__)
app.secret_key = SECRET_KEY

# Ensure DB is initialized (for gunicorn and direct run)
init_db()


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _hash_pw(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()


def _logged_in() -> bool:
    return "client_id" in session


@app.before_request
def _validate_session():
    if "client_id" in session:
        from outreach.db import get_db
        with get_db() as db:
            row = db.execute("SELECT 1 FROM clients WHERE id = ?",
                             (session["client_id"],)).fetchone()
            if row is None:
                session.clear()


def _esc(text: str) -> str:
    """HTML-escape user content to prevent XSS."""
    return html_module.escape(str(text)) if text else ""


# ---------------------------------------------------------------------------
# HTML Layout
# ---------------------------------------------------------------------------

LAYOUT = """<!DOCTYPE html>
<html lang="{{lang}}" data-theme="">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>MachReach — {{title}}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <script>
    // Apply saved theme immediately to prevent flash
    (function(){var t=localStorage.getItem('machreach-theme');if(t)document.documentElement.setAttribute('data-theme',t);})();
  </script>
  <style>
    :root {
      --bg: #F0F2F5;
      --card: #FFFFFF;
      --primary: #6366F1;
      --primary-hover: #4F46E5;
      --primary-light: #EEF2FF;
      --primary-dark: #3730A3;
      --green: #10B981;
      --green-hover: #059669;
      --green-light: #D1FAE5;
      --green-dark: #065F46;
      --red: #EF4444;
      --red-hover: #DC2626;
      --red-light: #FEE2E2;
      --yellow: #F59E0B;
      --yellow-light: #FEF3C7;
      --blue: #3B82F6;
      --blue-light: #DBEAFE;
      --text: #1E293B;
      --text-secondary: #64748B;
      --text-muted: #94A3B8;
      --border: #E2E8F0;
      --border-light: #F1F5F9;
      --shadow: 0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04);
      --shadow-md: 0 4px 6px -1px rgba(0,0,0,0.08), 0 2px 4px -2px rgba(0,0,0,0.06);
      --shadow-lg: 0 10px 15px -3px rgba(0,0,0,0.08), 0 4px 6px -4px rgba(0,0,0,0.06);
      --radius: 12px;
      --radius-sm: 8px;
      --radius-xs: 6px;
    }

    /* Dark mode */
    :root[data-theme="dark"] {
      --bg: #0F172A;
      --card: #1E293B;
      --primary-light: #312E81;
      --primary-dark: #C7D2FE;
      --green-light: #064E3B;
      --green-dark: #6EE7B7;
      --red-light: #7F1D1D;
      --yellow-light: #78350F;
      --blue-light: #1E3A5F;
      --text: #E2E8F0;
      --text-secondary: #94A3B8;
      --text-muted: #64748B;
      --border: #334155;
      --border-light: #283548;
      --shadow: 0 1px 3px rgba(0,0,0,0.3), 0 1px 2px rgba(0,0,0,0.2);
      --shadow-md: 0 4px 6px -1px rgba(0,0,0,0.4), 0 2px 4px -2px rgba(0,0,0,0.3);
      --shadow-lg: 0 10px 15px -3px rgba(0,0,0,0.5), 0 4px 6px -4px rgba(0,0,0,0.4);
    }
    :root[data-theme="dark"] .nav {
      background: linear-gradient(135deg, #020617 0%, #0F172A 100%);
      border-bottom-color: rgba(255,255,255,0.04);
    }
    :root[data-theme="dark"] tbody tr:hover td { background: #334155; }
    :root[data-theme="dark"] .hero h1 span { -webkit-text-fill-color: #A5B4FC; }
    :root[data-theme="dark"] .badge-yellow { background: #422006; color: #FCD34D; }
    :root[data-theme="dark"] .badge-blue { background: #172554; color: #93C5FD; }
    :root[data-theme="dark"] .badge-red { background: #450A0A; color: #FCA5A5; }
    :root[data-theme="dark"] .badge-green { background: #052E16; color: #6EE7B7; }
    :root[data-theme="dark"] .badge-gray { background: #334155; color: #CBD5E1; }
    :root[data-theme="dark"] .badge-purple { background: #312E81; color: #C7D2FE; }
    :root[data-theme="dark"] .stat-card { border-color: #334155; }
    :root[data-theme="dark"] .flash-success { background: #064E3B; color: #6EE7B7; border-color: #065F46; }
    :root[data-theme="dark"] .flash-error { background: #450A0A; color: #FCA5A5; border-color: #7F1D1D; }
    :root[data-theme="dark"] .seq-body { background: #0F172A; border-color: #334155; }
    :root[data-theme="dark"] .seq-card { border-left-color: #818CF8; }
    :root[data-theme="dark"] .auth-card { border-color: #334155; }
    :root[data-theme="dark"] .tab { color: #94A3B8; }
    :root[data-theme="dark"] .tab:hover { color: #E2E8F0; }
    :root[data-theme="dark"] .tab.active { color: #A5B4FC; border-bottom-color: #818CF8; }
    :root[data-theme="dark"] .feature { border-color: #334155; }
    :root[data-theme="dark"] input, :root[data-theme="dark"] textarea, :root[data-theme="dark"] select {
      background: #0F172A; color: #E2E8F0; border-color: #334155;
    }
    :root[data-theme="dark"] input::placeholder, :root[data-theme="dark"] textarea::placeholder {
      color: #475569;
    }

    * { margin:0; padding:0; box-sizing:border-box; }
    body { font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: var(--bg); color: var(--text); line-height: 1.6; -webkit-font-smoothing: antialiased; }

    /* Nav */
    .nav {
      background: linear-gradient(135deg, #0F172A 0%, #1E293B 100%);
      padding: 0 48px; display: flex; align-items: center; justify-content: space-between;
      height: 56px; position: sticky; top: 0; z-index: 100;
      border-bottom: 1px solid rgba(255,255,255,0.06);
    }
    .nav .brand { color: #fff; font-weight: 800; font-size: 17px; letter-spacing: -0.5px; display: flex; align-items: center; gap: 10px; text-decoration: none; }
    .nav .brand-icon { width: 28px; height: 28px; background: linear-gradient(135deg, var(--primary), #8B5CF6); border-radius: 7px; display: flex; align-items: center; justify-content: center; font-size: 13px; color: #fff; }
    .nav-links { display: flex; align-items: center; gap: 1px; }
    .nav-links a { color: #94A3B8; text-decoration: none; font-size: 13px; font-weight: 500; padding: 6px 12px; border-radius: var(--radius-xs); transition: all 0.15s; }
    .nav-links a:hover { color: #E2E8F0; background: rgba(255,255,255,0.08); }
    .nav-links a.active { color: #fff; background: rgba(255,255,255,0.12); }
    .nav-links .nav-divider { width: 1px; height: 20px; background: rgba(255,255,255,0.1); margin: 0 4px; }
    .nav-links .nav-user { color: #64748B; font-size: 12px; margin-right: 4px; }

    /* Layout — edge-to-edge with comfortable padding */
    .container { max-width: 1600px; margin: 0 auto; padding: 28px 48px; }
    .container.container-wide { max-width: 100%; padding: 28px 48px; }
    .page-header { margin-bottom: 24px; }
    .page-header h1 { font-size: 26px; font-weight: 700; letter-spacing: -0.5px; }
    .page-header p { color: var(--text-secondary); margin-top: 2px; font-size: 14px; }
    .breadcrumb { font-size: 13px; color: var(--text-muted); margin-bottom: 16px; }
    .breadcrumb a { color: var(--text-muted); text-decoration: none; }
    .breadcrumb a:hover { color: var(--primary); }

    /* Cards */
    .card { background: var(--card); border-radius: var(--radius); padding: 24px; box-shadow: var(--shadow); margin-bottom: 16px; border: 1px solid var(--border); }
    .card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; padding-bottom: 14px; border-bottom: 1px solid var(--border-light); }
    .card-header h2 { font-size: 16px; font-weight: 600; }

    /* Stats */
    .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 14px; margin-bottom: 20px; }
    .stat-card { background: var(--card); border-radius: var(--radius); padding: 18px; text-align: center; box-shadow: var(--shadow); border: 1px solid var(--border); }
    .stat-card .num { font-size: 28px; font-weight: 800; letter-spacing: -1px; }
    .stat-card .label { font-size: 10px; text-transform: uppercase; letter-spacing: 1px; color: var(--text-muted); margin-top: 2px; font-weight: 600; }
    .stat-purple .num { color: var(--primary); }
    .stat-green .num { color: var(--green); }
    .stat-blue .num { color: var(--blue); }
    .stat-yellow .num { color: var(--yellow); }
    .stat-red .num { color: var(--red); }

    /* Progress bar */
    .progress-wrap { background: var(--border-light); border-radius: 20px; height: 6px; overflow: hidden; margin-top: 8px; }
    .progress-bar { height: 100%; border-radius: 20px; transition: width 0.5s ease; }
    .progress-bar.bar-purple { background: linear-gradient(90deg, var(--primary), #8B5CF6); }
    .progress-bar.bar-green { background: linear-gradient(90deg, var(--green), #34D399); }

    /* Forms */
    label { display: block; font-size: 12px; font-weight: 600; color: var(--text-secondary); margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.5px; }
    input, textarea, select {
      width: 100%; padding: 9px 13px; border: 1.5px solid var(--border); border-radius: var(--radius-sm);
      font-size: 14px; margin-bottom: 14px; background: var(--card); color: var(--text);
      transition: border-color 0.2s, box-shadow 0.2s; font-family: inherit;
    }
    input:focus, textarea:focus, select:focus { outline: none; border-color: var(--primary); box-shadow: 0 0 0 3px var(--primary-light); }
    textarea { min-height: 90px; resize: vertical; }
    input[type="file"] { padding: 8px; cursor: pointer; }
    .form-hint { font-size: 11px; color: var(--text-muted); margin-top: -10px; margin-bottom: 14px; }
    .form-group { margin-bottom: 2px; }
    .form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
    .form-divider { border: none; border-top: 1px dashed var(--border); margin: 16px 0; }

    /* Buttons */
    .btn {
      display: inline-flex; align-items: center; gap: 5px;
      padding: 9px 18px; font-size: 13px; font-weight: 600; cursor: pointer;
      text-decoration: none; border: none; border-radius: var(--radius-xs);
      transition: all 0.15s; font-family: inherit; white-space: nowrap;
    }
    .btn-primary { background: var(--primary); color: #fff; }
    .btn-primary:hover { background: var(--primary-hover); box-shadow: var(--shadow-md); }
    .btn-green { background: var(--green); color: #fff; }
    .btn-green:hover { background: var(--green-hover); }
    .btn-red { background: var(--red); color: #fff; }
    .btn-red:hover { background: var(--red-hover); }
    .btn-yellow { background: var(--yellow); color: #fff; }
    .btn-yellow:hover { background: #D97706; }
    .btn-outline { background: transparent; color: var(--text-secondary); border: 1.5px solid var(--border); }
    .btn-outline:hover { border-color: var(--text-secondary); color: var(--text); background: var(--border-light); }
    .btn-ghost { background: transparent; color: var(--text-muted); padding: 6px 10px; }
    .btn-ghost:hover { color: var(--text); background: var(--border-light); }
    .btn-sm { padding: 5px 12px; font-size: 12px; }
    .btn-lg { padding: 12px 28px; font-size: 15px; }
    .btn-group { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
    .btn-icon { width: 32px; height: 32px; padding: 0; display: inline-flex; align-items: center; justify-content: center; border-radius: var(--radius-xs); }

    /* Tables */
    table { width: 100%; border-collapse: collapse; }
    th { text-align: left; padding: 8px 12px; color: var(--text-muted); font-weight: 600; font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 2px solid var(--border-light); }
    td { padding: 10px 12px; border-bottom: 1px solid var(--border-light); font-size: 13px; }
    tr:last-child td { border-bottom: none; }
    tbody tr { transition: background 0.1s; }
    tbody tr:hover td { background: #FAFBFC; }

    /* Badges */
    .badge { padding: 2px 9px; border-radius: 20px; font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; display: inline-block; }
    .badge-green { background: var(--green-light); color: var(--green-dark); }
    .badge-yellow { background: var(--yellow-light); color: #92400E; }
    .badge-gray { background: var(--border-light); color: var(--text-secondary); }
    .badge-blue { background: var(--blue-light); color: #1E40AF; }
    .badge-red { background: var(--red-light); color: #991B1B; }
    .badge-purple { background: var(--primary-light); color: var(--primary-dark); }

    /* Flash messages */
    .flash { padding: 12px 16px; border-radius: var(--radius-sm); margin-bottom: 12px; font-size: 13px; font-weight: 500; display: flex; align-items: center; gap: 8px; animation: slideDown 0.25s ease; }
    .flash-success { background: var(--green-light); color: var(--green-dark); border: 1px solid #A7F3D0; }
    .flash-error { background: var(--red-light); color: #991B1B; border: 1px solid #FECACA; }

    /* Sequence cards */
    .seq-card { background: var(--card); border-radius: var(--radius); padding: 20px; box-shadow: var(--shadow); margin-bottom: 12px; border: 1px solid var(--border-light); border-left: 3px solid var(--primary); position: relative; }
    .seq-card .seq-actions { position: absolute; top: 16px; right: 16px; display: flex; gap: 4px; opacity: 0; transition: opacity 0.15s; }
    .seq-card:hover .seq-actions { opacity: 1; }
    .seq-step { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; color: var(--primary); margin-bottom: 6px; display: flex; align-items: center; gap: 8px; }
    .seq-step .seq-delay { color: var(--text-muted); font-weight: 500; }
    .seq-subject { font-size: 13px; font-weight: 600; color: var(--text); }
    .seq-subject-label { font-size: 10px; color: var(--text-muted); font-weight: 600; text-transform: uppercase; }
    .seq-body { background: var(--bg); padding: 14px; border-radius: var(--radius-sm); font-size: 12px; line-height: 1.7; white-space: pre-wrap; margin-top: 10px; color: var(--text-secondary); border: 1px solid var(--border-light); }

    /* Tabs */
    .tabs { display: flex; border-bottom: 2px solid var(--border-light); margin-bottom: 20px; gap: 0; overflow-x: auto; }
    .tab { padding: 10px 18px; font-size: 13px; font-weight: 600; color: var(--text-muted); cursor: pointer; border-bottom: 2px solid transparent; margin-bottom: -2px; transition: all 0.15s; text-decoration: none; white-space: nowrap; }
    .tab:hover { color: var(--text); }
    .tab.active { color: var(--primary); border-bottom-color: var(--primary); }
    .tab .tab-count { background: var(--border-light); color: var(--text-muted); font-size: 10px; padding: 1px 6px; border-radius: 10px; margin-left: 4px; font-weight: 700; }
    .tab.active .tab-count { background: var(--primary-light); color: var(--primary); }

    /* Empty state */
    .empty { text-align: center; padding: 40px 24px; color: var(--text-muted); }
    .empty-icon { font-size: 40px; margin-bottom: 8px; opacity: 0.4; }
    .empty h3 { color: var(--text-secondary); margin-bottom: 6px; font-size: 15px; }
    .empty p { font-size: 13px; max-width: 300px; margin: 0 auto; }

    /* Auth pages */
    .auth-wrapper { max-width: 420px; margin: 60px auto; padding: 0 24px; }
    .auth-card { background: var(--card); border-radius: var(--radius); padding: 36px; box-shadow: var(--shadow-lg); border: 1px solid var(--border-light); }
    .auth-card h1 { font-size: 22px; text-align: center; margin-bottom: 6px; }
    .auth-card .subtitle { text-align: center; color: var(--text-muted); margin-bottom: 24px; font-size: 13px; }
    .auth-footer { text-align: center; margin-top: 18px; font-size: 13px; color: var(--text-muted); }
    .auth-footer a { color: var(--primary); font-weight: 600; text-decoration: none; }

    /* Hero */
    .hero { text-align: center; padding: 72px 24px 48px; }
    .hero h1 { font-size: 42px; font-weight: 800; letter-spacing: -1.5px; line-height: 1.1; margin-bottom: 14px; }
    .hero h1 span { background: linear-gradient(135deg, var(--primary), #8B5CF6); -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; }
    .hero p { font-size: 17px; color: var(--text-secondary); max-width: 480px; margin: 0 auto 28px; line-height: 1.6; }
    .features { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; max-width: 780px; margin: 0 auto; }
    .feature { background: var(--card); border-radius: var(--radius); padding: 28px 20px; text-align: center; box-shadow: var(--shadow); border: 1px solid var(--border-light); }
    .feature-icon { font-size: 28px; margin-bottom: 10px; }
    .feature h3 { font-size: 14px; margin-bottom: 4px; font-weight: 600; }
    .feature p { font-size: 12px; color: var(--text-muted); line-height: 1.5; }

    /* Activity */
    .activity-item { display: flex; align-items: flex-start; gap: 10px; padding: 10px 0; border-bottom: 1px solid var(--border-light); }
    .activity-item:last-child { border-bottom: none; }
    .activity-dot { width: 7px; height: 7px; border-radius: 50%; margin-top: 6px; flex-shrink: 0; }
    .activity-dot.sent { background: var(--blue); }
    .activity-dot.opened { background: var(--green); }
    .activity-dot.replied { background: var(--primary); }
    .activity-text { font-size: 13px; color: var(--text-secondary); }
    .activity-text strong { color: var(--text); font-weight: 600; }
    .activity-time { font-size: 11px; color: var(--text-muted); margin-top: 1px; }

    /* Preview modal */
    .preview-modal { display: none; position: fixed; inset: 0; z-index: 200; background: rgba(0,0,0,0.5); backdrop-filter: blur(4px); justify-content: center; align-items: center; }
    .preview-modal.show { display: flex; }
    .preview-content { background: var(--card); border-radius: var(--radius); width: 90%; max-width: 640px; max-height: 80vh; overflow-y: auto; box-shadow: var(--shadow-lg); }
    .preview-header { padding: 16px 20px; border-bottom: 1px solid var(--border-light); display: flex; justify-content: space-between; align-items: center; }
    .preview-header h3 { font-size: 15px; }
    .preview-body { padding: 24px; }
    .preview-field { margin-bottom: 12px; }
    .preview-field .pf-label { font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text-muted); font-weight: 600; margin-bottom: 2px; }
    .preview-field .pf-value { font-size: 14px; }
    .preview-email { background: var(--bg); border: 1px solid var(--border-light); border-radius: var(--radius-sm); padding: 20px; font-size: 14px; line-height: 1.7; white-space: pre-wrap; }

    /* Misc */
    .confirm-form { display: inline; }
    .divider { border: none; border-top: 1px solid var(--border-light); margin: 20px 0; }
    .text-muted { color: var(--text-muted); }
    .text-sm { font-size: 13px; }
    .text-xs { font-size: 11px; }
    .mt-2 { margin-top: 8px; }
    .mt-4 { margin-top: 16px; }
    .mb-4 { margin-bottom: 16px; }

    /* Spinner */
    .spinner { display: none; width: 18px; height: 18px; border: 2px solid rgba(255,255,255,0.3); border-top-color: #fff; border-radius: 50%; animation: spin 0.6s linear infinite; }
    .btn.loading .spinner { display: inline-block; }
    .btn.loading .btn-text { display: none; }
    @keyframes spin { to { transform: rotate(360deg); } }

    @keyframes slideDown { from { opacity: 0; transform: translateY(-6px); } to { opacity: 1; transform: translateY(0); } }

    /* Entrance animations */
    @keyframes fadeInUp {
      from { opacity: 0; transform: translateY(8px); }
      to { opacity: 1; transform: translateY(0); }
    }
    @keyframes fadeIn {
      from { opacity: 0; }
      to { opacity: 1; }
    }
    @keyframes scaleIn {
      from { opacity: 0; transform: scale(0.95); }
      to { opacity: 1; transform: scale(1); }
    }

    /* Apply animations — fast & subtle */
    .container { animation: fadeIn 0.15s ease; }
    .page-header { animation: fadeInUp 0.2s ease both; }

    .stat-card {
      animation: scaleIn 0.2s ease both;
      transition: transform 0.15s ease, box-shadow 0.15s ease;
    }
    .stat-card:hover { transform: translateY(-3px); box-shadow: var(--shadow-md); }

    .card {
      animation: fadeIn 0.2s ease both;
      transition: transform 0.15s ease, box-shadow 0.15s ease;
    }
    .card:hover { box-shadow: var(--shadow-md); }

    .badge { transition: transform 0.1s ease; }
    .badge:hover { transform: scale(1.05); }

    .btn {
      transition: all 0.15s cubic-bezier(0.4, 0, 0.2, 1);
    }
    .btn-primary:hover { transform: translateY(-1px); box-shadow: 0 4px 12px rgba(99,102,241,0.35); }
    .btn-primary:active { transform: translateY(0); }

    .seq-card {
      transition: transform 0.15s ease, box-shadow 0.15s ease, border-color 0.15s ease;
    }
    .seq-card:hover { transform: translateY(-2px); box-shadow: var(--shadow-md); border-left-color: #8B5CF6; }

    /* Smooth focus glow */
    input:focus, textarea:focus, select:focus {
      outline: none;
      border-color: var(--primary);
      box-shadow: 0 0 0 3px var(--primary-light);
      transition: border-color 0.2s ease, box-shadow 0.2s ease;
    }

    /* Progress bar animation */
    .progress-bar { transition: width 0.6s cubic-bezier(0.4, 0, 0.2, 1); }

    /* Hover lift for clickable cards/links */
    a.card:hover { transform: translateY(-2px); box-shadow: var(--shadow-md); }

    /* Smooth row hover */
    tbody tr { transition: background 0.1s ease; }

    /* Scrollbar styling */
    ::-webkit-scrollbar { width: 8px; height: 8px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }
    ::-webkit-scrollbar-thumb:hover { background: var(--text-muted); }

    @media (max-width: 1200px) {
      .container, .container.container-wide { padding: 24px 28px; }
      .nav { padding: 0 28px; }
    }
    @media (max-width: 900px) {
      .container, .container.container-wide { padding: 20px 20px; }
      .nav { padding: 0 16px; }
      .nav-links a { font-size: 12px; padding: 5px 8px; }
    }
    @media (max-width: 640px) {
      .container, .container.container-wide { padding: 16px; }
      .stats-grid { grid-template-columns: repeat(2, 1fr); }
      .form-row { grid-template-columns: 1fr; }
      .features { grid-template-columns: 1fr; }
      .hero h1 { font-size: 30px; }
      .nav { padding: 0 12px; }
      .seq-card .seq-actions { opacity: 1; }
      .nav-links a { font-size: 11px; padding: 4px 6px; }
      .nav-links .nav-divider { display: none; }
    }
  </style>
</head>
<body>
  <div class="nav">
    <a href="/" class="brand">
      <div class="brand-icon">&#9993;</div>
      MachReach
    </a>
    <div class="nav-links">
      {% if logged_in %}
        <a href="/dashboard" {% if active_page == 'dashboard' %}class="active"{% endif %}>{{nav.dashboard}}</a>
        <a href="/campaign/new" {% if active_page == 'new_campaign' %}class="active"{% endif %}>{{nav.new_campaign}}</a>
        <a href="/inbox" {% if active_page == 'inbox' %}class="active"{% endif %}>{{nav.inbox}}</a>
        <a href="/ab-tests" {% if active_page == 'ab_tests' %}class="active"{% endif %}>{{nav.ab_tests}}</a>
        <a href="/smart-times" {% if active_page == 'smart_times' %}class="active"{% endif %}>&#9201; {{nav.send_times}}</a>
        <a href="/calendar" {% if active_page == 'calendar' %}class="active"{% endif %}>{{nav.calendar}}</a>
        <a href="/export" {% if active_page == 'export' %}class="active"{% endif %}>&#128202; {{nav.export}}</a>
        <div class="nav-divider"></div>
        <a href="/mail-hub" {% if active_page == 'mail_hub' %}class="active"{% endif %} style="{% if active_page == 'mail_hub' %}color:var(--primary);{% endif %}">&#128233; {{nav.mail_hub}}</a>
        <a href="/contacts" {% if active_page == 'contacts' %}class="active"{% endif %} style="{% if active_page == 'contacts' %}color:var(--primary);{% endif %}">&#128101; {{nav.contacts}}</a>
        <a href="/billing" {% if active_page == 'billing' %}class="active"{% endif %}>&#128179; {{nav.billing}}</a>
        <a href="/settings" {% if active_page == 'settings' %}class="active"{% endif %}>{{nav.settings}}</a>
        <button onclick="toggleDarkMode()" class="btn btn-ghost btn-sm" id="theme-toggle" title="Toggle dark mode" style="font-size:16px;padding:4px 8px;cursor:pointer;background:none;border:none;color:#94A3B8;">&#127769;</button>
        <a href="/set-language/{% if lang == 'en' %}es{% else %}en{% endif %}" class="btn btn-ghost btn-sm" style="font-size:12px;padding:4px 8px;color:#94A3B8;font-weight:700;" title="Switch language">{% if lang == 'en' %}ES{% else %}EN{% endif %}</a>
        <div class="nav-divider"></div>
        <span class="nav-user">{{client_name}}</span>
        <a href="/logout" style="color:#EF4444;">{{nav.logout}}</a>
      {% else %}
        <a href="/pricing">{{nav.pricing}}</a>
        <a href="/login">{{nav.login}}</a>
        <a href="/register" class="btn btn-primary btn-sm" style="color:#fff;">{{nav.get_started}}</a>
        <a href="/set-language/{% if lang == 'en' %}es{% else %}en{% endif %}" class="btn btn-ghost btn-sm" style="font-size:12px;padding:4px 8px;color:#94A3B8;font-weight:700;" title="Switch language">{% if lang == 'en' %}ES{% else %}EN{% endif %}</a>
      {% endif %}
    </div>
  </div>
  <div class="container{% if wide %} container-wide{% endif %}">
    {% for cat, msg in messages %}
      <div class="flash flash-{{cat}}">
        {% if cat == 'success' %}&#10003;{% else %}&#9888;{% endif %}
        {{msg}}
      </div>
    {% endfor %}
    {{content|safe}}
  </div>
  <script>
    // Loading button handler
    document.querySelectorAll('form[data-loading]').forEach(form => {
      form.addEventListener('submit', () => {
        const btn = form.querySelector('button[type=submit]');
        if (btn) btn.classList.add('loading');
      });
    });
    // Preview modal
    function showPreview(id) {
      document.getElementById('preview-' + id).classList.add('show');
    }
    function hidePreview(id) {
      document.getElementById('preview-' + id).classList.remove('show');
    }
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape') document.querySelectorAll('.preview-modal.show').forEach(m => m.classList.remove('show'));
    });

    // Live stats polling — refreshes every 15s
    (function() {
      const dashStatEls = {
        total_campaigns: document.querySelector('[data-stat="total_campaigns"]'),
        active_campaigns: document.querySelector('[data-stat="active_campaigns"]'),
        total_sent: document.querySelector('[data-stat="total_sent"]'),
        open_rate_fmt: document.querySelector('[data-stat="open_rate_fmt"]'),
        total_replied: document.querySelector('[data-stat="total_replied"]'),
        reply_rate_fmt: document.querySelector('[data-stat="reply_rate_fmt"]'),
      };
      const hasDashStats = Object.values(dashStatEls).some(el => el !== null);

      const campStatEls = {
        total_contacts: document.querySelector('[data-stat="camp_total_contacts"]'),
        emails_sent: document.querySelector('[data-stat="camp_emails_sent"]'),
        open_rate_fmt: document.querySelector('[data-stat="camp_open_rate_fmt"]'),
        reply_rate_fmt: document.querySelector('[data-stat="camp_reply_rate_fmt"]'),
      };
      const campId = document.querySelector('[data-campaign-id]');
      const hasCampStats = Object.values(campStatEls).some(el => el !== null);

      function refreshDashboardStats() {
        fetch('/api/stats')
          .then(r => r.json())
          .then(data => {
            if (data.error) return;
            for (const [key, el] of Object.entries(dashStatEls)) {
              if (el && data[key] !== undefined) {
                const newVal = String(data[key]);
                if (el.textContent !== newVal) {
                  el.textContent = newVal;
                  el.style.transition = 'color 0.3s';
                  el.style.color = 'var(--green)';
                  setTimeout(() => el.style.color = '', 1000);
                }
              }
            }
          })
          .catch(() => {});
      }

      function refreshCampaignStats() {
        if (!campId) return;
        const cid = campId.dataset.campaignId;
        fetch('/api/campaign/' + cid + '/stats')
          .then(r => r.json())
          .then(data => {
            if (data.error) return;
            for (const [key, el] of Object.entries(campStatEls)) {
              if (el && data[key] !== undefined) {
                const newVal = String(data[key]);
                if (el.textContent !== newVal) {
                  el.textContent = newVal;
                  el.style.transition = 'color 0.3s';
                  el.style.color = 'var(--green)';
                  setTimeout(() => el.style.color = '', 1000);
                }
              }
            }
          })
          .catch(() => {});
      }

      if (hasDashStats) {
        setInterval(refreshDashboardStats, 15000);
      }
      if (hasCampStats) {
        setInterval(refreshCampaignStats, 15000);
      }
    })();

    // --- Dark mode toggle ---
    function toggleDarkMode() {
      const html = document.documentElement;
      const current = html.getAttribute('data-theme');
      const next = current === 'dark' ? '' : 'dark';
      html.setAttribute('data-theme', next);
      localStorage.setItem('machreach-theme', next);
      const btn = document.getElementById('theme-toggle');
      if (btn) btn.innerHTML = next === 'dark' ? '&#9728;' : '&#127769;';
    }
    // Set correct icon on load
    (function(){
      var btn = document.getElementById('theme-toggle');
      if (btn && document.documentElement.getAttribute('data-theme') === 'dark') btn.innerHTML = '&#9728;';
    })();

    // --- Global keyboard shortcuts ---
    (function() {
      let selectedIdx = -1;
      function getRows() { return Array.from(document.querySelectorAll('tr[data-mail-id]')); }
      function selectRow(idx) {
        const rows = getRows();
        if (rows.length === 0) return;
        rows.forEach(r => r.style.outline = '');
        selectedIdx = Math.max(0, Math.min(idx, rows.length - 1));
        const row = rows[selectedIdx];
        row.style.outline = '2px solid var(--primary)';
        row.scrollIntoView({block: 'nearest'});
      }

      document.addEventListener('keydown', function(e) {
        // Skip if typing in input/textarea
        const tag = (e.target.tagName || '').toLowerCase();
        if (tag === 'input' || tag === 'textarea' || tag === 'select' || e.target.isContentEditable) return;

        const rows = getRows();
        if (rows.length === 0) return;

        switch(e.key) {
          case 'j': // Next email
            e.preventDefault();
            selectRow(selectedIdx + 1);
            break;
          case 'k': // Previous email
            e.preventDefault();
            selectRow(selectedIdx - 1);
            break;
          case 'o': // Open selected
          case 'Enter':
            if (selectedIdx >= 0 && selectedIdx < rows.length) {
              e.preventDefault();
              window.location = '/mail-hub/' + rows[selectedIdx].dataset.mailId;
            }
            break;
          case 'x': // Toggle checkbox
            if (selectedIdx >= 0 && selectedIdx < rows.length) {
              e.preventDefault();
              const cb = rows[selectedIdx].querySelector('input[type=checkbox]');
              if (cb) { cb.checked = !cb.checked; cb.dispatchEvent(new Event('change', {bubbles:true})); }
            }
            break;
          case 'e': // Archive
            if (selectedIdx >= 0 && selectedIdx < rows.length) {
              e.preventDefault();
              const id = rows[selectedIdx].dataset.mailId;
              if (typeof archiveEmail === 'function') archiveEmail(parseInt(id), rows[selectedIdx].querySelector('button'));
            }
            break;
          case 's': // Star
            if (selectedIdx >= 0 && selectedIdx < rows.length) {
              e.preventDefault();
              const starEl = rows[selectedIdx].querySelector('span[onclick*="toggleStar"]');
              if (starEl) starEl.click();
            }
            break;
          case '/': // Focus search
            e.preventDefault();
            const searchInput = document.getElementById('mail-search-input');
            if (searchInput) searchInput.focus();
            break;
          case '?': // Show shortcuts help
            e.preventDefault();
            const helpModal = document.getElementById('shortcuts-modal');
            if (helpModal) helpModal.style.display = helpModal.style.display === 'flex' ? 'none' : 'flex';
            break;
        }
      });
    })();
  </script>
</body>
</html>"""


def _render(title: str, content: str, active_page: str = "", wide: bool = False, **kwargs):
    flashed = list(session.pop("_flashes", []) if "_flashes" in session else [])
    nav = t_dict("nav")
    return render_template_string(
        LAYOUT,
        title=title,
        content=render_template_string(content, **kwargs),
        logged_in=_logged_in(),
        messages=flashed,
        active_page=active_page,
        client_name=session.get("client_name", ""),
        wide=wide,
        nav=nav,
        lang=session.get("lang", "en"),
    )


# ---------------------------------------------------------------------------
# Routes — Public
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    if _logged_in():
        return redirect(url_for("dashboard"))
    return render_template_string(LAYOUT, title="Home", logged_in=False, messages=[], active_page="home", client_name="", nav=t_dict("nav"), lang=session.get("lang", "en"), content=Markup(f"""
    <div class="hero">
      <h1>{t("landing.hero_title")}</h1>
      <p>{t("landing.hero_desc")}</p>
      <div class="btn-group" style="justify-content:center;">
        <a href="/register" class="btn btn-primary btn-lg">{t("landing.start_free")}</a>
        <a href="/pricing" class="btn btn-outline btn-lg">{t("landing.see_pricing")}</a>
        <a href="/login" class="btn btn-ghost btn-lg">{t("nav.login")}</a>
      </div>
    </div>
    <div class="features">
      <div class="feature"><div class="feature-icon">&#129302;</div><h3>{t("landing.ai_emails")}</h3><p>{t("landing.ai_emails_desc")}</p></div>
      <div class="feature"><div class="feature-icon">&#128233;</div><h3>{t("landing.mail_hub")}</h3><p>{t("landing.mail_hub_desc")}</p></div>
      <div class="feature"><div class="feature-icon">&#128200;</div><h3>{t("landing.track")}</h3><p>{t("landing.track_desc")}</p></div>
      <div class="feature"><div class="feature-icon">&#9889;</div><h3>{t("landing.automated")}</h3><p>{t("landing.automated_desc")}</p></div>
    </div>
    <div style="text-align:center;margin:32px 0;color:var(--text-muted);">
      <p>{t("landing.free_forever")}</p>
    </div>
    """))


# ---------------------------------------------------------------------------
# Routes — Auth
# ---------------------------------------------------------------------------

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        business = request.form.get("business", "").strip()
        if not name or not email or not password:
            flash(("error", t("auth.all_required")))
            return redirect(url_for("register"))
        if get_client_by_email(email):
            flash(("error", t("auth.email_exists")))
            return redirect(url_for("register"))
        client_id = create_client(name, email, _hash_pw(password), business)
        session["client_id"] = client_id
        session["client_name"] = name
        flash(("success", f"Welcome, {_esc(name)}! Create your first campaign to get started."))
        return redirect(url_for("dashboard"))
    return render_template_string(LAYOUT, title="Register", logged_in=False, messages=list(session.pop("_flashes", []) if "_flashes" in session else []), active_page="register", client_name="", nav=t_dict("nav"), lang=session.get("lang", "en"), content=Markup(f"""
    <div class="auth-wrapper">
      <div class="auth-card">
        <h1>{t("auth.create_account")}</h1>
        <p class="subtitle">{t("auth.create_subtitle")}</p>
        <form method="post">
          <div class="form-group"><label>{t("auth.full_name")}</label><input name="name" placeholder="John Doe" required></div>
          <div class="form-group"><label>{t("auth.email")}</label><input name="email" type="email" placeholder="john@company.com" required></div>
          <div class="form-group"><label>{t("auth.password")}</label><input name="password" type="password" placeholder="At least 6 characters" required minlength="6"></div>
          <div class="form-group"><label>{t("auth.business_name")} <span style="font-weight:400;text-transform:none;color:var(--text-muted);">({t("auth.optional")})</span></label><input name="business" placeholder="Acme Inc."></div>
          <button class="btn btn-primary" type="submit" style="width:100%;justify-content:center;">{t("auth.create_btn")}</button>
        </form>
        <div class="auth-footer">{t("auth.have_account")} <a href="/login">{t("auth.log_in")}</a></div>
      </div>
    </div>
    """))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        client = get_client_by_email(email)
        if not client or client["password"] != _hash_pw(password):
            flash(("error", t("auth.invalid_creds")))
            return redirect(url_for("login"))
        session["client_id"] = client["id"]
        session["client_name"] = client["name"]
        return redirect(url_for("dashboard"))
    return render_template_string(LAYOUT, title="Login", logged_in=False, messages=list(session.pop("_flashes", []) if "_flashes" in session else []), active_page="login", client_name="", nav=t_dict("nav"), lang=session.get("lang", "en"), content=Markup(f"""
    <div class="auth-wrapper">
      <div class="auth-card">
        <h1>{t("auth.welcome_back")}</h1>
        <p class="subtitle">{t("auth.sign_in_desc")}</p>
        <form method="post">
          <div class="form-group"><label>{t("auth.email")}</label><input name="email" type="email" placeholder="john@company.com" required></div>
          <div class="form-group"><label>{t("auth.password")}</label><input name="password" type="password" required></div>
          <button class="btn btn-primary" type="submit" style="width:100%;justify-content:center;">{t("auth.sign_in")}</button>
        </form>
        <div class="auth-footer">{t("auth.no_account")} <a href="/register">{t("auth.sign_up_free")}</a></div>
      </div>
    </div>
    """))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/set-language/<lang>")
def set_language(lang):
    if lang in ("en", "es"):
        session["lang"] = lang
    return redirect(request.referrer or url_for("index"))


# ---------------------------------------------------------------------------
# Routes — Dashboard
# ---------------------------------------------------------------------------

@app.route("/dashboard")
def dashboard():
    if not _logged_in():
        return redirect(url_for("login"))

    campaigns = get_campaigns(session["client_id"])
    gstats = get_global_stats(session["client_id"])

    # Check if user has connected an email account
    from outreach.db import get_email_accounts
    accounts = get_email_accounts(session["client_id"])
    has_accounts = len(accounts) > 0

    # Usage info for plan banner
    from outreach.db import get_subscription, get_usage
    from outreach.config import PLAN_LIMITS
    sub = get_subscription(session["client_id"])
    usage = get_usage(session["client_id"])
    plan = sub.get("plan", "free")
    limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
    email_limit = limits["emails_per_month"]
    emails_used = usage.get("emails_sent", 0)
    plan_label = plan.capitalize()
    if email_limit == -1:
        usage_text = f"<b>{plan_label}</b> plan &middot; {emails_used:,} emails sent this month (unlimited)"
    else:
        pct = min(emails_used / email_limit * 100, 100) if email_limit else 0
        usage_text = f"<b>{plan_label}</b> plan &middot; {emails_used:,} / {email_limit:,} emails ({pct:.0f}%)"
    upgrade_cta = "" if plan != "free" else ' &middot; <a href="/billing" style="color:var(--primary);font-weight:600;">Upgrade</a>'

    rows = ""
    for c in campaigns:
        stats = get_campaign_stats(c["id"])
        sc = {"active": "badge-green", "draft": "badge-gray", "paused": "badge-yellow", "completed": "badge-blue"}.get(c["status"], "badge-gray")
        pct = (stats["emails_sent"] / stats["total_contacts"] * 100) if stats["total_contacts"] else 0
        bar_class = "bar-green" if c["status"] == "active" else "bar-purple"
        rows += f"""<tr>
          <td>
            <a href="/campaign/{c['id']}" style="color:var(--primary);font-weight:600;text-decoration:none;">{_esc(c['name'])}</a>
            <div class="progress-wrap" style="width:100px;"><div class="progress-bar {bar_class}" style="width:{min(pct,100):.0f}%"></div></div>
          </td>
          <td><span class="badge {sc}">{c['status']}</span></td>
          <td>{stats['total_contacts']}</td>
          <td>{stats['emails_sent']}</td>
          <td>{stats['open_rate']:.0%}</td>
          <td>{stats['reply_rate']:.0%}</td>
          <td style="text-align:right;">
            <div class="btn-group">
              <a href="/campaign/{c['id']}" class="btn btn-outline btn-sm">View</a>
              <form method="post" action="/campaign/{c['id']}/duplicate" class="confirm-form"><button class="btn btn-ghost btn-sm" title="Duplicate">&#128203;</button></form>
            </div>
          </td>
        </tr>"""

    if not rows:
        rows = """<tr><td colspan="7">
          <div class="empty">
            <div class="empty-icon">&#128235;</div>
            <h3>No campaigns yet</h3>
            <p>Create your first campaign to start sending outreach emails.</p>
            <a href="/campaign/new" class="btn btn-primary mt-2">+ New Campaign</a>
          </div>
        </td></tr>"""

    return _render(t("dash.title"), """
    <div class="page-header">
      <h1>{{page_title}}</h1>
    </div>

    <div style="background:var(--card);border:1px solid var(--border);border-radius:8px;padding:12px 20px;margin-bottom:20px;font-size:14px;color:var(--text-muted);display:flex;align-items:center;justify-content:space-between;">
      <span>{{usage_text}}{{upgrade_cta}}</span>
      <a href="/billing" class="btn btn-ghost btn-sm">&#128179; Manage Plan</a>
    </div>

    {% if not has_accounts %}
    <div style="background:linear-gradient(135deg, var(--primary), #7c3aed);border-radius:12px;padding:28px 32px;margin-bottom:24px;color:#fff;">
      <h2 style="margin:0 0 8px;font-size:1.3rem;">&#128075; Welcome to MachReach!</h2>
      <p style="margin:0 0 16px;opacity:.9;font-size:.95rem;">Complete these steps to start sending outreach emails:</p>
      <div style="display:flex;gap:16px;flex-wrap:wrap;">
        <div style="background:rgba(255,255,255,.15);border-radius:10px;padding:16px 20px;flex:1;min-width:200px;">
          <div style="font-size:1.5rem;margin-bottom:6px;">1️⃣</div>
          <strong>Connect your email</strong>
          <p style="font-size:.85rem;opacity:.85;margin:4px 0 10px;">Add your Gmail or SMTP account so MachReach can send emails on your behalf.</p>
          <a href="/settings" style="background:#fff;color:var(--primary);padding:8px 16px;border-radius:8px;font-weight:700;font-size:.85rem;text-decoration:none;display:inline-block;">Go to Settings &rarr;</a>
        </div>
        <div style="background:rgba(255,255,255,.1);border-radius:10px;padding:16px 20px;flex:1;min-width:200px;opacity:.7;">
          <div style="font-size:1.5rem;margin-bottom:6px;">2️⃣</div>
          <strong>Create a campaign</strong>
          <p style="font-size:.85rem;opacity:.85;margin:4px 0 0;">Add contacts, write email sequences, and launch your first outreach.</p>
        </div>
        <div style="background:rgba(255,255,255,.1);border-radius:10px;padding:16px 20px;flex:1;min-width:200px;opacity:.7;">
          <div style="font-size:1.5rem;margin-bottom:6px;">3️⃣</div>
          <strong>Track &amp; manage replies</strong>
          <p style="font-size:.85rem;opacity:.85;margin:4px 0 0;">Monitor opens, replies, and manage conversations from Mail Hub.</p>
        </div>
      </div>
    </div>
    {% endif %}

    <div class="stats-grid">
      <div class="stat-card stat-purple"><div class="num" data-stat="total_campaigns">{{g.total_campaigns}}</div><div class="label">{{lbl_campaigns}}</div></div>
      <div class="stat-card stat-green"><div class="num" data-stat="active_campaigns">{{g.active_campaigns}}</div><div class="label">{{lbl_active}}</div></div>
      <div class="stat-card stat-blue"><div class="num" data-stat="total_sent">{{g.total_sent}}</div><div class="label">{{lbl_emails_sent}}</div></div>
      <div class="stat-card stat-purple"><div class="num" data-stat="open_rate_fmt">{{g_open_rate}}</div><div class="label">{{lbl_open_rate}}</div></div>
      <div class="stat-card stat-green"><div class="num" data-stat="total_replied">{{g.total_replied}}</div><div class="label">{{lbl_replies}}</div></div>
      <div class="stat-card stat-yellow"><div class="num" data-stat="reply_rate_fmt">{{g_reply_rate}}</div><div class="label">Reply Rate</div></div>
    </div>

    <div class="card">
      <div class="card-header">
        <h2>{{lbl_campaigns}}</h2>
        <a href="/campaign/new" class="btn btn-primary btn-sm">+ {{lbl_new_campaign}}</a>
      </div>
      <table>
        <thead><tr><th>{{lbl_name}}</th><th>{{lbl_status}}</th><th>Contacts</th><th>{{lbl_sent}}</th><th>{{lbl_opened}}</th><th>{{lbl_replied}}</th><th></th></tr></thead>
        <tbody>{{rows}}</tbody>
      </table>
    </div>
    """, active_page="dashboard", rows=Markup(rows), g=gstats,
        g_open_rate=f"{gstats['open_rate']:.0%}", g_reply_rate=f"{gstats['reply_rate']:.0%}",
        usage_text=Markup(usage_text), upgrade_cta=Markup(upgrade_cta),
        has_accounts=has_accounts,
        page_title=t("dash.title"),
        lbl_campaigns=t("dash.campaigns"), lbl_active=t("common.active"),
        lbl_emails_sent=t("dash.emails_sent"), lbl_open_rate=t("dash.open_rate"),
        lbl_replies=t("dash.replies"), lbl_new_campaign=t("dash.new_campaign"),
        lbl_name=t("dash.name"), lbl_status=t("dash.status"),
        lbl_sent=t("dash.sent"), lbl_opened=t("dash.opened"),
        lbl_replied=t("dash.replied"))


# ---------------------------------------------------------------------------
# Routes — Settings
# ---------------------------------------------------------------------------

@app.route("/settings", methods=["GET", "POST"])
def settings():
    if not _logged_in():
        return redirect(url_for("login"))
    client = get_client(session["client_id"])
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        business = request.form.get("business", "").strip()
        if name:
            update_client(session["client_id"], name, business)
            session["client_name"] = name
            flash(("success", "Settings saved."))
        return redirect(url_for("settings"))

    from outreach.db import get_email_accounts, get_subscription
    from outreach.config import PLAN_LIMITS
    accounts = get_email_accounts(session["client_id"])
    sub = get_subscription(session["client_id"])
    plan = sub.get("plan", "free") if sub else "free"
    limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
    max_mailboxes = limits.get("mailboxes", 1)
    can_add = max_mailboxes == -1 or len(accounts) < max_mailboxes

    accounts_html = ""
    for a in accounts:
        default_badge = '<span class="badge badge-blue" style="font-size:10px;margin-left:6px;">Default</span>' if a["is_default"] else ""
        accounts_html += f"""
        <div style="display:flex;justify-content:space-between;align-items:center;padding:14px 0;border-bottom:1px solid var(--border-light);">
          <div style="display:flex;align-items:center;gap:12px;">
            <div style="width:40px;height:40px;border-radius:50%;background:linear-gradient(135deg,var(--primary),#8B5CF6);display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:16px;">
              {_esc(a['email'][:1].upper())}
            </div>
            <div>
              <div style="font-weight:600;">{_esc(a['label'] or a['email'])}{default_badge}</div>
              <div style="font-size:13px;color:var(--text-muted);">{_esc(a['email'])}</div>
            </div>
          </div>
          <div style="display:flex;gap:8px;">
            {'<button class="btn btn-ghost btn-sm" onclick="setDefault(' + str(a['id']) + ')" style="font-size:12px;">Set Default</button>' if not a['is_default'] else ''}
            <button class="btn btn-ghost btn-sm" onclick="deleteAccount({a['id']})" style="font-size:12px;color:var(--red);">&#128465;</button>
          </div>
        </div>
        """

    if not accounts_html:
        accounts_html = '<p style="color:var(--text-muted);padding:20px 0;text-align:center;">No email accounts connected yet. Add one below to start using Mail Hub and sending emails.</p>'

    limit_text = "Unlimited" if max_mailboxes == -1 else str(max_mailboxes)

    return _render(t("settings.title"), f"""
    <div class="page-header">
      <h1>{t("settings.title")}</h1>
      <p>Manage your account details and email connections.</p>
    </div>
    <div class="card">
      <div class="card-header"><h2>Profile</h2></div>
      <form method="post">
        <div class="form-row">
          <div class="form-group"><label>Name</label><input name="name" value="{{{{client.name}}}}" required></div>
          <div class="form-group"><label>Business</label><input name="business" value="{{{{client.business or ''}}}}"></div>
        </div>
        <div class="form-group">
          <label>Email</label>
          <input value="{{{{client.email}}}}" disabled style="background:var(--border-light);color:var(--text-muted);">
          <p class="form-hint">Email cannot be changed.</p>
        </div>
        <button class="btn btn-primary" type="submit">Save Changes</button>
      </form>
    </div>

    <div class="card">
      <div class="card-header" style="display:flex;justify-content:space-between;align-items:center;">
        <h2>&#128231; Email Accounts</h2>
        <span style="font-size:13px;color:var(--text-muted);">{len(accounts)}/{limit_text} mailboxes</span>
      </div>
      <div id="accounts-list">
        {accounts_html}
      </div>
      {'<div style="margin-top:16px;"><button class="btn btn-primary" onclick="showAddAccount()" id="add-account-btn">&#43; Add Email Account</button></div>' if can_add else '<p style="margin-top:12px;font-size:13px;color:var(--text-muted);">Mailbox limit reached. <a href="/billing">Upgrade your plan</a> for more.</p>'}

      <div id="add-account-form" style="display:none;margin-top:16px;padding:20px;background:var(--bg);border-radius:var(--radius-sm);border:1px solid var(--border-light);">
        <h3 style="font-size:16px;margin-bottom:14px;">Add Email Account</h3>
        <div class="form-row">
          <div class="form-group"><label>Label</label><input id="acct-label" placeholder="Work Gmail, Personal, etc."></div>
          <div class="form-group"><label>Email Address</label><input id="acct-email" type="email" placeholder="you@example.com" required></div>
        </div>
        <div class="form-group">
          <label>App Password</label>
          <input id="acct-password" type="password" placeholder="Gmail: Settings > Security > App Passwords" required>
          <p class="form-hint">For Gmail, generate an <a href="https://myaccount.google.com/apppasswords" target="_blank">App Password</a>. For Outlook, use your account password with <a href="https://support.microsoft.com/en-us/account-billing/using-app-passwords-with-apps-that-don-t-support-two-step-verification-5896ed9b-4263-e681-128a-a6f2979a7944" target="_blank">app passwords</a>.</p>
        </div>
        <details style="margin-bottom:14px;">
          <summary style="font-size:13px;color:var(--text-muted);cursor:pointer;">Advanced Settings (IMAP/SMTP)</summary>
          <div style="margin-top:10px;">
            <div class="form-row">
              <div class="form-group"><label>IMAP Host</label><input id="acct-imap-host" value="imap.gmail.com"></div>
              <div class="form-group"><label>IMAP Port</label><input id="acct-imap-port" value="993" type="number"></div>
            </div>
            <div class="form-row">
              <div class="form-group"><label>SMTP Host</label><input id="acct-smtp-host" value="smtp.gmail.com"></div>
              <div class="form-group"><label>SMTP Port</label><input id="acct-smtp-port" value="465" type="number"></div>
            </div>
          </div>
        </details>
        <div style="display:flex;gap:8px;">
          <button class="btn btn-primary" onclick="addAccount()" id="save-account-btn">&#128274; Test &amp; Add Account</button>
          <button class="btn btn-ghost" onclick="hideAddAccount()">Cancel</button>
        </div>
        <div id="add-account-status" style="margin-top:10px;font-size:13px;"></div>
      </div>
    </div>

    <div class="card">
      <div class="card-header"><h2>Sending Configuration</h2></div>
      <div style="font-size:13px;color:var(--text-secondary);line-height:1.7;">
        <p><strong>Sender Name:</strong> {{{{sender_name}}}}</p>
        <p><strong>Daily Limit:</strong> {{{{daily_limit}}}} emails/day (based on your plan)</p>
        <p><strong>Delay Between Emails:</strong> 60 seconds</p>
        <p class="text-xs text-muted mt-2">Daily limits scale with your plan: Free=50, Growth=200, Pro=500, Unlimited=∞</p>
      </div>
    </div>

    <script>
    function showAddAccount() {{
      document.getElementById('add-account-form').style.display = 'block';
      document.getElementById('add-account-btn').style.display = 'none';
    }}
    function hideAddAccount() {{
      document.getElementById('add-account-form').style.display = 'none';
      document.getElementById('add-account-btn').style.display = '';
    }}
    function addAccount() {{
      const btn = document.getElementById('save-account-btn');
      const status = document.getElementById('add-account-status');
      btn.disabled = true;
      btn.innerHTML = '&#8987; Testing connection...';
      status.innerHTML = '';
      fetch('/api/email-accounts', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{
          label: document.getElementById('acct-label').value,
          email: document.getElementById('acct-email').value,
          password: document.getElementById('acct-password').value,
          imap_host: document.getElementById('acct-imap-host').value,
          imap_port: parseInt(document.getElementById('acct-imap-port').value),
          smtp_host: document.getElementById('acct-smtp-host').value,
          smtp_port: parseInt(document.getElementById('acct-smtp-port').value),
        }})
      }}).then(r => r.json()).then(data => {{
        if (data.id) {{
          window.location.reload();
        }} else {{
          status.innerHTML = '<span style="color:var(--red);">&#9888; ' + (data.error || 'Failed to add account') + '</span>';
          btn.disabled = false;
          btn.innerHTML = '&#128274; Test &amp; Add Account';
        }}
      }}).catch(() => {{
        status.innerHTML = '<span style="color:var(--red);">&#9888; Connection error</span>';
        btn.disabled = false;
        btn.innerHTML = '&#128274; Test &amp; Add Account';
      }});
    }}
    function setDefault(id) {{
      fetch('/api/email-accounts/' + id + '/default', {{method: 'POST'}})
        .then(() => window.location.reload());
    }}
    function deleteAccount(id) {{
      if (!confirm('Remove this email account? Emails already synced will be kept.')) return;
      fetch('/api/email-accounts/' + id, {{method: 'DELETE'}})
        .then(() => window.location.reload());
    }}
    </script>
    """, active_page="settings", client=client, sender_name=SENDER_NAME,
        daily_limit="Unlimited" if limits["emails_per_day"] == -1 else str(limits["emails_per_day"]))


# ---------------------------------------------------------------------------
# Routes — Reply Inbox
# ---------------------------------------------------------------------------

@app.route("/inbox")
def inbox():
    if not _logged_in():
        return redirect(url_for("login"))

    from outreach.db import get_inbox_all
    emails = get_inbox_all(session["client_id"])

    # Group by contact
    contacts = {}
    for e in emails:
        cid = e["contact_id"]
        if cid not in contacts:
            contacts[cid] = {
                "contact_name": e["contact_name"],
                "contact_email": e["contact_email"],
                "company": e["company"],
                "role": e["role"],
                "contact_status": e["contact_status"],
                "campaign_name": e["campaign_name"],
                "campaign_id": e["campaign_id"],
                "emails": [],
                "reply_sentiment": "",
            }
        contacts[cid]["emails"].append(e)
        # Capture sentiment from the replied email
        if e.get("reply_sentiment"):
            contacts[cid]["reply_sentiment"] = e["reply_sentiment"]

    # Sort: replied first, then by latest email date
    def sort_key(item):
        c = item[1]
        is_replied = 1 if c["contact_status"] == "replied" else 0
        latest = c["emails"][0]["sent_at"] if c["emails"] else ""
        return (-is_replied, latest)

    sorted_contacts = sorted(contacts.items(), key=sort_key, reverse=True)

    # Filter
    filter_val = request.args.get("filter", "all")

    # Build inbox HTML
    thread_cards = ""
    count_all = len(sorted_contacts)
    count_replied = sum(1 for _, c in sorted_contacts if c["contact_status"] == "replied")
    count_opened = sum(1 for _, c in sorted_contacts if any(e["email_status"] in ("opened", "clicked") for e in c["emails"]))
    count_pending = sum(1 for _, c in sorted_contacts if c["contact_status"] == "sent")
    count_positive = sum(1 for _, c in sorted_contacts if c["reply_sentiment"] == "positive")
    count_negative = sum(1 for _, c in sorted_contacts if c["reply_sentiment"] == "negative")
    count_neutral = sum(1 for _, c in sorted_contacts if c["reply_sentiment"] == "neutral")

    for cid, c in sorted_contacts:
        if filter_val == "replied" and c["contact_status"] != "replied":
            continue
        if filter_val == "opened" and not any(e["email_status"] in ("opened", "clicked") for e in c["emails"]):
            continue
        if filter_val == "pending" and c["contact_status"] != "sent":
            continue
        if filter_val == "positive" and c["reply_sentiment"] != "positive":
            continue
        if filter_val == "negative" and c["reply_sentiment"] != "negative":
            continue
        if filter_val == "neutral" and c["reply_sentiment"] != "neutral":
            continue

        status_map = {
            "replied": ("Replied", "badge-green"),
            "sent": ("Sent", "badge-blue"),
            "opened": ("Opened", "badge-blue"),
            "pending": ("Pending", ""),
            "bounced": ("Bounced", "badge-red"),
            "unsubscribed": ("Unsub", "badge-red"),
        }
        st = c["contact_status"]
        badge_text, badge_cls = status_map.get(st, (st.title(), ""))
        # Check if any email was opened
        if st == "sent" and any(e["email_status"] in ("opened", "clicked") for e in c["emails"]):
            badge_text, badge_cls = "Opened", "badge-blue"

        # Sentiment badge
        sentiment_badge = ""
        if c["reply_sentiment"] == "positive":
            sentiment_badge = '<span class="badge badge-green" style="margin-left:4px;">&#128077; Positive</span>'
        elif c["reply_sentiment"] == "negative":
            sentiment_badge = '<span class="badge badge-red" style="margin-left:4px;">&#128078; Negative</span>'
        elif c["reply_sentiment"] == "neutral":
            sentiment_badge = '<span class="badge badge-yellow" style="margin-left:4px;">&#8212; Neutral</span>'

        email_timeline = ""
        for e in reversed(c["emails"]):  # oldest first
            step_label = f"Step {e['step']}" if e["step"] else ""
            time_str = e["sent_at"][:16] if e["sent_at"] else ""
            opened_str = ""
            if e.get("opened_at"):
                opened_str = f' &middot; <span style="color:var(--green);">Opened {e["opened_at"][:16]}</span>'
            replied_str = ""
            if e.get("replied_at"):
                replied_str = f' &middot; <span style="color:var(--green);font-weight:600;">Replied {e["replied_at"][:16]}</span>'

            email_timeline += f"""
            <div style="padding:10px 0;border-bottom:1px solid var(--border-light);">
              <div style="display:flex;justify-content:space-between;align-items:center;">
                <strong style="font-size:13px;">&#9993; You sent: {_esc(e['subject'])}</strong>
                <span style="font-size:11px;color:var(--text-muted);">{step_label} &middot; Variant {e['variant'].upper()}</span>
              </div>
              <div style="font-size:12px;color:var(--text-secondary);margin-top:4px;">
                Sent {time_str}{opened_str}{replied_str}
              </div>
              <div style="font-size:12px;color:var(--text-secondary);margin-top:6px;line-height:1.5;max-height:60px;overflow:hidden;">
                {_esc(e['body'][:200])}{'...' if len(e['body']) > 200 else ''}
              </div>
            </div>"""

            # Show reply body if exists
            reply_body = e.get("reply_body", "")
            if reply_body:
                sent_color = {"positive": "var(--green-light)", "negative": "var(--red-light)", "neutral": "var(--yellow-light)"}.get(e.get("reply_sentiment", ""), "var(--border-light)")
                sent_border = {"positive": "var(--green)", "negative": "var(--red)", "neutral": "var(--yellow)"}.get(e.get("reply_sentiment", ""), "var(--border)")
                email_timeline += f"""
            <div style="padding:12px;margin:8px 0 8px 20px;background:{sent_color};border-left:3px solid {sent_border};border-radius:0 8px 8px 0;">
              <div style="font-size:11px;font-weight:700;color:var(--text-secondary);margin-bottom:4px;">&#8617; Reply from {_esc(c['contact_name'] or c['contact_email'])}</div>
              <div style="font-size:13px;color:var(--text);line-height:1.6;white-space:pre-wrap;">{_esc(reply_body[:2000])}</div>
            </div>"""

        thread_cards += f"""
        <div class="card" style="margin-bottom:12px;">
          <div style="display:flex;align-items:center;gap:10px;padding-bottom:10px;border-bottom:1px solid var(--border-light);">
            <div style="width:36px;height:36px;background:var(--primary-light);border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:700;color:var(--primary);font-size:14px;">
              {_esc(c['contact_name'][:1].upper() if c['contact_name'] else '?')}
            </div>
            <div style="flex:1;">
              <div style="font-weight:600;font-size:14px;">{_esc(c['contact_name'] or c['contact_email'])}</div>
              <div style="font-size:12px;color:var(--text-secondary);">{_esc(c['contact_email'])} &middot; {_esc(c['company'])} &middot; {_esc(c['role'])}</div>
            </div>
            <span class="badge {badge_cls}">{badge_text}</span>
            {sentiment_badge}
            <a href="/campaign/{c['campaign_id']}" style="font-size:11px;color:var(--primary);">{_esc(c['campaign_name'])}</a>
          </div>
          {email_timeline}
          {f'''<div style="padding-top:10px;display:flex;gap:8px;align-items:center;">
            <a href="mailto:{_esc(c["contact_email"])}?subject=Re: {_esc(c["emails"][0]["subject"])}" class="btn btn-outline btn-sm">&#9993; Manual Reply</a>
            <button onclick="generateDraft({c['emails'][0]['sent_id']}, this)" class="btn btn-primary btn-sm">&#129302; AI Draft Reply</button>
          </div>
          <div id="draft-{c['emails'][0]['sent_id']}" style="display:none;margin-top:10px;padding:14px;background:var(--primary-light);border:1px solid var(--primary);border-radius:8px;">
            <div style="font-size:11px;font-weight:700;color:var(--primary-dark);margin-bottom:6px;">&#129302; AI-Suggested Reply</div>
            <div id="draft-text-{c['emails'][0]['sent_id']}" style="font-size:13px;line-height:1.6;white-space:pre-wrap;color:var(--text);"></div>
            <div style="margin-top:10px;display:flex;gap:8px;">
              <button onclick="copyDraft({c['emails'][0]['sent_id']})" class="btn btn-green btn-sm">&#128203; Copy to Clipboard</button>
              <button onclick="generateDraft({c['emails'][0]['sent_id']}, this)" class="btn btn-outline btn-sm">&#128260; Regenerate</button>
            </div>
          </div>''' if c['contact_status'] == 'replied' else ''}
        </div>"""

    if not thread_cards:
        thread_cards = """<div class="empty" style="padding:40px;">
          <div class="empty-icon">&#128236;</div>
          <h3>No emails yet</h3>
          <p>Emails will appear here once your campaigns start sending.</p>
        </div>"""

    return _render(t("inbox.title"), f"""
    <div class="breadcrumb"><a href="/dashboard">{t("dash.title")}</a> / {t("inbox.title")}</div>
    <div class="page-header" style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:12px;">
      <div>
        <h1>&#128236; {t("inbox.title")}</h1>
        <p class="subtitle">Track every email sent, see who opened, and respond to replies.</p>
      </div>
      <button onclick="checkRepliesNow()" class="btn btn-primary btn-sm" id="check-replies-btn">&#128260; Check Replies Now</button>
    </div>

    <div style="display:flex;gap:8px;margin-bottom:20px;flex-wrap:wrap;">
      <a href="?filter=all" class="btn btn-sm {'btn-primary' if filter_val == 'all' else 'btn-outline'}">All ({count_all})</a>
      <a href="?filter=replied" class="btn btn-sm {'btn-primary' if filter_val == 'replied' else 'btn-outline'}">&#9989; Replied ({count_replied})</a>
      <a href="?filter=opened" class="btn btn-sm {'btn-primary' if filter_val == 'opened' else 'btn-outline'}">&#128065; Opened ({count_opened})</a>
      <a href="?filter=pending" class="btn btn-sm {'btn-primary' if filter_val == 'pending' else 'btn-outline'}">&#9203; Pending ({count_pending})</a>
      <span style="width:1px;background:var(--border);margin:0 4px;"></span>
      <a href="?filter=positive" class="btn btn-sm {'btn-green' if filter_val == 'positive' else 'btn-outline'}" style="{'color:#fff;' if filter_val == 'positive' else ''}">&#128077; Positive ({count_positive})</a>
      <a href="?filter=neutral" class="btn btn-sm {'btn-yellow' if filter_val == 'neutral' else 'btn-outline'}" style="{'color:#fff;' if filter_val == 'neutral' else ''}">&#8212; Neutral ({count_neutral})</a>
      <a href="?filter=negative" class="btn btn-sm {'btn-red' if filter_val == 'negative' else 'btn-outline'}" style="{'color:#fff;' if filter_val == 'negative' else ''}">&#128078; Negative ({count_negative})</a>
    </div>

    {thread_cards}

    <script>
    function checkRepliesNow() {{
      const btn = document.getElementById('check-replies-btn');
      btn.innerHTML = '&#8987; Checking...';
      btn.disabled = true;
      fetch('/api/check-replies', {{method: 'POST'}})
        .then(r => r.json())
        .then(data => {{
          if (data.new_replies > 0) {{
            btn.innerHTML = '&#9989; Found ' + data.new_replies + ' new reply(s)! Refreshing...';
            setTimeout(() => location.reload(), 1000);
          }} else {{
            btn.innerHTML = '&#10003; Up to date';
            setTimeout(() => {{ btn.innerHTML = '&#128260; Check Replies Now'; btn.disabled = false; }}, 2000);
          }}
        }})
        .catch(() => {{
          btn.innerHTML = '&#9888; Error checking';
          setTimeout(() => {{ btn.innerHTML = '&#128260; Check Replies Now'; btn.disabled = false; }}, 2000);
        }});
    }}

    function generateDraft(sentId, btn) {{
      btn.innerHTML = '&#8987; Generating...';
      btn.disabled = true;
      fetch('/api/reply-draft/' + sentId, {{method: 'POST'}})
        .then(r => r.json())
        .then(data => {{
          if (data.draft) {{
            document.getElementById('draft-text-' + sentId).textContent = data.draft;
            document.getElementById('draft-' + sentId).style.display = 'block';
            btn.innerHTML = '&#129302; AI Draft Reply';
            btn.disabled = false;
          }} else {{
            btn.innerHTML = '&#9888; ' + (data.error || 'Failed');
            setTimeout(() => {{ btn.innerHTML = '&#129302; AI Draft Reply'; btn.disabled = false; }}, 2000);
          }}
        }})
        .catch(() => {{
          btn.innerHTML = '&#9888; Error';
          setTimeout(() => {{ btn.innerHTML = '&#129302; AI Draft Reply'; btn.disabled = false; }}, 2000);
        }});
    }}

    function copyDraft(sentId) {{
      const text = document.getElementById('draft-text-' + sentId).textContent;
      navigator.clipboard.writeText(text).then(() => {{
        const btn = event.target;
        btn.innerHTML = '&#9989; Copied!';
        setTimeout(() => btn.innerHTML = '&#128203; Copy to Clipboard', 2000);
      }});
    }}
    </script>
    """, active_page="inbox")


# ---------------------------------------------------------------------------
# Routes — A/B Test Dashboard
# ---------------------------------------------------------------------------

@app.route("/ab-tests")
def ab_tests():
    if not _logged_in():
        return redirect(url_for("login"))

    from outreach.db import get_ab_stats, get_send_time_stats

    raw = get_ab_stats(session["client_id"])
    time_stats = get_send_time_stats(session["client_id"])

    # Group by sequence
    tests = {}
    for r in raw:
        sid = r["seq_id"]
        if sid not in tests:
            tests[sid] = {
                "campaign_name": r["campaign_name"],
                "campaign_id": r["campaign_id"],
                "step": r["step"],
                "subject_a": r["subject_a"],
                "subject_b": r["subject_b"],
                "a": {"sent": 0, "opens": 0, "replies": 0},
                "b": {"sent": 0, "opens": 0, "replies": 0},
            }
        v = r["variant"] or "a"
        if v in ("a", "b"):
            tests[sid][v]["sent"] = r["sent_count"] or 0
            tests[sid][v]["opens"] = r["opens"] or 0
            tests[sid][v]["replies"] = r["replies"] or 0

    # Build test cards
    cards = ""
    for sid, t in tests.items():
        a, b = t["a"], t["b"]
        a_rate = (a["opens"] / a["sent"] * 100) if a["sent"] else 0
        b_rate = (b["opens"] / b["sent"] * 100) if b["sent"] else 0
        a_reply = (a["replies"] / a["sent"] * 100) if a["sent"] else 0
        b_reply = (b["replies"] / b["sent"] * 100) if b["sent"] else 0

        winner = ""
        if a["sent"] >= 5 and b["sent"] >= 5:
            if a_rate > b_rate + 5:
                winner = "a"
            elif b_rate > a_rate + 5:
                winner = "b"

        a_bar_color = "var(--green)" if winner == "a" else "var(--primary)"
        b_bar_color = "var(--green)" if winner == "b" else "var(--primary)"
        a_winner = ' <span style="color:var(--green);font-weight:700;">&#9733; WINNER</span>' if winner == "a" else ""
        b_winner = ' <span style="color:var(--green);font-weight:700;">&#9733; WINNER</span>' if winner == "b" else ""

        cards += f"""
        <div class="card" style="margin-bottom:16px;">
          <div class="card-header">
            <h2>Step {t['step']} — <a href="/campaign/{t['campaign_id']}" style="color:var(--primary);">{_esc(t['campaign_name'])}</a></h2>
          </div>

          <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:8px;">
            <div style="padding:12px;border:2px solid {'var(--green)' if winner == 'a' else 'var(--border-light)'};border-radius:8px;">
              <div style="font-size:11px;font-weight:700;color:var(--text-muted);margin-bottom:4px;">VARIANT A{a_winner}</div>
              <div style="font-size:13px;font-weight:600;margin-bottom:8px;">{_esc(t['subject_a'])}</div>
              <div style="display:flex;gap:16px;font-size:12px;">
                <div><span style="font-weight:700;font-size:18px;">{a['sent']}</span> sent</div>
                <div><span style="font-weight:700;font-size:18px;color:var(--blue);">{a_rate:.0f}%</span> opens</div>
                <div><span style="font-weight:700;font-size:18px;color:var(--green);">{a_reply:.0f}%</span> replies</div>
              </div>
              <div style="margin-top:8px;background:var(--border-light);border-radius:4px;height:8px;overflow:hidden;">
                <div style="width:{a_rate}%;height:100%;background:{a_bar_color};border-radius:4px;transition:width 0.3s;"></div>
              </div>
            </div>

            <div style="padding:12px;border:2px solid {'var(--green)' if winner == 'b' else 'var(--border-light)'};border-radius:8px;">
              <div style="font-size:11px;font-weight:700;color:var(--text-muted);margin-bottom:4px;">VARIANT B{b_winner}</div>
              <div style="font-size:13px;font-weight:600;margin-bottom:8px;">{_esc(t['subject_b'])}</div>
              <div style="display:flex;gap:16px;font-size:12px;">
                <div><span style="font-weight:700;font-size:18px;">{b['sent']}</span> sent</div>
                <div><span style="font-weight:700;font-size:18px;color:var(--blue);">{b_rate:.0f}%</span> opens</div>
                <div><span style="font-weight:700;font-size:18px;color:var(--green);">{b_reply:.0f}%</span> replies</div>
              </div>
              <div style="margin-top:8px;background:var(--border-light);border-radius:4px;height:8px;overflow:hidden;">
                <div style="width:{b_rate}%;height:100%;background:{b_bar_color};border-radius:4px;transition:width 0.3s;"></div>
              </div>
            </div>
          </div>
        </div>"""

    if not cards:
        cards = """<div class="card"><div class="empty" style="padding:40px;">
          <div class="empty-icon">&#128202;</div>
          <h3>No A/B tests yet</h3>
          <p>Add a B subject line to your email sequences to start testing. Results appear once you've sent at least 5 emails per variant.</p>
        </div></div>"""

    # Build send time heatmap
    day_names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    time_grid = {}
    max_rate = 0
    for r in time_stats:
        key = (int(r["dow"]), int(r["hour"]))
        rate = (r["opens"] / r["total"] * 100) if r["total"] else 0
        time_grid[key] = {"total": r["total"], "opens": r["opens"], "rate": rate}
        if rate > max_rate:
            max_rate = rate

    heatmap_rows = ""
    for dow in range(7):
        cells = ""
        for hour in range(6, 22):  # 6am to 10pm
            data = time_grid.get((dow, hour), {"total": 0, "rate": 0})
            if data["total"] == 0:
                bg = "var(--border-light)"
                text_col = "var(--text-muted)"
            else:
                intensity = min(data["rate"] / max(max_rate, 1), 1.0)
                if intensity > 0.7:
                    bg = "var(--green)"
                    text_col = "#fff"
                elif intensity > 0.4:
                    bg = "var(--green-light)"
                    text_col = "var(--green-dark)"
                elif intensity > 0.1:
                    bg = "var(--blue-light)"
                    text_col = "var(--blue)"
                else:
                    bg = "var(--border-light)"
                    text_col = "var(--text-muted)"
            cells += f'<td style="width:36px;height:28px;text-align:center;font-size:10px;background:{bg};color:{text_col};border:1px solid var(--card);" title="{day_names[dow]} {hour}:00 — {data["total"]} sent, {data["rate"]:.0f}% open rate">{data["rate"]:.0f}%</td>'
        heatmap_rows += f"<tr><td style='font-size:11px;font-weight:600;padding-right:8px;color:var(--text-secondary);'>{day_names[dow]}</td>{cells}</tr>"

    hour_headers = "".join(f'<th style="font-size:10px;color:var(--text-muted);font-weight:500;padding:2px;">{h}</th>' for h in range(6, 22))

    heatmap = f"""
    <div class="card" style="margin-top:20px;">
      <div class="card-header"><h2>&#128345; Best Send Times</h2></div>
      <p style="font-size:12px;color:var(--text-secondary);margin-bottom:12px;">Open rates by day of week and hour. Green = high open rates.</p>
      <div style="overflow-x:auto;">
        <table style="border-collapse:separate;border-spacing:2px;margin:0;">
          <thead><tr><th></th>{hour_headers}</tr></thead>
          <tbody>{heatmap_rows}</tbody>
        </table>
      </div>
    </div>"""

    return _render(t("ab.title"), f"""
    <div class="breadcrumb"><a href="/dashboard">{t("dash.title")}</a> / {t("ab.title")}</div>
    <div class="page-header">
      <h1>&#128202; {t("ab.title")}</h1>
      <p class="subtitle">See which subject lines and send times perform best.</p>
    </div>
    {cards}
    {heatmap}
    """, active_page="ab_tests")


# ---------------------------------------------------------------------------
# Routes — Campaign Calendar
# ---------------------------------------------------------------------------

@app.route("/calendar")
def calendar_view():
    if not _logged_in():
        return redirect(url_for("login"))

    from outreach.db import get_calendar_events
    from collections import defaultdict
    import calendar as cal_module
    from datetime import date, timedelta

    events = get_calendar_events(session["client_id"])

    # Parse month from query string
    today = date.today()
    try:
        year = int(request.args.get("year", today.year))
        month = int(request.args.get("month", today.month))
    except ValueError:
        year, month = today.year, today.month

    # Group events by date
    events_by_date = defaultdict(list)
    for e in events:
        if e.get("date"):
            day_str = e["date"][:10]  # YYYY-MM-DD
            events_by_date[day_str].append(e)

    # Build calendar grid
    first_weekday, num_days = cal_module.monthrange(year, month)
    month_name = cal_module.month_name[month]

    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1

    # Calendar cells
    cells = ""
    # Empty cells before first day
    for _ in range(first_weekday):
        cells += '<td style="padding:4px;vertical-align:top;background:var(--border-light);"></td>'

    for day in range(1, num_days + 1):
        day_str = f"{year}-{month:02d}-{day:02d}"
        day_events = events_by_date.get(day_str, [])
        is_today = (year == today.year and month == today.month and day == today.day)

        bg = "var(--card)" if not is_today else "var(--primary-light)"
        border = "2px solid var(--primary)" if is_today else "1px solid var(--border-light)"

        event_dots = ""
        sent_count = sum(1 for e in day_events if e["event_type"] == "sent")
        sched_count = sum(1 for e in day_events if e["event_type"] == "scheduled")
        replied_count = sum(1 for e in day_events if e.get("email_status") == "replied")
        opened_count = sum(1 for e in day_events if e.get("email_status") in ("opened", "clicked"))

        if day_events:
            dots = []
            if sent_count:
                dots.append(f'<span style="font-size:10px;color:var(--blue);">{sent_count} sent</span>')
            if sched_count:
                dots.append(f'<span style="font-size:10px;color:var(--yellow);">{sched_count} sched</span>')
            if replied_count:
                dots.append(f'<span style="font-size:10px;color:var(--green);">{replied_count} reply</span>')
            if opened_count:
                dots.append(f'<span style="font-size:10px;color:var(--primary);">{opened_count} open</span>')
            event_dots = "<br>".join(dots)

            # Tooltip with details
            detail_lines = []
            for e in day_events[:5]:
                icon = "&#128232;" if e["event_type"] == "sent" else "&#128197;"
                name = _esc(e.get("contact_name") or e.get("contact_email", ""))
                subj = _esc((e.get("subject") or "")[:30])
                detail_lines.append(f"{icon} {name}: {subj}")
            tooltip = "&#10;".join(detail_lines)
            if len(day_events) > 5:
                tooltip += f"&#10;... and {len(day_events) - 5} more"
        else:
            tooltip = ""

        cells += f'''<td style="padding:6px;vertical-align:top;background:{bg};border:{border};min-height:70px;width:14.28%;" title="{tooltip}">
          <div style="font-weight:{'700' if is_today else '500'};font-size:13px;color:{'var(--primary)' if is_today else 'var(--text)'};">{day}</div>
          <div style="margin-top:2px;line-height:1.4;">{event_dots}</div>
        </td>'''

    # Fill remaining cells
    total_cells = first_weekday + num_days
    remaining = (7 - total_cells % 7) % 7
    for _ in range(remaining):
        cells += '<td style="padding:4px;vertical-align:top;background:var(--border-light);"></td>'

    # Arrange into rows
    all_cells = cells
    rows = ""
    idx = 0
    import re as _re
    cell_list = _re.findall(r'<td[^>]*>.*?</td>', all_cells, _re.DOTALL)
    for i in range(0, len(cell_list), 7):
        rows += "<tr>" + "".join(cell_list[i:i+7]) + "</tr>"

    # Stats sidebar
    total_sent = sum(1 for e in events if e["event_type"] == "sent")
    total_scheduled = sum(1 for e in events if e["event_type"] == "scheduled")
    total_replied = sum(1 for e in events if e.get("email_status") == "replied")

    # Upcoming events list (next 7 days)
    upcoming_html = ""
    for i in range(7):
        d = today + timedelta(days=i)
        ds = d.strftime("%Y-%m-%d")
        day_evts = events_by_date.get(ds, [])
        if day_evts:
            label = "Today" if i == 0 else ("Tomorrow" if i == 1 else d.strftime("%a %b %d"))
            items = ""
            for e in day_evts[:3]:
                icon = "&#9989;" if e.get("email_status") == "replied" else ("&#128232;" if e["event_type"] == "sent" else "&#128197;")
                name = _esc(e.get("contact_name") or e.get("contact_email", ""))
                camp = _esc(e.get("campaign_name", ""))
                items += f'<div style="padding:4px 0;font-size:12px;">{icon} <strong>{name}</strong> — {camp}</div>'
            if len(day_evts) > 3:
                items += f'<div style="font-size:11px;color:var(--text-muted);">+{len(day_evts) - 3} more</div>'
            upcoming_html += f'<div style="margin-bottom:10px;"><div style="font-weight:600;font-size:12px;color:var(--text-secondary);margin-bottom:2px;">{label}</div>{items}</div>'

    if not upcoming_html:
        upcoming_html = '<div style="font-size:13px;color:var(--text-muted);padding:12px 0;">No upcoming sends</div>'

    return _render(t("calendar.title"), f"""
    <div class="breadcrumb"><a href="/dashboard">{t("dash.title")}</a> / {t("calendar.title")}</div>
    <div class="page-header">
      <h1>&#128197; {t("calendar.title")}</h1>
      <p class="subtitle">View all sent and scheduled emails across campaigns.</p>
    </div>

    <div style="display:grid;grid-template-columns:1fr 280px;gap:20px;">
      <div class="card">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;">
          <a href="?year={prev_year}&month={prev_month}" class="btn btn-outline btn-sm">&larr;</a>
          <h2 style="font-size:18px;">{month_name} {year}</h2>
          <a href="?year={next_year}&month={next_month}" class="btn btn-outline btn-sm">&rarr;</a>
        </div>
        <table style="width:100%;border-collapse:separate;border-spacing:3px;table-layout:fixed;">
          <thead><tr>
            {''.join(f'<th style="font-size:11px;color:var(--text-muted);font-weight:600;padding:4px;text-align:center;">{d}</th>' for d in ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'])}
          </tr></thead>
          <tbody>{rows}</tbody>
        </table>
      </div>

      <div>
        <div class="card" style="margin-bottom:16px;">
          <div class="card-header"><h2>Overview</h2></div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:8px;">
            <div class="stat-card" style="padding:12px;text-align:center;">
              <div style="font-size:22px;font-weight:800;color:var(--blue);">{total_sent}</div>
              <div style="font-size:11px;color:var(--text-muted);">Emails Sent</div>
            </div>
            <div class="stat-card" style="padding:12px;text-align:center;">
              <div style="font-size:22px;font-weight:800;color:var(--yellow);">{total_scheduled}</div>
              <div style="font-size:11px;color:var(--text-muted);">Scheduled</div>
            </div>
            <div class="stat-card" style="padding:12px;text-align:center;">
              <div style="font-size:22px;font-weight:800;color:var(--green);">{total_replied}</div>
              <div style="font-size:11px;color:var(--text-muted);">Replies</div>
            </div>
          </div>
        </div>

        <div class="card">
          <div class="card-header"><h2>&#128197; Upcoming</h2></div>
          {upcoming_html}
        </div>
      </div>
    </div>
    """, active_page="calendar")


# ---------------------------------------------------------------------------
# Routes — Campaign CRUD
# ---------------------------------------------------------------------------

@app.route("/campaign/new", methods=["GET", "POST"])
def new_campaign():
    if not _logged_in():
        return redirect(url_for("login"))
    if request.method == "POST":
        # Check campaign limit for free plan
        from outreach.db import get_subscription
        from outreach.config import PLAN_LIMITS
        sub = get_subscription(session["client_id"])
        plan = sub.get("plan", "free")
        limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
        camp_limit = limits.get("campaigns", -1)
        if camp_limit != -1:
            existing = get_campaigns(session["client_id"])
            if len(existing) >= camp_limit:
                flash(("error", f"Free plan allows {camp_limit} campaigns. Upgrade to create more."))
                return redirect(url_for("new_campaign"))

        name = request.form.get("name", "").strip()
        btype = request.form.get("business_type", "").strip()
        audience = request.form.get("target_audience", "").strip()
        tone = request.form.get("tone", "professional")
        if not name or not btype or not audience:
            flash(("error", "Please fill in all required fields."))
            return redirect(url_for("new_campaign"))
        camp_id = create_campaign(session["client_id"], name, btype, audience, tone)

        try:
            steps = generate_sequence(btype, audience, tone)
            for s in steps:
                save_sequence(
                    camp_id, s["step"], s["subject_a"], s.get("subject_b", ""),
                    s["body"], s.get("body_b", ""), s.get("delay_days", 0),
                )
            flash(("success", f"Campaign created with {len(steps)} email steps! Add contacts to start sending."))
        except Exception as e:
            flash(("error", f"AI generation failed: {e}"))

        return redirect(f"/campaign/{camp_id}")

    return _render(t("campaign.new_title"), """
    <div class="breadcrumb"><a href="/dashboard">{{lbl_dashboard}}</a> / {{lbl_new_campaign}}</div>
    <div class="page-header">
      <h1>{{lbl_new_campaign}}</h1>
    </div>
    <div class="card">
      <form method="post" data-loading>
        <div class="form-group">
          <label>{{lbl_name}}</label>
          <input name="name" placeholder="e.g. Q2 Agency Outreach" required>
        </div>
        <div class="form-group">
          <label>Your Business Type</label>
          <input name="business_type" placeholder="e.g. AI-powered email outreach SaaS" required>
        </div>
        <div class="form-group">
          <label>{{lbl_audience}}</label>
          <textarea name="target_audience" placeholder="e.g. Marketing directors at mid-size e-commerce companies" required></textarea>
        </div>
        <div class="form-group">
          <label>{{lbl_tone}}</label>
          <select name="tone">
            <option value="professional">{{lbl_professional}}</option>
            <option value="casual">{{lbl_casual}}</option>
            <option value="direct">Direct &amp; Concise</option>
            <option value="humorous">Witty &amp; Humorous</option>
          </select>
        </div>
        <div class="btn-group mt-2">
          <button class="btn btn-primary" type="submit">
            <span class="btn-text">&#129302; {{lbl_create}}</span>
            <span class="spinner"></span>
          </button>
          <a href="/dashboard" class="btn btn-outline">{{lbl_cancel}}</a>
        </div>
      </form>
    </div>
    """, active_page="new_campaign",
        lbl_dashboard=t("dash.title"), lbl_new_campaign=t("campaign.new_title"),
        lbl_name=t("campaign.name"), lbl_audience=t("campaign.target_audience"),
        lbl_tone=t("campaign.tone"), lbl_professional=t("campaign.tone_professional"),
        lbl_casual=t("campaign.tone_casual"), lbl_create=t("campaign.create_btn"),
        lbl_cancel=t("common.cancel"))


@app.route("/campaign/<int:campaign_id>")
def view_campaign(campaign_id):
    if not _logged_in():
        return redirect(url_for("login"))
    camp = get_campaign(campaign_id)
    if not camp:
        return redirect(url_for("dashboard"))

    tab = request.args.get("tab", "overview")
    stats = get_campaign_stats(campaign_id)
    sequences = get_sequences(campaign_id)
    contacts = get_campaign_contacts(campaign_id)

    # Status badge
    sc = {"active": "badge-green", "draft": "badge-gray", "paused": "badge-yellow", "completed": "badge-blue"}.get(camp["status"], "badge-gray")
    status_badge = f'<span class="badge {sc}">{camp["status"]}</span>'

    # Progress
    pct = (stats["emails_sent"] / stats["total_contacts"] * 100) if stats["total_contacts"] else 0

    # Action buttons
    actions = ""
    if camp["status"] == "draft":
        actions = f"""
        <form method="post" action="/campaign/{campaign_id}/status" class="confirm-form"><input type="hidden" name="action" value="activate"><button class="btn btn-green btn-sm">&#9654; Activate</button></form>
        <form method="post" action="/campaign/{campaign_id}/duplicate" class="confirm-form"><button class="btn btn-outline btn-sm">&#128203; Duplicate</button></form>
        <form method="post" action="/campaign/{campaign_id}/delete" class="confirm-form" onsubmit="return confirm('Delete this campaign and all its data?')"><button class="btn btn-red btn-sm">Delete</button></form>"""
    elif camp["status"] == "active":
        actions = f"""
        <form method="post" action="/campaign/{campaign_id}/status" class="confirm-form"><input type="hidden" name="action" value="pause"><button class="btn btn-yellow btn-sm">&#9208; Pause</button></form>
        <form method="post" action="/campaign/{campaign_id}/duplicate" class="confirm-form"><button class="btn btn-outline btn-sm">&#128203; Duplicate</button></form>"""
    elif camp["status"] == "paused":
        actions = f"""
        <form method="post" action="/campaign/{campaign_id}/status" class="confirm-form"><input type="hidden" name="action" value="activate"><button class="btn btn-green btn-sm">&#9654; Resume</button></form>
        <form method="post" action="/campaign/{campaign_id}/duplicate" class="confirm-form"><button class="btn btn-outline btn-sm">&#128203; Duplicate</button></form>
        <form method="post" action="/campaign/{campaign_id}/delete" class="confirm-form" onsubmit="return confirm('Delete this campaign and all its data?')"><button class="btn btn-red btn-sm">Delete</button></form>"""

    # Sequence cards with previews
    seq_html = ""
    sample_contact = {"name": "John", "company": "Acme Inc", "role": "CEO"}
    for s in sequences:
        delay_str = "Initial email" if s["step"] == 1 else f"Follow-up &bull; {s['delay_days']}d delay"
        subj_b = f'<div style="margin-top:3px;"><span class="seq-subject-label">Subject B:</span> <span class="seq-subject">{_esc(s["subject_b"])}</span></div>' if s.get("subject_b") else ""

        # Preview with sample personalization
        preview_subj = personalize_email(s["subject_a"], sample_contact, SENDER_NAME)
        preview_body = personalize_email(s["body_a"], sample_contact, SENDER_NAME)

        seq_html += f"""
        <div class="seq-card">
          <div class="seq-actions">
            <button class="btn btn-ghost btn-sm" onclick="showPreview({s['id']})">&#128065; Preview</button>
            <a href="/campaign/{campaign_id}/sequence/{s['id']}/edit" class="btn btn-ghost btn-sm">&#9998; Edit</a>
          </div>
          <div class="seq-step">Step {s['step']} <span class="seq-delay">&bull; {delay_str}</span></div>
          <div><span class="seq-subject-label">Subject A:</span> <span class="seq-subject">{_esc(s['subject_a'])}</span></div>
          {subj_b}
          <div class="seq-body">{_esc(s['body_a'])}</div>
        </div>
        <div class="preview-modal" id="preview-{s['id']}">
          <div class="preview-content">
            <div class="preview-header">
              <h3>Email Preview (Step {s['step']})</h3>
              <button class="btn btn-ghost btn-sm" onclick="hidePreview({s['id']})">&#10005; Close</button>
            </div>
            <div class="preview-body">
              <p class="text-xs text-muted mb-4">Showing how this email looks with sample data (John, CEO at Acme Inc).</p>
              <div class="preview-field"><div class="pf-label">Subject</div><div class="pf-value" style="font-weight:600;">{_esc(preview_subj)}</div></div>
              <div class="preview-email">{_esc(preview_body)}</div>
            </div>
          </div>
        </div>"""

    if not seq_html:
        seq_html = '<div class="empty"><div class="empty-icon">&#128221;</div><h3>No email sequence</h3><p>Something went wrong with AI generation. Try creating a new campaign.</p></div>'

    # Contacts table
    contacts_html = ""
    for c in contacts:
        csc = {"sent": "badge-blue", "pending": "badge-gray", "replied": "badge-green", "bounced": "badge-red", "unsubscribed": "badge-yellow", "opened": "badge-purple"}.get(c["status"], "badge-gray")
        lang = c.get("language", "en") or "en"
        contacts_html += f"""<tr>
          <td><strong>{_esc(c['name'])}</strong></td>
          <td style="font-family:monospace;font-size:12px;">{_esc(c['email'])}</td>
          <td>{_esc(c['company'])}</td>
          <td>{_esc(c.get('role', ''))}</td>
          <td><span class="badge badge-gray" style="font-size:10px;text-transform:uppercase;">{_esc(lang)}</span></td>
          <td><span class="badge {csc}">{c['status']}</span></td>
          <td style="text-align:right;">
            <form method="post" action="/campaign/{campaign_id}/contact/{c['id']}/delete" class="confirm-form" onsubmit="return confirm('Remove this contact?')">
              <button class="btn btn-ghost btn-sm" style="color:var(--red);" title="Remove">&#10005;</button>
            </form>
          </td>
        </tr>"""
    if not contacts_html:
        contacts_html = f"""<tr><td colspan="7">
          <div class="empty" style="padding:28px;">
            <div class="empty-icon">&#128101;</div>
            <h3>No contacts yet</h3>
            <p>Upload a CSV or paste contacts to get started.</p>
          </div>
        </td></tr>"""

    # Count by status
    pending_count = sum(1 for c in contacts if c["status"] == "pending")
    sent_count = sum(1 for c in contacts if c["status"] == "sent")
    replied_count = sum(1 for c in contacts if c["status"] == "replied")

    # Activity feed
    sent_emails = get_sent_emails(campaign_id)
    activity_html = ""
    for e in sent_emails[:20]:
        dot_class = "replied" if e.get("status") == "replied" else ("opened" if e.get("status") == "opened" else "sent")
        action = "replied to" if e.get("status") == "replied" else ("opened" if e.get("status") == "opened" else "sent to")
        activity_html += f"""
        <div class="activity-item">
          <div class="activity-dot {dot_class}"></div>
          <div>
            <div class="activity-text">Email {action} <strong>{_esc(e['contact_name'])}</strong> ({_esc(e['contact_email'])})</div>
            <div class="activity-time">Step {e['step']} &bull; {e.get('sent_at', '')}</div>
          </div>
        </div>"""
    if not activity_html:
        activity_html = '<div class="empty" style="padding:24px;"><div class="empty-icon">&#128172;</div><p>No activity yet. Activate the campaign to start sending.</p></div>'

    return _render(_esc(camp["name"]), """
    <div class="breadcrumb"><a href="/dashboard">Dashboard</a> / {{camp_name}}</div>
    <div class="page-header" style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:12px;">
      <div>
        <div style="display:flex;align-items:center;gap:10px;">
          <h1>{{camp_name}}</h1>
          {{status_badge}}
        </div>
      </div>
      <div class="btn-group">{{actions}}</div>
    </div>

    <div class="stats-grid" data-campaign-id="{{camp_id}}">
      <div class="stat-card stat-purple">
        <div class="num" data-stat="camp_total_contacts">{{stats.total_contacts}}</div><div class="label">Contacts</div>
        <div class="progress-wrap"><div class="progress-bar bar-purple" style="width:{{pct_fmt}}%"></div></div>
      </div>
      <div class="stat-card stat-blue"><div class="num" data-stat="camp_emails_sent">{{stats.emails_sent}}</div><div class="label">Sent</div></div>
      <div class="stat-card stat-green"><div class="num" data-stat="camp_open_rate_fmt">{{open_rate_fmt}}</div><div class="label">Open Rate</div></div>
      <div class="stat-card stat-purple"><div class="num" data-stat="camp_reply_rate_fmt">{{reply_rate_fmt}}</div><div class="label">Reply Rate</div></div>
    </div>

    <div class="tabs">
      <a class="tab {% if tab == 'overview' %}active{% endif %}" href="?tab=overview">Sequence <span class="tab-count">{{num_sequences}}</span></a>
      <a class="tab {% if tab == 'contacts' %}active{% endif %}" href="?tab=contacts">Contacts <span class="tab-count">{{stats.total_contacts}}</span></a>
      <a class="tab {% if tab == 'activity' %}active{% endif %}" href="?tab=activity">Activity <span class="tab-count">{{num_sent}}</span></a>
    </div>

    {% if tab == 'overview' %}
      {{seq_html}}

    {% elif tab == 'contacts' %}
      <div class="card">
        <div class="card-header"><h2>Add Contacts</h2></div>
        <form method="post" action="/campaign/{{camp_id}}/contacts" enctype="multipart/form-data">
          <div class="form-group">
            <label>Upload CSV file</label>
            <input type="file" name="csv_file" accept=".csv">
            <p class="form-hint">CSV columns: name, email, company, role, language (header row auto-skipped). Language is optional (default: en).</p>
          </div>
          <hr class="form-divider">
          <div class="form-group">
            <label>Or paste manually</label>
            <textarea name="contacts_csv" placeholder="John Doe,john@company.com,Acme Inc,CEO,en&#10;Maria Garcia,maria@empresa.com,Empresa SA,Directora,es" style="min-height:70px;font-family:monospace;font-size:12px;"></textarea>
          </div>
          <button class="btn btn-green" type="submit">Add Contacts</button>
        </form>
      </div>

      <div class="card">
        <div class="card-header">
          <h2>Contact List</h2>
          <div class="text-xs text-muted">
            <span class="badge badge-gray">{{pending}} pending</span>
            <span class="badge badge-blue">{{sent_c}} sent</span>
            <span class="badge badge-green">{{replied}} replied</span>
          </div>
        </div>
        <table>
          <thead><tr><th>Name</th><th>Email</th><th>Company</th><th>Role</th><th>Lang</th><th>Status</th><th></th></tr></thead>
          <tbody>{{contacts_html}}</tbody>
        </table>
      </div>

    {% elif tab == 'activity' %}
      <div class="card">
        <div class="card-header"><h2>Recent Activity</h2></div>
        {{activity_html}}
      </div>
    {% endif %}
    """, camp_name=_esc(camp["name"]), status_badge=Markup(status_badge), actions=Markup(actions),
        stats=stats, seq_html=Markup(seq_html), contacts_html=Markup(contacts_html),
        activity_html=Markup(activity_html), camp_id=campaign_id, tab=tab,
        pct_fmt=f"{min(pct,100):.0f}", num_sequences=len(sequences), num_sent=len(sent_emails),
        pending=pending_count, sent_c=sent_count, replied=replied_count,
        open_rate_fmt=f"{stats['open_rate']:.0%}", reply_rate_fmt=f"{stats['reply_rate']:.0%}")

@app.route("/campaign/<int:cid>/sequence/<int:sid>/edit", methods=["GET", "POST"])
def edit_sequence(cid, sid):
    if not _logged_in():
        return redirect(url_for("login"))
    sequences = get_sequences(cid)
    seq = None
    for s in sequences:
        if s["id"] == sid:
            seq = s
            break
    if not seq:
        return redirect(f"/campaign/{cid}")

    if request.method == "POST":
        subject_a = request.form.get("subject_a", "").strip()
        subject_b = request.form.get("subject_b", "").strip()
        body_a = request.form.get("body_a", "").strip()
        delay = int(request.form.get("delay_days", 0))
        if subject_a and body_a:
            update_sequence(sid, subject_a, subject_b, body_a, delay)
            flash(("success", f"Step {seq['step']} updated."))
        return redirect(f"/campaign/{cid}")

    return _render("Edit Sequence", """
    <div class="breadcrumb"><a href="/dashboard">Dashboard</a> / <a href="/campaign/{{cid}}">Campaign</a> / Edit Step {{seq.step}}</div>
    <div class="page-header">
      <h1>Edit Step {{seq.step}}</h1>
    </div>
    <div class="card">
      <form method="post">
        <div class="form-group"><label>Subject Line A</label><input name="subject_a" value="{{seq.subject_a}}" required></div>
        <div class="form-group"><label>Subject Line B (A/B test)</label><input name="subject_b" value="{{seq.subject_b or ''}}"></div>
        <div class="form-group"><label>Email Body</label><textarea name="body_a" style="min-height:180px;font-family:monospace;font-size:13px;" required>{{seq.body_a}}</textarea></div>
        <div class="form-group" style="max-width:200px;"><label>Delay (days)</label><input name="delay_days" type="number" min="0" value="{{seq.delay_days}}"></div>
        <p class="form-hint">Use placeholders: <code>{{name}}</code> <code>{{company}}</code> <code>{{role}}</code> <code>{{sender_name}}</code></p>
        <div class="btn-group mt-2">
          <button class="btn btn-primary" type="submit">Save Changes</button>
          <a href="/campaign/{{cid}}" class="btn btn-outline">Cancel</a>
        </div>
      </form>
    </div>
    """, seq=seq, cid=cid)

@app.route("/campaign/<int:campaign_id>/contacts", methods=["POST"])
def upload_contacts(campaign_id):
    if not _logged_in():
        return redirect(url_for("login"))
    raw = request.form.get("contacts_csv", "")
    csv_file = request.files.get("csv_file")
    if csv_file and csv_file.filename:
        raw = csv_file.read().decode("utf-8-sig")

    contacts = []
    for line in raw.strip().splitlines():
        if line.lower().startswith("name,") or line.lower().startswith("name\t"):
            continue
        parts = [p.strip().strip('"') for p in line.split(",")]
        if len(parts) >= 2 and "@" in parts[1]:
            contacts.append({
                "name": parts[0],
                "email": parts[1],
                "company": parts[2] if len(parts) > 2 else "",
                "role": parts[3] if len(parts) > 3 else "",
                "language": parts[4] if len(parts) > 4 else "en",
            })
    if contacts:
        count = add_contacts(campaign_id, contacts)
        flash(("success", f"Added {count} contact{'s' if count != 1 else ''}."))
    else:
        flash(("error", "No valid contacts found. Format: name, email, company, role, language"))
    return redirect(f"/campaign/{campaign_id}?tab=contacts")


@app.route("/campaign/<int:campaign_id>/status", methods=["POST"])
def campaign_status(campaign_id):
    if not _logged_in():
        return redirect(url_for("login"))
    action = request.form.get("action", "")
    msgs = {
        "activate": ("active", "Campaign activated! Emails will start sending."),
        "pause": ("paused", "Campaign paused."),
        "complete": ("completed", "Campaign completed."),
    }
    if action in msgs:
        status, msg = msgs[action]
        update_campaign_status(campaign_id, status)
        flash(("success", msg))
    return redirect(f"/campaign/{campaign_id}")


@app.route("/campaign/<int:campaign_id>/activate", methods=["POST"])
def activate_campaign(campaign_id):
    if not _logged_in():
        return redirect(url_for("login"))
    update_campaign_status(campaign_id, "active")
    flash(("success", "Campaign activated!"))
    return redirect(f"/campaign/{campaign_id}")


@app.route("/campaign/<int:campaign_id>/delete", methods=["POST"])
def campaign_delete(campaign_id):
    if not _logged_in():
        return redirect(url_for("login"))
    delete_campaign(campaign_id)
    flash(("success", "Campaign deleted."))
    return redirect(url_for("dashboard"))


@app.route("/campaign/<int:campaign_id>/duplicate", methods=["POST"])
def campaign_dup(campaign_id):
    if not _logged_in():
        return redirect(url_for("login"))
    new_id = duplicate_campaign(campaign_id, session["client_id"])
    if new_id:
        flash(("success", "Campaign duplicated as draft."))
        return redirect(f"/campaign/{new_id}")
    flash(("error", "Could not duplicate campaign."))
    return redirect(url_for("dashboard"))


@app.route("/campaign/<int:cid>/contact/<int:contact_id>/delete", methods=["POST"])
def contact_delete(cid, contact_id):
    if not _logged_in():
        return redirect(url_for("login"))
    delete_contact(contact_id)
    flash(("success", "Contact removed."))
    return redirect(f"/campaign/{cid}?tab=contacts")

@app.route("/track/open/<int:sent_email_id>")
def track_open(sent_email_id):
    from outreach.tracker import TRACKING_PIXEL, handle_open
    handle_open(sent_email_id)
    return Response(TRACKING_PIXEL, mimetype="image/gif")


@app.route("/unsubscribe/<int:contact_id>")
def unsubscribe(contact_id):
    from outreach.db import get_db
    with get_db() as db:
        db.execute("UPDATE contacts SET status = 'unsubscribed' WHERE id = ?", (contact_id,))
    return render_template_string(LAYOUT, title="Unsubscribed", logged_in=False, messages=[], active_page="", client_name="", nav=t_dict("nav"), lang=session.get("lang", "en"), content=Markup("""
    <div style="text-align:center;padding:80px 24px;">
      <div style="font-size:48px;margin-bottom:16px;opacity:0.4;">&#9993;</div>
      <h1 style="font-size:22px;margin-bottom:8px;">You've been unsubscribed</h1>
      <p style="color:var(--text-secondary);font-size:14px;">You won't receive any more emails from this campaign.</p>
    </div>
    """))

@app.route("/api/stats")
def api_global_stats():
    """Return global stats as JSON for live dashboard refresh."""
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401
    gstats = get_global_stats(session["client_id"])
    gstats["open_rate_fmt"] = f"{gstats['open_rate']:.0%}"
    gstats["reply_rate_fmt"] = f"{gstats['reply_rate']:.0%}"
    return jsonify(gstats)


@app.route("/api/campaign/<int:campaign_id>/stats")
def api_campaign_stats(campaign_id):
    """Return campaign stats as JSON for live refresh."""
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401
    stats = get_campaign_stats(campaign_id)
    stats["open_rate_fmt"] = f"{stats['open_rate']:.0%}"
    stats["reply_rate_fmt"] = f"{stats['reply_rate']:.0%}"
    return jsonify(stats)


@app.route("/api/check-replies", methods=["POST"])
def api_check_replies():
    """Trigger an immediate reply check and return updated stats."""
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401
    from outreach.reply_checker import check_replies
    try:
        n = check_replies()
        gstats = get_global_stats(session["client_id"])
        gstats["open_rate_fmt"] = f"{gstats['open_rate']:.0%}"
        gstats["reply_rate_fmt"] = f"{gstats['reply_rate']:.0%}"
        return jsonify({"new_replies": n, "stats": gstats})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/reply-draft/<int:sent_email_id>", methods=["POST"])
def api_reply_draft(sent_email_id):
    """Generate an AI-powered reply draft for a replied email."""
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401

    ctx = get_reply_context(sent_email_id)
    if not ctx:
        return jsonify({"error": "Email not found or not replied"}), 404

    if not ctx.get("reply_body"):
        return jsonify({"error": "No reply body to respond to"}), 400

    try:
        draft = generate_reply_draft(
            original_subject=ctx["subject"],
            original_body=ctx["body"],
            reply_body=ctx["reply_body"],
            reply_sentiment=ctx.get("reply_sentiment", "neutral"),
            contact_name=ctx["contact_name"],
            contact_company=ctx["company"],
            sender_name=SENDER_NAME,
            business_context=ctx.get("business_type", ""),
        )
        return jsonify({"draft": draft})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Routes — Smart Send Times
# ---------------------------------------------------------------------------

@app.route("/smart-times")
def smart_times():
    if not _logged_in():
        return redirect(url_for("login"))

    from outreach.db import get_send_time_stats
    time_stats = get_send_time_stats(session["client_id"])
    insights = get_optimal_send_hour(time_stats)

    # Build the best hours visual
    hours_html = ""
    if insights["best_hours"]:
        for h, rate in insights["best_hours"]:
            bar_w = min(rate / max(insights["best_hours"][0][1], 1) * 100, 100)
            color = "var(--green)" if rate >= 50 else ("var(--blue)" if rate >= 25 else "var(--yellow)")
            hours_html += f"""
            <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px;">
              <div style="width:60px;font-size:14px;font-weight:700;text-align:right;color:var(--text);">{h}:00</div>
              <div style="flex:1;background:var(--border-light);border-radius:4px;height:28px;overflow:hidden;position:relative;">
                <div style="width:{bar_w}%;height:100%;background:{color};border-radius:4px;transition:width 0.5s;display:flex;align-items:center;padding-left:10px;">
                  <span style="font-size:12px;font-weight:700;color:#fff;">{rate}%</span>
                </div>
              </div>
            </div>"""
    else:
        hours_html = '<div class="empty" style="padding:20px;"><p>Not enough data yet. Send at least 3 emails per time slot.</p></div>'

    # Best days visual
    days_html = ""
    if insights["best_days"]:
        for d, rate in insights["best_days"]:
            bar_w = min(rate / max(insights["best_days"][0][1], 1) * 100, 100)
            color = "var(--green)" if rate >= 50 else ("var(--blue)" if rate >= 25 else "var(--yellow)")
            days_html += f"""
            <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px;">
              <div style="width:60px;font-size:14px;font-weight:700;text-align:right;color:var(--text);">{d}</div>
              <div style="flex:1;background:var(--border-light);border-radius:4px;height:28px;overflow:hidden;position:relative;">
                <div style="width:{bar_w}%;height:100%;background:{color};border-radius:4px;transition:width 0.5s;display:flex;align-items:center;padding-left:10px;">
                  <span style="font-size:12px;font-weight:700;color:#fff;">{rate}%</span>
                </div>
              </div>
            </div>"""

    # Heatmap (same as A/B tests page but more prominent)
    day_names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    time_grid = {}
    max_rate = 0
    for r in time_stats:
        key = (int(r["dow"]), int(r["hour"]))
        rate = (r["opens"] / r["total"] * 100) if r["total"] else 0
        time_grid[key] = {"total": r["total"], "opens": r["opens"], "rate": rate}
        if rate > max_rate:
            max_rate = rate

    heatmap_rows = ""
    for dow in range(7):
        cells = ""
        for hour in range(6, 22):
            data = time_grid.get((dow, hour), {"total": 0, "rate": 0})
            if data["total"] == 0:
                bg = "var(--border-light)"
                text_col = "var(--text-muted)"
            else:
                intensity = min(data["rate"] / max(max_rate, 1), 1.0)
                if intensity > 0.7:
                    bg = "var(--green)"
                    text_col = "#fff"
                elif intensity > 0.4:
                    bg = "var(--green-light)"
                    text_col = "var(--green-dark)"
                elif intensity > 0.1:
                    bg = "var(--blue-light)"
                    text_col = "var(--blue)"
                else:
                    bg = "var(--border-light)"
                    text_col = "var(--text-muted)"
            cells += f'<td style="width:36px;height:32px;text-align:center;font-size:10px;background:{bg};color:{text_col};border:2px solid var(--card);border-radius:4px;" title="{day_names[dow]} {hour}:00 — {data["total"]} sent, {data["rate"]:.0f}% opens">{data["rate"]:.0f}%</td>'
        heatmap_rows += f"<tr><td style='font-size:12px;font-weight:600;padding-right:10px;color:var(--text-secondary);'>{day_names[dow]}</td>{cells}</tr>"

    hour_headers = "".join(f'<th style="font-size:10px;color:var(--text-muted);font-weight:500;padding:2px 0;">{h}</th>' for h in range(6, 22))

    return _render(t("smart.title"), f"""
    <div class="breadcrumb"><a href="/dashboard">{t("dash.title")}</a> / {t("smart.title")}</div>
    <div class="page-header">
      <h1>&#9201; {t("smart.title")}</h1>
      <p class="subtitle">AI-analyzed optimal sending windows based on your open rate data.</p>
    </div>

    <div class="card" style="background:linear-gradient(135deg, var(--primary-light) 0%, #F0F0FF 100%);border:1px solid var(--primary);margin-bottom:20px;">
      <div style="display:flex;align-items:center;gap:12px;">
        <div style="font-size:32px;">&#129302;</div>
        <div>
          <div style="font-weight:700;font-size:15px;color:var(--primary-dark);">AI Recommendation</div>
          <div style="font-size:14px;color:var(--text);margin-top:2px;">{_esc(insights['recommendation'])}</div>
        </div>
      </div>
    </div>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:20px;">
      <div class="card">
        <div class="card-header"><h2>&#128200; Best Hours to Send</h2></div>
        <p style="font-size:12px;color:var(--text-secondary);margin-bottom:12px;">Ranked by open rate. Only hours with 3+ emails shown.</p>
        {hours_html}
      </div>
      <div class="card">
        <div class="card-header"><h2>&#128197; Best Days to Send</h2></div>
        <p style="font-size:12px;color:var(--text-secondary);margin-bottom:12px;">Ranked by open rate across all campaigns.</p>
        {days_html}
      </div>
    </div>

    <div class="card">
      <div class="card-header"><h2>&#128345; Open Rate Heatmap</h2></div>
      <p style="font-size:12px;color:var(--text-secondary);margin-bottom:12px;">Green = high open rates. Gray = no data or low volume.</p>
      <div style="overflow-x:auto;">
        <table style="border-collapse:separate;border-spacing:2px;margin:0;">
          <thead><tr><th></th>{hour_headers}</tr></thead>
          <tbody>{heatmap_rows}</tbody>
        </table>
      </div>
    </div>
    """, active_page="smart_times")


# ---------------------------------------------------------------------------
# Routes — Analytics Export
# ---------------------------------------------------------------------------

@app.route("/export")
def export_page():
    if not _logged_in():
        return redirect(url_for("login"))

    campaigns = get_campaigns(session["client_id"])
    options = '<option value="">All Campaigns</option>'
    for c in campaigns:
        options += f'<option value="{c["id"]}">{_esc(c["name"])}</option>'

    return _render(t("export.title"), f"""
    <div class="breadcrumb"><a href="/dashboard">{t("dash.title")}</a> / {t("export.title")}</div>
    <div class="page-header">
      <h1>&#128202; {t("export.title")}</h1>
      <p class="subtitle">Download campaign data as CSV for reporting and analysis.</p>
    </div>

    <div class="card">
      <div class="card-header"><h2>Download Report</h2></div>
      <form method="get" action="/export/csv" style="display:flex;gap:12px;align-items:end;flex-wrap:wrap;">
        <div class="form-group" style="flex:1;min-width:200px;">
          <label>Campaign</label>
          <select name="campaign_id" style="margin-bottom:0;">{options}</select>
        </div>
        <button class="btn btn-primary" type="submit" style="margin-bottom:14px;">&#11015; Download CSV</button>
      </form>
      <p class="form-hint" style="margin-top:8px;">Includes: contact info, email status, open/reply times, reply sentiment, and reply content.</p>
    </div>

    <div class="card" style="margin-top:16px;">
      <div class="card-header"><h2>Quick Stats Summary</h2></div>
      <div id="summary-stats">Loading...</div>
    </div>

    <script>
      fetch('/api/stats')
        .then(r => r.json())
        .then(d => {{
          if (d.error) return;
          document.getElementById('summary-stats').innerHTML = `
            <div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(150px, 1fr));gap:12px;">
              <div style="text-align:center;padding:16px;">
                <div style="font-size:28px;font-weight:800;color:var(--blue);">${{d.total_sent}}</div>
                <div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;font-weight:600;">Emails Sent</div>
              </div>
              <div style="text-align:center;padding:16px;">
                <div style="font-size:28px;font-weight:800;color:var(--green);">${{d.total_opened}}</div>
                <div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;font-weight:600;">Opens (${{d.open_rate_fmt}})</div>
              </div>
              <div style="text-align:center;padding:16px;">
                <div style="font-size:28px;font-weight:800;color:var(--primary);">${{d.total_replied}}</div>
                <div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;font-weight:600;">Replies (${{d.reply_rate_fmt}})</div>
              </div>
              <div style="text-align:center;padding:16px;">
                <div style="font-size:28px;font-weight:800;color:var(--yellow);">${{d.total_contacts}}</div>
                <div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;font-weight:600;">Total Contacts</div>
              </div>
            </div>`;
        }});
    </script>
    """, active_page="export")


@app.route("/export/csv")
def export_csv():
    if not _logged_in():
        return redirect(url_for("login"))

    import csv
    import io

    campaign_id = request.args.get("campaign_id", type=int)
    data = get_export_data(session["client_id"], campaign_id)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Campaign", "Contact Name", "Contact Email", "Company", "Role",
        "Contact Status", "Step", "Subject", "Variant", "Email Status",
        "Sent At", "Opened At", "Replied At", "Reply Sentiment", "Reply Body",
    ])
    for row in data:
        writer.writerow([
            row.get("campaign_name", ""),
            row.get("contact_name", ""),
            row.get("contact_email", ""),
            row.get("company", ""),
            row.get("role", ""),
            row.get("contact_status", ""),
            row.get("step", ""),
            row.get("subject", ""),
            row.get("variant", ""),
            row.get("email_status", ""),
            row.get("sent_at", ""),
            row.get("opened_at", ""),
            row.get("replied_at", ""),
            row.get("reply_sentiment", ""),
            (row.get("reply_body", "") or "")[:500],
        ])

    csv_content = output.getvalue()
    filename = "outreach-export"
    if campaign_id:
        camp = get_campaign(campaign_id)
        if camp:
            safe_name = "".join(c for c in camp["name"] if c.isalnum() or c in " -_").strip()
            filename = f"outreach-{safe_name}"
    filename += ".csv"

    return Response(
        csv_content,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )

@app.route("/mail-hub")
def mail_hub():
    if not _logged_in():
        return redirect(url_for("login"))

    from outreach.db import get_mail_inbox, get_mail_stats, search_mail_inbox, get_scheduled_emails, get_email_accounts, get_subscription, get_top_senders
    filter_by = request.args.get("filter", "all")
    category = request.args.get("category")
    search_q = request.args.get("q", "").strip()
    account_filter = request.args.get("account")
    account_id = int(account_filter) if account_filter and account_filter.isdigit() else None
    sender_filter = request.args.get("sender", "").strip().lower()

    stats = get_mail_stats(session["client_id"])
    accounts = get_email_accounts(session["client_id"])
    top_senders = get_top_senders(session["client_id"])
    sub = get_subscription(session["client_id"])
    user_plan = sub.get("plan", "free") if sub else "free"
    is_paid = user_plan in ("growth", "pro", "unlimited")
    # Build account lookup for labels/colors
    acct_map = {a["id"]: a for a in accounts}

    if search_q:
        emails = search_mail_inbox(session["client_id"], search_q)
    else:
        emails = get_mail_inbox(session["client_id"], filter_by=filter_by, category=category, account_id=account_id, sender=sender_filter or None)

    scheduled = get_scheduled_emails(session["client_id"], status="pending")
    sched_count = len(scheduled)

    # Priority colors/icons
    pri_config = {
        "urgent": ("&#128308;", "var(--red)", "badge-red"),
        "important": ("&#128992;", "var(--yellow)", "badge-yellow"),
        "normal": ("&#128309;", "var(--blue)", "badge-blue"),
        "low": ("&#11035;", "var(--text-muted)", "badge-gray"),
    }
    cat_config = {
        "action_required": ("&#9889;", "Action Required"),
        "meeting": ("&#128197;", "Meeting"),
        "fyi": ("&#128196;", "FYI"),
        "newsletter": ("&#128240;", "Newsletter"),
        "personal": ("&#128100;", "Personal"),
        "spam": ("&#128681;", "Spam"),
        "uncategorized": ("&#128233;", "Uncategorized"),
    }

    # Build email rows
    email_rows = ""
    acct_colors = ["#6366F1", "#EC4899", "#14B8A6", "#F59E0B", "#8B5CF6", "#EF4444", "#06B6D4", "#84CC16"]
    for e in emails:
        pi, pc, pb = pri_config.get(e["priority"], ("&#128309;", "var(--blue)", "badge-blue"))
        ci, cl = cat_config.get(e["category"], ("&#128233;", e["category"]))
        star_cls = "color:var(--yellow);" if e["is_starred"] else "color:var(--text-muted);opacity:0.3;"
        read_weight = "400" if e["is_read"] else "600"
        read_bg = "" if e["is_read"] else "background:var(--primary-light);"
        # Account badge
        acct_badge = ""
        if e.get("account_id") and e["account_id"] in acct_map:
            a = acct_map[e["account_id"]]
            acct_idx = list(acct_map.keys()).index(e["account_id"]) % len(acct_colors)
            acct_badge = f' <span class="badge" style="font-size:10px;background:{acct_colors[acct_idx]}20;color:{acct_colors[acct_idx]};border:1px solid {acct_colors[acct_idx]}40;">{_esc(a["label"] or a["email"].split("@")[0])}</span>'
        from_display = _esc(e["from_name"]) if e["from_name"] else _esc(e["from_email"])
        time_str = e["received_at"][:16] if e["received_at"] else ""
        summary = f'<div style="font-size:13px;color:var(--primary);margin-top:4px;font-style:italic;">{_esc(e["ai_summary"])}</div>' if e.get("ai_summary") else ""

        # Snooze badge: active vs resurfaced with reminder
        snooze_badge = ""
        if e.get("snooze_until"):
            from datetime import datetime
            try:
                snooze_dt = datetime.strptime(e["snooze_until"][:19], "%Y-%m-%d %H:%M:%S")
                if snooze_dt > datetime.now():
                    snooze_badge = f' <span class="badge badge-yellow" style="font-size:11px;">&#128340; Snoozed until {e["snooze_until"][:16]}</span>'
                else:
                    snooze_badge = ' <span class="badge badge-purple" style="font-size:11px;">&#128276; Resurfaced</span>'
            except Exception:
                snooze_badge = ' <span class="badge badge-yellow" style="font-size:11px;">&#128340; Snoozed</span>'
        snooze_note_badge = ""
        if e.get("snooze_note"):
            snooze_note_badge = f' <span class="badge badge-blue" style="font-size:11px;" title="{_esc(e["snooze_note"])}">&#128221; {_esc(e["snooze_note"][:30])}</span>'

        email_rows += f"""
        <tr style="{read_bg}cursor:pointer;" data-mail-id="{e['id']}" onclick="if(event.ctrlKey){{handleShiftClick(this,event);return;}} if(!event.target.closest('button,span[onclick],input[type=checkbox],label'))window.location='/mail-hub/{e['id']}'">
          <td style="width:36px;text-align:center;padding:12px 6px;">
            <input type="checkbox" class="mail-checkbox" value="{e['id']}" onclick="event.stopPropagation();handleCheckboxClick(this,event);" style="width:16px;height:16px;cursor:pointer;accent-color:var(--primary);">
          </td>
          <td style="width:26px;text-align:center;padding:12px 6px;">
            <span style="cursor:pointer;font-size:20px;{star_cls}" onclick="toggleStar({e['id']}, this)">&#9733;</span>
          </td>
          <td style="width:26px;text-align:center;font-size:14px;padding:12px 4px;" title="{e['priority']}">{pi}</td>
          <td style="width:180px;padding:12px 8px;">
            <div style="font-weight:{read_weight};font-size:15px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:180px;">{from_display}</div>
            <div style="font-size:12px;color:var(--text-muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:180px;">{_esc(e['from_email'])}</div>
          </td>
          <td style="padding:12px 8px;">
            <div style="font-weight:{read_weight};font-size:15px;">{_esc(e['subject'] or '(no subject)')}</div>
            <div style="font-size:13px;color:var(--text-secondary);max-height:20px;overflow:hidden;margin-top:2px;">{_esc(e['body_preview'][:120])}</div>
            <div style="display:flex;align-items:center;gap:6px;margin-top:5px;flex-wrap:wrap;">
              <span class="badge {pb}" style="font-size:11px;">{e['priority']}</span>
              <span class="badge badge-gray" style="font-size:11px;">{ci} {cl}</span>
              {acct_badge}{snooze_badge}{snooze_note_badge}
            </div>
            {summary}
          </td>
          <td style="width:140px;padding:12px 8px;text-align:right;vertical-align:top;">
            <div style="font-size:12px;color:var(--text-muted);white-space:nowrap;margin-bottom:6px;">{time_str}</div>
            <div style="display:flex;gap:4px;justify-content:flex-end;">
              <button class="btn btn-ghost btn-sm" onclick="markRead({e['id']}, this)" title="{'Mark unread' if e['is_read'] else 'Mark read'}" style="font-size:14px;padding:4px 8px;">{'&#128065;' if not e['is_read'] else '&#9898;'}</button>
              <button class="btn btn-ghost btn-sm" onclick="openSnoozeModal({e['id']})" title="Snooze / Remind" style="font-size:14px;padding:4px 8px;">&#128340;</button>
              <button class="btn btn-ghost btn-sm" onclick="archiveEmail({e['id']}, this)" title="Archive" style="font-size:14px;padding:4px 8px;">&#128230;</button>
            </div>
          </td>
        </tr>"""

    if not email_rows:
        email_rows = f"""<tr><td colspan="6">
          <div class="empty" style="padding:40px;">
            <div class="empty-icon">&#128233;</div>
            <h3>{'No emails match "' + _esc(search_q) + '"' if search_q else ('No emails match this filter' if filter_by != 'all' or category else 'Inbox empty')}</h3>
            <p>{'Try different search terms.' if search_q else ('Try a different filter or sync your inbox.' if filter_by != 'all' or category else 'Click "Sync Inbox" to fetch your latest emails.')}</p>
          </div>
        </td></tr>"""

    # Category sidebar items
    cat_sidebar = ""
    for cat_key, (cat_icon, cat_label) in cat_config.items():
        cnt = stats["categories"].get(cat_key, 0)
        if cnt == 0 and cat_key == "uncategorized":
            continue
        active = "background:var(--primary-light);color:var(--primary);font-weight:600;" if category == cat_key else ""
        cat_sidebar += f'<a href="?category={cat_key}" style="display:flex;align-items:center;gap:10px;padding:10px 14px;border-radius:8px;text-decoration:none;font-size:14px;color:var(--text-secondary);{active}"><span style="font-size:16px;">{cat_icon}</span> {cat_label} <span style="margin-left:auto;font-size:12px;color:var(--text-muted);">{cnt}</span></a>'

    # Sender sidebar items
    sender_sidebar = ""
    for s in top_senders:
        s_email = s["email"].lower()
        s_name = _esc(s["name"])
        s_active = "background:var(--primary-light);color:var(--primary);font-weight:600;" if sender_filter == s_email else ""
        sender_sidebar += f'<a href="?sender={_esc(s_email)}" style="display:flex;align-items:center;gap:10px;padding:8px 14px;border-radius:8px;text-decoration:none;font-size:13px;color:var(--text-secondary);{s_active}" title="{_esc(s_email)}"><span style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:150px;">{s_name}</span> <span style="margin-left:auto;font-size:12px;color:var(--text-muted);">{s["count"]}</span></a>'

    # Build scheduled emails section
    sched_html = ""
    for s in scheduled[:5]:
        sched_html += f'<div style="padding:8px 0;border-bottom:1px solid var(--border-light);font-size:13px;"><div style="font-weight:600;">{_esc(s["to_email"])}</div><div style="color:var(--text-muted);">{_esc(s["subject"][:40])}</div><div style="color:var(--primary);font-size:12px;">&#128340; {s["scheduled_at"][:16]}</div></div>'

    return _render(t("mail.title"), f"""
    <div class="breadcrumb"><a href="/dashboard">Dashboard</a> / {t("mail.title")}</div>
    <div class="page-header" style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:16px;">
      <div>
        <h1 style="font-size:30px;">&#128233; {t("mail.title")}</h1>
        <p class="subtitle" style="font-size:16px;">AI-powered inbox triage — your emails organized by what matters. <span style="font-size:13px;color:var(--text-muted);cursor:pointer;" onclick="document.getElementById('shortcuts-modal').style.display='flex'">Press <kbd style="background:var(--border-light);padding:1px 6px;border-radius:4px;font-size:12px;border:1px solid var(--border);">?</kbd> for shortcuts</span></p>
      </div>
      <div class="btn-group" style="display:flex;align-items:center;gap:12px;">
        <span id="peek-badge" style="display:none;font-size:13px;font-weight:600;color:var(--primary);background:var(--primary-light,rgba(99,102,241,0.1));padding:6px 14px;border-radius:var(--radius-xs);white-space:nowrap;"></span>
        <button onclick="openComposeModal()" class="btn btn-green" style="font-size:15px;padding:10px 22px;">&#9997; Compose</button>
        <button onclick="syncInbox()" class="btn btn-primary" id="sync-btn" style="font-size:15px;padding:10px 22px;">&#128260; Sync Inbox</button>
      </div>
    </div>

    <!-- Search bar -->
    <div class="card" style="padding:12px 16px;margin-bottom:20px;display:flex;align-items:center;gap:12px;">
      <form method="GET" style="display:flex;align-items:center;gap:10px;flex:1;">
        <span style="font-size:18px;color:var(--text-muted);">&#128269;</span>
        <input type="text" name="q" id="mail-search-input" value="{_esc(search_q)}" placeholder="Search emails by subject, sender, body... (press / to focus)" style="flex:1;font-size:14px;padding:10px 14px;border:1px solid var(--border-light);border-radius:var(--radius-xs);margin-bottom:0;">
        <button type="submit" class="btn btn-primary btn-sm" style="font-size:13px;">Search</button>
        {'<a href="/mail-hub" class="btn btn-ghost btn-sm">Clear</a>' if search_q else ''}
      </form>
    </div>

    {"" if len(accounts) < 2 else '<div style="margin-bottom:16px;display:flex;align-items:center;gap:8px;flex-wrap:wrap;"><span style=' + '"' + 'font-size:13px;font-weight:600;color:var(--text-muted);' + '"' + '>Mailbox:</span><a href="/mail-hub" class="badge ' + ('badge-blue' if not account_id else 'badge-gray') + '" style="font-size:12px;text-decoration:none;padding:6px 12px;cursor:pointer;">All</a>' + ''.join(f'<a href="?account={a["id"]}" class="badge {("badge-blue" if account_id == a["id"] else "badge-gray")}" style="font-size:12px;text-decoration:none;padding:6px 12px;cursor:pointer;">{_esc(a["label"] or a["email"].split("@")[0])}</a>' for a in accounts) + '</div>'}

    <div class="stats-grid" style="margin-bottom:24px;">
      <div class="stat-card stat-blue" style="padding:22px;"><div class="num" style="font-size:34px;">{stats['total']}</div><div class="label" style="font-size:12px;">Total</div></div>
      <div class="stat-card stat-red" style="padding:22px;"><div class="num" style="font-size:34px;">{stats['unread']}</div><div class="label" style="font-size:12px;">Unread</div></div>
      <div class="stat-card stat-yellow" style="padding:22px;"><div class="num" style="font-size:34px;">{stats['urgent']}</div><div class="label" style="font-size:12px;">Urgent</div></div>
      <div class="stat-card stat-purple" style="padding:22px;"><div class="num" style="font-size:34px;">{stats['starred']}</div><div class="label" style="font-size:12px;">Starred</div></div>
      <div class="stat-card stat-green" style="padding:22px;"><div class="num" style="font-size:34px;">{stats['snoozed']}</div><div class="label" style="font-size:12px;">Snoozed</div></div>
    </div>

    <!-- Bulk action bar (hidden until checkboxes selected) -->
    <div id="bulk-bar" style="display:none;position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:var(--card);border:2px solid var(--primary);border-radius:var(--radius);padding:12px 24px;box-shadow:var(--shadow-lg);z-index:150;display:none;align-items:center;gap:12px;">
      <span id="bulk-count" style="font-weight:700;font-size:14px;color:var(--primary);">0 selected</span>
      <div style="width:1px;height:24px;background:var(--border);"></div>
      <button class="btn btn-ghost btn-sm" onclick="bulkAction('is_read', 1)">&#128065; Mark Read</button>
      <button class="btn btn-ghost btn-sm" onclick="bulkAction('is_read', 0)">&#9898; Mark Unread</button>
      <button class="btn btn-ghost btn-sm" onclick="bulkAction('is_starred', 1)">&#9733; Star</button>
      <button class="btn btn-ghost btn-sm" onclick="bulkAction('is_archived', 1)" style="color:var(--red);">&#128230; Archive</button>
      <button class="btn btn-ghost btn-sm" onclick="clearSelection()">&#10005; Clear</button>
    </div>

    <div style="display:grid;grid-template-columns:240px 1fr;gap:24px;">
      <!-- Sidebar -->
      <div>
        <div class="card" style="padding:18px;position:sticky;top:76px;max-height:calc(100vh - 96px);overflow-y:auto;">
          <div style="font-size:12px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:10px;">Filters</div>
          <a href="?filter=all" class="{'btn btn-primary' if filter_by == 'all' and not category and not search_q else 'btn btn-ghost'}" style="width:100%;justify-content:flex-start;margin-bottom:4px;font-size:14px;padding:10px 14px;">&#128233; All ({stats['total']})</a>
          <a href="?filter=unread" class="{'btn btn-primary' if filter_by == 'unread' else 'btn btn-ghost'}" style="width:100%;justify-content:flex-start;margin-bottom:4px;font-size:14px;padding:10px 14px;">&#128308; Unread ({stats['unread']})</a>
          <a href="?filter=starred" class="{'btn btn-primary' if filter_by == 'starred' else 'btn btn-ghost'}" style="width:100%;justify-content:flex-start;margin-bottom:4px;font-size:14px;padding:10px 14px;">&#9733; Starred ({stats['starred']})</a>
          <a href="?filter=urgent" class="{'btn btn-primary' if filter_by == 'urgent' else 'btn btn-ghost'}" style="width:100%;justify-content:flex-start;margin-bottom:4px;font-size:14px;padding:10px 14px;">&#128680; Urgent ({stats['urgent']})</a>
          <a href="?filter=snoozed" class="{'btn btn-primary' if filter_by == 'snoozed' else 'btn btn-ghost'}" style="width:100%;justify-content:flex-start;margin-bottom:4px;font-size:14px;padding:10px 14px;">&#128340; Snoozed ({stats['snoozed']})</a>

          <div style="font-size:12px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;margin:16px 0 10px;">Categories</div>
          {cat_sidebar}

          {'<div style="font-size:12px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;margin:16px 0 10px;">&#128101; Contacts</div>' + sender_sidebar + ('<a href="/mail-hub" style="display:block;text-align:center;font-size:12px;color:var(--primary);margin-top:4px;text-decoration:none;">Clear filter</a>' if sender_filter else '') if top_senders else ''}

          {'<div style="font-size:12px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;margin:16px 0 10px;">&#128340; Scheduled (' + str(sched_count) + ')</div>' + sched_html if sched_count > 0 else ''}
        </div>
      </div>

      <!-- Email list -->
      <div class="card" style="padding:0;overflow:hidden;">
        <table style="margin:0;table-layout:fixed;width:100%;">
          <thead>
            <tr style="background:var(--bg);">
              <th style="width:36px;"><input type="checkbox" id="select-all" onchange="toggleSelectAll(this)" style="width:16px;height:16px;cursor:pointer;accent-color:var(--primary);"></th>
              <th style="width:26px;"></th>
              <th style="width:26px;"></th>
              <th style="width:180px;font-size:13px;">From</th>
              <th style="font-size:13px;">Subject / Tags</th>
              <th style="width:140px;text-align:right;font-size:13px;">Date / Actions</th>
            </tr>
          </thead>
          <tbody>{email_rows}</tbody>
        </table>
      </div>
    </div>

    <!-- Snooze modal -->
    <div id="snooze-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:200;justify-content:center;align-items:center;backdrop-filter:blur(4px);" onclick="if(event.target===this)this.style.display='none'">
      <div style="background:var(--card);border-radius:var(--radius);padding:32px;width:460px;max-width:90vw;box-shadow:var(--shadow-lg);">
        <h2 style="font-size:20px;margin-bottom:20px;">&#128340; Snooze & Remind</h2>
        <input type="hidden" id="snooze-mail-id">
        <div style="font-size:13px;color:var(--text-muted);margin-bottom:12px;">Choose when this email should resurface as <strong style="color:var(--yellow);">important</strong>:</div>

        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:16px;">
          <button class="btn btn-outline" onclick="snoozePreset('today')" style="justify-content:center;padding:12px;">&#127769; Later Today<br><span style="font-size:11px;color:var(--text-muted);">6:00 PM</span></button>
          <button class="btn btn-outline" onclick="snoozePreset('tomorrow')" style="justify-content:center;padding:12px;">&#9728; Tomorrow AM<br><span style="font-size:11px;color:var(--text-muted);">9:00 AM</span></button>
          <button class="btn btn-outline" onclick="snoozePreset('tomorrow-eve')" style="justify-content:center;padding:12px;">&#127769; Tomorrow PM<br><span style="font-size:11px;color:var(--text-muted);">6:00 PM</span></button>
          <button class="btn btn-outline" onclick="snoozePreset('next-monday')" style="justify-content:center;padding:12px;">&#128197; Next Monday<br><span style="font-size:11px;color:var(--text-muted);">9:00 AM</span></button>
        </div>

        <div class="form-group">
          <label>Custom Date & Time</label>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
            <input type="date" id="snooze-date" style="margin-bottom:0;">
            <input type="time" id="snooze-time" value="09:00" style="margin-bottom:0;">
          </div>
        </div>

        <div class="form-group" style="margin-top:12px;">
          <label>&#128221; Reminder Note <span style="font-weight:400;text-transform:none;color:var(--text-muted);">(optional)</span></label>
          <textarea id="snooze-note" rows="2" placeholder="e.g. Follow up on proposal, Check contract status..." style="font-size:14px;resize:vertical;"></textarea>
        </div>

        <div style="display:flex;gap:8px;margin-top:16px;">
          <button class="btn btn-primary" onclick="submitSnooze()" style="flex:1;font-size:15px;">&#128340; Set Reminder</button>
          <button class="btn btn-ghost" onclick="document.getElementById('snooze-modal').style.display='none'" style="font-size:15px;">Cancel</button>
        </div>
      </div>
    </div>

    <!-- Compose modal -->
    <div id="compose-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:200;justify-content:center;align-items:center;backdrop-filter:blur(4px);" onclick="if(event.target===this)this.style.display='none'">
      <div style="background:var(--card);border-radius:var(--radius);padding:32px;width:600px;max-width:90vw;max-height:90vh;overflow-y:auto;box-shadow:var(--shadow-lg);">
        <h2 style="font-size:20px;margin-bottom:20px;">&#9997; Compose Email</h2>

        {"" if len(accounts) < 2 else '<div class="form-group"><label>From</label><select id="compose-account" style="font-size:14px;">' + ''.join(f'<option value="{a["id"]}" {"selected" if a["is_default"] else ""}>{_esc(a["label"] or a["email"])} ({_esc(a["email"])})</option>' for a in accounts) + '</select></div>'}

        <div class="form-group">
          <label>To</label>
          <input type="email" id="compose-to" placeholder="recipient@example.com" style="font-size:14px;">
        </div>
        <div class="form-group">
          <label>Subject</label>
          <input type="text" id="compose-subject" placeholder="Email subject..." style="font-size:14px;">
        </div>
        <div class="form-group">
          <label>Message</label>
          <textarea id="compose-body" rows="10" placeholder="Write your email..." style="font-size:14px;line-height:1.6;font-family:inherit;resize:vertical;"></textarea>
        </div>

        <div class="form-group">
          <label style="cursor:pointer;display:inline-flex;align-items:center;gap:6px;font-size:13px;color:var(--text-muted);">
            &#128206; Attachments
          </label>
          <input type="file" id="compose-attachments" multiple style="font-size:13px;margin-top:4px;">
          <div id="compose-file-list" style="font-size:12px;color:var(--text-muted);margin-top:4px;"></div>
        </div>

        <div id="schedule-section" style="display:none;margin-bottom:14px;">
          <label>&#128197; Schedule for</label>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
            <input type="date" id="compose-date" style="margin-bottom:0;font-size:14px;">
            <input type="time" id="compose-time" value="09:00" style="margin-bottom:0;font-size:14px;">
          </div>
        </div>

        <div style="display:flex;gap:8px;margin-top:8px;">
          <button class="btn btn-primary" onclick="sendCompose('now')" id="compose-send-btn" style="flex:1;font-size:15px;">&#9993; Send Now</button>
          <button class="btn btn-yellow" onclick="toggleSchedule()" id="compose-schedule-toggle" style="font-size:15px;">&#128340; Schedule</button>
          <button class="btn btn-ghost" onclick="document.getElementById('compose-modal').style.display='none'" style="font-size:15px;">Cancel</button>
        </div>
        <div id="compose-status" style="margin-top:10px;font-size:13px;text-align:center;"></div>
      </div>
    </div>

    <!-- Keyboard shortcuts modal -->
    <div id="shortcuts-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:200;justify-content:center;align-items:center;backdrop-filter:blur(4px);" onclick="if(event.target===this)this.style.display='none'">
      <div style="background:var(--card);border-radius:var(--radius);padding:32px;width:420px;max-width:90vw;box-shadow:var(--shadow-lg);">
        <h2 style="font-size:20px;margin-bottom:20px;">&#9000; Keyboard Shortcuts</h2>
        <table style="width:100%;">
          <tbody>
            <tr><td style="padding:6px 0;"><kbd style="background:var(--bg);padding:2px 8px;border-radius:4px;font-size:13px;border:1px solid var(--border);font-family:monospace;">j</kbd> / <kbd style="background:var(--bg);padding:2px 8px;border-radius:4px;font-size:13px;border:1px solid var(--border);font-family:monospace;">k</kbd></td><td style="padding:6px 0;font-size:14px;">Navigate up/down</td></tr>
            <tr><td style="padding:6px 0;"><kbd style="background:var(--bg);padding:2px 8px;border-radius:4px;font-size:13px;border:1px solid var(--border);font-family:monospace;">o</kbd> / <kbd style="background:var(--bg);padding:2px 8px;border-radius:4px;font-size:13px;border:1px solid var(--border);font-family:monospace;">Enter</kbd></td><td style="padding:6px 0;font-size:14px;">Open email</td></tr>
            <tr><td style="padding:6px 0;"><kbd style="background:var(--bg);padding:2px 8px;border-radius:4px;font-size:13px;border:1px solid var(--border);font-family:monospace;">x</kbd></td><td style="padding:6px 0;font-size:14px;">Select/deselect</td></tr>
            <tr><td style="padding:6px 0;"><kbd style="background:var(--bg);padding:2px 8px;border-radius:4px;font-size:13px;border:1px solid var(--border);font-family:monospace;">s</kbd></td><td style="padding:6px 0;font-size:14px;">Toggle star</td></tr>
            <tr><td style="padding:6px 0;"><kbd style="background:var(--bg);padding:2px 8px;border-radius:4px;font-size:13px;border:1px solid var(--border);font-family:monospace;">e</kbd></td><td style="padding:6px 0;font-size:14px;">Archive</td></tr>
            <tr><td style="padding:6px 0;"><kbd style="background:var(--bg);padding:2px 8px;border-radius:4px;font-size:13px;border:1px solid var(--border);font-family:monospace;">/</kbd></td><td style="padding:6px 0;font-size:14px;">Focus search</td></tr>
            <tr><td style="padding:6px 0;"><kbd style="background:var(--bg);padding:2px 8px;border-radius:4px;font-size:13px;border:1px solid var(--border);font-family:monospace;">?</kbd></td><td style="padding:6px 0;font-size:14px;">Show this help</td></tr>
          </tbody>
        </table>
        <button class="btn btn-ghost" onclick="document.getElementById('shortcuts-modal').style.display='none'" style="width:100%;margin-top:16px;font-size:14px;">Close</button>
      </div>
    </div>

    <script>
    // --- Sync ---
    function syncInbox() {{
      const btn = document.getElementById('sync-btn');
      btn.innerHTML = '&#8987; Syncing...';
      btn.disabled = true;
      fetch('/api/mail-hub/sync', {{method: 'POST'}})
        .then(r => r.json())
        .then(data => {{
          if (data.new_emails > 0) {{
            btn.innerHTML = '&#9989; ' + data.new_emails + ' new! Refreshing...';
            setTimeout(() => location.reload(), 800);
          }} else if (data.new_emails === 0) {{
            btn.innerHTML = '&#10003; Already up to date';
            setTimeout(() => {{ btn.innerHTML = '&#128260; Sync Inbox'; btn.disabled = false; }}, 2000);
          }} else {{
            btn.innerHTML = '&#9888; ' + (data.error || 'Failed');
            setTimeout(() => {{ btn.innerHTML = '&#128260; Sync Inbox'; btn.disabled = false; }}, 3000);
          }}
        }})
        .catch(() => {{
          btn.innerHTML = '&#9888; Sync failed';
          setTimeout(() => {{ btn.innerHTML = '&#128260; Sync Inbox'; btn.disabled = false; }}, 2000);
        }});
    }}

    // --- Peek for new mail (lightweight, no sync cost) ---
    const isPaid = {'true' if is_paid else 'false'};
    let peekAutoSynced = false;

    function peekInbox() {{
      fetch('/api/mail-hub/peek', {{method: 'POST'}})
        .then(r => r.json())
        .then(data => {{
          const badge = document.getElementById('peek-badge');
          if (data.unseen > 0) {{
            badge.textContent = data.unseen + ' new email' + (data.unseen === 1 ? '' : 's') + ' waiting';
            badge.style.display = 'inline-block';
            // Auto-sync for paid tiers (once per page load)
            if (isPaid && !peekAutoSynced) {{
              peekAutoSynced = true;
              syncInbox();
            }}
          }} else {{
            badge.style.display = 'none';
          }}
        }})
        .catch(() => {{}});
    }}

    // Peek immediately on page load, then every 60s
    peekInbox();
    setInterval(peekInbox, 60000);

    // --- Star ---
    function toggleStar(id, el) {{
      const isStarred = el.style.color.includes('text-muted') || el.style.opacity === '0.3';
      fetch('/api/mail-hub/' + id + '/update', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{field: 'is_starred', value: isStarred ? 1 : 0}})
      }}).then(() => {{
        if (isStarred) {{
          el.style.color = 'var(--yellow)';
          el.style.opacity = '1';
        }} else {{
          el.style.color = 'var(--text-muted)';
          el.style.opacity = '0.3';
        }}
      }});
    }}

    // --- Read ---
    function markRead(id, btn) {{
      const row = btn.closest('tr');
      const isUnread = row.style.background.includes('primary');
      fetch('/api/mail-hub/' + id + '/update', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{field: 'is_read', value: isUnread ? 1 : 0}})
      }}).then(() => {{
        if (isUnread) {{
          row.style.background = '';
          btn.innerHTML = '&#9898;';
        }} else {{
          row.style.background = 'var(--primary-light)';
          btn.innerHTML = '&#128065;';
        }}
      }});
    }}

    // --- Archive ---
    function archiveEmail(id, btn) {{
      fetch('/api/mail-hub/' + id + '/update', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{field: 'is_archived', value: 1}})
      }}).then(() => {{
        const row = document.querySelector('tr[data-mail-id="' + id + '"]');
        if (row) {{
          row.style.opacity = '0';
          row.style.transition = 'opacity 0.3s';
          setTimeout(() => row.remove(), 300);
        }}
        updateBulkBar();
      }});
    }}

    // --- Snooze modal ---
    function openSnoozeModal(id) {{
      document.getElementById('snooze-mail-id').value = id;
      document.getElementById('snooze-note').value = '';
      document.getElementById('snooze-date').value = '';
      document.getElementById('snooze-time').value = '09:00';
      document.getElementById('snooze-modal').style.display = 'flex';
    }}

    function snoozePreset(preset) {{
      const now = new Date();
      let target;
      switch(preset) {{
        case 'today':
          target = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 18, 0, 0);
          if (target <= now) target.setDate(target.getDate() + 1);
          break;
        case 'tomorrow':
          target = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1, 9, 0, 0);
          break;
        case 'tomorrow-eve':
          target = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1, 18, 0, 0);
          break;
        case 'next-monday':
          target = new Date(now);
          const day = target.getDay();
          const daysUntilMon = day === 0 ? 1 : (8 - day);
          target.setDate(target.getDate() + daysUntilMon);
          target.setHours(9, 0, 0, 0);
          break;
      }}
      const dateStr = target.getFullYear() + '-' + String(target.getMonth()+1).padStart(2,'0') + '-' + String(target.getDate()).padStart(2,'0');
      document.getElementById('snooze-date').value = dateStr;
      document.getElementById('snooze-time').value = String(target.getHours()).padStart(2,'0') + ':' + String(target.getMinutes()).padStart(2,'0');
      submitSnooze();
    }}

    function submitSnooze() {{
      const id = document.getElementById('snooze-mail-id').value;
      const date = document.getElementById('snooze-date').value;
      const time = document.getElementById('snooze-time').value || '09:00';
      const note = document.getElementById('snooze-note').value.trim();

      if (!date) {{
        alert('Please select a date');
        return;
      }}
      const snoozeUntil = date + ' ' + time + ':00';

      // Set snooze_until
      fetch('/api/mail-hub/' + id + '/update', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{field: 'snooze_until', value: snoozeUntil}})
      }}).then(() => {{
        // Set snooze_note if provided
        if (note) {{
          return fetch('/api/mail-hub/' + id + '/update', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{field: 'snooze_note', value: note}})
          }});
        }}
      }}).then(() => {{
        document.getElementById('snooze-modal').style.display = 'none';
        const row = document.querySelector('tr[data-mail-id="' + id + '"]');
        if (row) {{
          row.style.opacity = '0.3';
          setTimeout(() => row.remove(), 500);
        }}
      }});
    }}

    // --- Ctrl-click multi-select ---
    var _lastCheckedIdx = null;

    function handleCheckboxClick(cb, event) {{
      const boxes = Array.from(document.querySelectorAll('.mail-checkbox'));
      const idx = boxes.indexOf(cb);
      if (event.ctrlKey && _lastCheckedIdx !== null && _lastCheckedIdx !== idx) {{
        const start = Math.min(_lastCheckedIdx, idx);
        const end = Math.max(_lastCheckedIdx, idx);
        const state = cb.checked;
        for (let i = start; i <= end; i++) {{
          boxes[i].checked = state;
        }}
      }}
      _lastCheckedIdx = idx;
      updateBulkBar();
    }}

    function handleShiftClick(row, event) {{
      event.preventDefault();
      const cb = row.querySelector('.mail-checkbox');
      if (!cb) return;
      cb.checked = !cb.checked;
      handleCheckboxClick(cb, event);
    }}

    // --- Bulk actions ---
    function toggleSelectAll(el) {{
      document.querySelectorAll('.mail-checkbox').forEach(cb => cb.checked = el.checked);
      _lastCheckedIdx = null;
      updateBulkBar();
    }}

    function updateBulkBar() {{
      const checked = document.querySelectorAll('.mail-checkbox:checked');
      const bar = document.getElementById('bulk-bar');
      if (checked.length > 0) {{
        bar.style.display = 'flex';
        document.getElementById('bulk-count').textContent = checked.length + ' selected';
      }} else {{
        bar.style.display = 'none';
      }}
    }}

    function clearSelection() {{
      document.querySelectorAll('.mail-checkbox').forEach(cb => cb.checked = false);
      document.getElementById('select-all').checked = false;
      updateBulkBar();
    }}

    function bulkAction(field, value) {{
      const ids = Array.from(document.querySelectorAll('.mail-checkbox:checked')).map(cb => parseInt(cb.value));
      if (ids.length === 0) return;
      fetch('/api/mail-hub/bulk', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{ids: ids, field: field, value: value}})
      }}).then(r => r.json()).then(data => {{
        if (data.updated > 0) {{
          if (field === 'is_archived') {{
            ids.forEach(id => {{
              const row = document.querySelector('tr[data-mail-id="' + id + '"]');
              if (row) {{ row.style.opacity = '0'; setTimeout(() => row.remove(), 300); }}
            }});
          }} else {{
            location.reload();
          }}
          clearSelection();
        }}
      }});
    }}

    // --- Compose ---
    let scheduleMode = false;
    function openComposeModal() {{
      document.getElementById('compose-to').value = '';
      document.getElementById('compose-subject').value = '';
      document.getElementById('compose-body').value = '';
      document.getElementById('compose-status').innerHTML = '';
      scheduleMode = false;
      document.getElementById('schedule-section').style.display = 'none';
      document.getElementById('compose-send-btn').innerHTML = '&#9993; Send Now';
      document.getElementById('compose-modal').style.display = 'flex';
    }}

    function toggleSchedule() {{
      scheduleMode = !scheduleMode;
      const sec = document.getElementById('schedule-section');
      const btn = document.getElementById('compose-send-btn');
      if (scheduleMode) {{
        sec.style.display = 'block';
        btn.innerHTML = '&#128340; Schedule Send';
        // Default to tomorrow 9am
        const tom = new Date();
        tom.setDate(tom.getDate() + 1);
        document.getElementById('compose-date').value = tom.getFullYear() + '-' + String(tom.getMonth()+1).padStart(2,'0') + '-' + String(tom.getDate()).padStart(2,'0');
        document.getElementById('compose-time').value = '09:00';
      }} else {{
        sec.style.display = 'none';
        btn.innerHTML = '&#9993; Send Now';
      }}
    }}

    function sendCompose(mode) {{
      const to = document.getElementById('compose-to').value.trim();
      const subject = document.getElementById('compose-subject').value.trim();
      const body = document.getElementById('compose-body').value.trim();
      const status = document.getElementById('compose-status');
      const btn = document.getElementById('compose-send-btn');
      const acctSelect = document.getElementById('compose-account');
      const accountId = acctSelect ? acctSelect.value : null;

      if (!to || !subject || !body) {{
        status.innerHTML = '<span style="color:var(--red);">Please fill in all fields.</span>';
        return;
      }}

      if (scheduleMode) {{
        const date = document.getElementById('compose-date').value;
        const time = document.getElementById('compose-time').value || '09:00';
        if (!date) {{
          status.innerHTML = '<span style="color:var(--red);">Please select a date for scheduling.</span>';
          return;
        }}
        btn.disabled = true;
        btn.innerHTML = '&#8987; Scheduling...';
        fetch('/api/mail-hub/schedule', {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{to_email: to, subject: subject, body: body, scheduled_at: date + ' ' + time + ':00', account_id: accountId}})
        }}).then(r => r.json()).then(data => {{
          if (data.id) {{
            status.innerHTML = '<span style="color:var(--green);">&#10003; Scheduled for ' + date + ' ' + time + '</span>';
            btn.innerHTML = '&#10003; Scheduled';
            setTimeout(() => location.reload(), 1500);
          }} else {{
            status.innerHTML = '<span style="color:var(--red);">&#9888; ' + (data.error || 'Failed') + '</span>';
            btn.innerHTML = '&#128340; Schedule Send';
            btn.disabled = false;
          }}
        }}).catch(() => {{
          status.innerHTML = '<span style="color:var(--red);">&#9888; Network error</span>';
          btn.innerHTML = '&#128340; Schedule Send';
          btn.disabled = false;
        }});
      }} else {{
        btn.disabled = true;
        btn.innerHTML = '&#8987; Sending...';
        const fileInput = document.getElementById('compose-attachments');
        const fd = new FormData();
        fd.append('to_email', to);
        fd.append('subject', subject);
        fd.append('body', body);
        if (accountId) fd.append('account_id', accountId);
        if (fileInput && fileInput.files.length > 0) {{
          for (const f of fileInput.files) fd.append('attachments', f);
        }}
        fetch('/api/mail-hub/send-compose', {{
          method: 'POST',
          body: fd
        }}).then(r => r.json()).then(data => {{
          if (data.ok) {{
            status.innerHTML = '<span style="color:var(--green);">&#10003; Email sent!</span>';
            btn.innerHTML = '&#10003; Sent';
            setTimeout(() => {{ document.getElementById('compose-modal').style.display = 'none'; }}, 1500);
          }} else {{
            status.innerHTML = '<span style="color:var(--red);">&#9888; ' + (data.error || 'Send failed') + '</span>';
            btn.innerHTML = '&#9993; Send Now';
            btn.disabled = false;
          }}
        }}).catch(() => {{
          status.innerHTML = '<span style="color:var(--red);">&#9888; Network error</span>';
          btn.innerHTML = '&#9993; Send Now';
          btn.disabled = false;
        }});
      }}
    }}

    // File list preview
    document.getElementById('compose-attachments').addEventListener('change', function() {{
      const list = document.getElementById('compose-file-list');
      if (this.files.length === 0) {{ list.innerHTML = ''; return; }}
      const names = Array.from(this.files).map(f => f.name + ' (' + (f.size/1024 < 1024 ? (f.size/1024).toFixed(0) + ' KB' : (f.size/1024/1024).toFixed(1) + ' MB') + ')');
      list.innerHTML = '&#128206; ' + names.join(', ');
    }});
    </script>
    """, active_page="mail_hub", wide=True)


# ---------------------------------------------------------------------------
# API — Email Accounts (multi-mailbox)
# ---------------------------------------------------------------------------

@app.route("/api/email-accounts", methods=["POST"])
def api_add_email_account():
    """Add a new email account after testing IMAP + SMTP connection."""
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401
    from outreach.db import get_email_accounts, create_email_account, get_subscription
    from outreach.config import PLAN_LIMITS

    data = request.get_json()
    if not data or not data.get("email") or not data.get("password"):
        return jsonify({"error": "Email and password are required"}), 400

    # Check mailbox limit
    sub = get_subscription(session["client_id"])
    plan = sub.get("plan", "free") if sub else "free"
    limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
    max_mb = limits.get("mailboxes", 1)
    existing = get_email_accounts(session["client_id"])
    if max_mb != -1 and len(existing) >= max_mb:
        return jsonify({"error": f"Mailbox limit reached ({max_mb}). Upgrade your plan for more."}), 429

    email_addr = data["email"].strip().lower()
    password = data["password"]
    imap_host = data.get("imap_host", "imap.gmail.com")
    imap_port = int(data.get("imap_port", 993))
    smtp_host = data.get("smtp_host", "smtp.gmail.com")
    smtp_port = int(data.get("smtp_port", 465))
    label = data.get("label", "").strip() or email_addr.split("@")[0].title()

    # Test IMAP connection
    import imaplib
    try:
        imap = imaplib.IMAP4_SSL(imap_host, imap_port, timeout=15)
        imap.login(email_addr, password)
        imap.logout()
    except Exception as e:
        return jsonify({"error": f"IMAP connection failed: {str(e)[:100]}"}), 400

    # Test SMTP connection — try SSL (465) first, fall back to STARTTLS (587)
    import smtplib
    smtp_connected = False
    smtp_error = ""
    try:
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=15) as srv:
            srv.login(email_addr, password)
        smtp_connected = True
    except Exception as e:
        smtp_error = str(e)[:100]
        # Fallback: try STARTTLS on port 587
        if smtp_port == 465:
            try:
                with smtplib.SMTP(smtp_host, 587, timeout=15) as srv:
                    srv.starttls()
                    srv.login(email_addr, password)
                smtp_connected = True
                smtp_port = 587  # Save the working port
            except Exception:
                pass
    if not smtp_connected:
        return jsonify({"error": f"SMTP connection failed: {smtp_error}"}), 400

    try:
        acct_id = create_email_account(
            client_id=session["client_id"],
            label=label, email=email_addr, password=password,
            imap_host=imap_host, imap_port=imap_port,
            smtp_host=smtp_host, smtp_port=smtp_port,
        )
        return jsonify({"id": acct_id})
    except Exception as e:
        if "UNIQUE" in str(e):
            return jsonify({"error": "This email is already connected"}), 400
        return jsonify({"error": str(e)}), 500


@app.route("/api/email-accounts/<int:account_id>/default", methods=["POST"])
def api_set_default_account(account_id):
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401
    from outreach.db import update_email_account
    update_email_account(account_id, session["client_id"], is_default=1)
    return jsonify({"ok": True})


@app.route("/api/email-accounts/<int:account_id>", methods=["DELETE"])
def api_delete_email_account(account_id):
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401
    from outreach.db import delete_email_account
    delete_email_account(account_id, session["client_id"])
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# API — Mail Hub
# ---------------------------------------------------------------------------

@app.route("/api/mail-hub/peek", methods=["POST"])
def api_mail_peek():
    """Quick IMAP check: count emails arrived since the last sync."""
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401
    try:
        from outreach.db import get_email_accounts, get_db
        from outreach.mail_hub import peek_unseen
        from datetime import datetime, timedelta

        # Find the date of the most recent synced email
        with get_db() as db:
            row = db.execute(
                "SELECT MAX(received_at) FROM mail_inbox WHERE client_id = ?",
                (session["client_id"],)).fetchone()
            last_synced = row[0] if row and row[0] else None

        # Convert last synced date to IMAP format; count DB emails from that date
        if last_synced:
            try:
                dt = datetime.strptime(last_synced[:10], "%Y-%m-%d")
            except ValueError:
                dt = datetime.now()
            imap_since = dt.strftime("%d-%b-%Y")
            db_since = dt.strftime("%Y-%m-%d")
        else:
            # No emails synced yet — check last 3 days
            dt = datetime.now() - timedelta(days=3)
            imap_since = dt.strftime("%d-%b-%Y")
            db_since = dt.strftime("%Y-%m-%d")

        # Count DB emails from that date onward
        with get_db() as db:
            db_count = db.execute(
                "SELECT COUNT(*) FROM mail_inbox WHERE client_id = ? AND received_at >= ?",
                (session["client_id"], db_since)).fetchone()[0]

        # Count IMAP emails from that date onward
        accounts = get_email_accounts(session["client_id"])
        imap_total = 0
        if accounts:
            for acct in accounts:
                n = peek_unseen(
                    imap_host=acct["imap_host"], imap_port=acct["imap_port"],
                    imap_user=acct["email"], imap_password=acct["password"],
                    since_date=imap_since)
                if n > 0:
                    imap_total += n
        else:
            n = peek_unseen(since_date=imap_since)
            if n > 0:
                imap_total = n

        # New = IMAP count since last sync minus what's already in DB
        waiting = max(imap_total - db_count, 0)
        return jsonify({"unseen": waiting})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/mail-hub/sync", methods=["POST"])
def api_mail_sync():
    """Sync inbox via IMAP and classify with AI — syncs ALL connected email accounts."""
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401
    try:
        from outreach.db import check_limit, increment_usage, get_email_accounts
        allowed, used, limit = check_limit(session["client_id"], "mail_hub_syncs")
        if not allowed:
            return jsonify({"error": f"Monthly Mail Hub sync limit reached ({used}/{limit}). Upgrade your plan for unlimited syncs."}), 429

        from outreach.mail_hub import sync_inbox
        accounts = get_email_accounts(session["client_id"])
        total_new = 0
        if accounts:
            for acct in accounts:
                n = sync_inbox(session["client_id"], days=3, account_id=acct["id"])
                total_new += n
        else:
            # Fallback to .env credentials if no accounts configured
            total_new = sync_inbox(session["client_id"], days=3)
        # Only count as a used sync if new emails were actually found
        if total_new > 0:
            increment_usage(session["client_id"], "mail_hub_syncs")
        return jsonify({"new_emails": total_new})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/mail-hub/<int:mail_id>/update", methods=["POST"])
def api_mail_update(mail_id):
    """Update a mail item field (star, read, archive, snooze, priority, category)."""
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401
    from outreach.db import update_mail_field
    data = request.get_json()
    if not data or "field" not in data:
        return jsonify({"error": "missing field"}), 400
    ok = update_mail_field(mail_id, session["client_id"], data["field"], data.get("value"))
    return jsonify({"ok": ok})


@app.route("/api/mail-hub/bulk", methods=["POST"])
def api_mail_bulk():
    """Bulk update multiple mail items."""
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401
    from outreach.db import bulk_update_mail
    data = request.get_json()
    if not data or "ids" not in data or "field" not in data:
        return jsonify({"error": "missing ids or field"}), 400
    ids = [int(i) for i in data["ids"]]
    updated = bulk_update_mail(ids, session["client_id"], data["field"], data.get("value"))
    return jsonify({"updated": updated})


@app.route("/api/mail-hub/send-compose", methods=["POST"])
def api_mail_send_compose():
    """Send a composed email immediately via SMTP — supports account_id and file attachments."""
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401

    # Support both JSON and multipart/form-data (for attachments)
    if request.content_type and 'multipart/form-data' in request.content_type:
        to_email = request.form.get("to_email", "")
        subject = request.form.get("subject", "")
        body = request.form.get("body", "")
        acct_id_raw = request.form.get("account_id")
        files = request.files.getlist("attachments")
    else:
        data = request.get_json() or {}
        to_email = data.get("to_email", "")
        subject = data.get("subject", "")
        body = data.get("body", "")
        acct_id_raw = data.get("account_id")
        files = []

    if not to_email or not subject or not body:
        return jsonify({"error": "to_email, subject, and body are required"}), 400

    MAX_ATTACHMENT_SIZE = 25 * 1024 * 1024  # 25 MB total
    total_size = sum(f.seek(0, 2) or f.tell() for f in files)
    for f in files:
        f.seek(0)
    if total_size > MAX_ATTACHMENT_SIZE:
        return jsonify({"error": "Attachments exceed 25 MB limit."}), 400

    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        from email.mime.base import MIMEBase
        from email import encoders
        from outreach.config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD
        from outreach.db import get_email_account, get_default_email_account

        smtp_host, smtp_port, smtp_user, smtp_pw = SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD
        if acct_id_raw:
            acct = get_email_account(int(acct_id_raw), session["client_id"])
            if acct:
                smtp_host, smtp_port = acct["smtp_host"], acct["smtp_port"]
                smtp_user, smtp_pw = acct["email"], acct["password"]
        elif not smtp_user:
            acct = get_default_email_account(session["client_id"])
            if acct:
                smtp_host, smtp_port = acct["smtp_host"], acct["smtp_port"]
                smtp_user, smtp_pw = acct["email"], acct["password"]

        msg = MIMEMultipart()
        msg["From"] = smtp_user
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))

        for f in files:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
            encoders.encode_base64(part)
            safe_name = f.filename.replace('"', '_') if f.filename else "attachment"
            part.add_header("Content-Disposition", f'attachment; filename="{safe_name}"')
            msg.attach(part)

        if smtp_port == 587:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as srv:
                srv.starttls()
                srv.login(smtp_user, smtp_pw)
                srv.send_message(msg)
        else:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30) as srv:
                srv.login(smtp_user, smtp_pw)
                srv.send_message(msg)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/mail-hub/schedule", methods=["POST"])
def api_mail_schedule():
    """Schedule an email to be sent later."""
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401
    from outreach.db import create_scheduled_email
    data = request.get_json()
    if not data or not data.get("to_email") or not data.get("subject") or not data.get("body") or not data.get("scheduled_at"):
        return jsonify({"error": "to_email, subject, body, and scheduled_at are required"}), 400
    email_id = create_scheduled_email(
        session["client_id"],
        to_email=data["to_email"],
        subject=data["subject"],
        body=data["body"],
        scheduled_at=data["scheduled_at"],
        to_name=data.get("to_name", ""),
        reply_to_mail_id=data.get("reply_to_mail_id"),
        account_id=data.get("account_id"),
    )
    return jsonify({"id": email_id})


@app.route("/api/mail-hub/scheduled/<int:email_id>/delete", methods=["POST"])
def api_scheduled_delete(email_id):
    """Cancel a pending scheduled email."""
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401
    from outreach.db import delete_scheduled_email
    delete_scheduled_email(email_id, session["client_id"])
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Route — Mail Hub detail view
# ---------------------------------------------------------------------------

@app.route("/mail-hub/<int:mail_id>")
def mail_hub_detail(mail_id):
    if not _logged_in():
        return redirect(url_for("login"))
    from outreach.db import get_mail_item, update_mail_field, get_contact_by_email, get_email_account, get_email_accounts

    mail = get_mail_item(mail_id, session["client_id"])
    if not mail:
        session.setdefault("_flashes", []).append(("error", "Email not found."))
        return redirect(url_for("mail_hub"))

    # Auto-mark as read
    if not mail["is_read"]:
        update_mail_field(mail_id, session["client_id"], "is_read", 1)

    # Check if sender is a saved contact
    saved_contact = get_contact_by_email(session["client_id"], mail["from_email"])

    # Get account info for this email
    mail_account = get_email_account(mail["account_id"], session["client_id"]) if mail.get("account_id") else None
    all_accounts = get_email_accounts(session["client_id"])

    pri_config = {
        "urgent": ("&#128308;", "badge-red"),
        "important": ("&#128992;", "badge-yellow"),
        "normal": ("&#128309;", "badge-blue"),
        "low": ("&#11035;", "badge-gray"),
    }
    cat_config = {
        "action_required": ("&#9889;", "Action Required"),
        "meeting": ("&#128197;", "Meeting"),
        "fyi": ("&#128196;", "FYI"),
        "newsletter": ("&#128240;", "Newsletter"),
        "personal": ("&#128100;", "Personal"),
        "spam": ("&#128681;", "Spam"),
        "uncategorized": ("&#128233;", "Uncategorized"),
    }

    pi, pb = pri_config.get(mail["priority"], ("&#128309;", "badge-blue"))
    ci, cl = cat_config.get(mail["category"], ("&#128233;", mail["category"]))
    from_display = _esc(mail["from_name"]) if mail["from_name"] else _esc(mail["from_email"])
    star_icon = "&#9733;" if mail["is_starred"] else "&#9734;"
    star_color = "color:var(--yellow);" if mail["is_starred"] else "color:var(--text-muted);"
    body_html = _esc(mail["body_preview"]).replace("\\n", "<br>")
    subject = _esc(mail["subject"] or "(no subject)")
    time_str = mail["received_at"][:19] if mail["received_at"] else ""

    return _render(t("mail.title"), f"""
    <div class="breadcrumb"><a href="/dashboard">Dashboard</a> / <a href="/mail-hub">{t("mail.title")}</a> / Email</div>

    <div style="display:grid;grid-template-columns:1fr 380px;gap:24px;align-items:start;">
      <!-- Main email view -->
      <div style="min-width:0;">
        <div class="card" style="padding:0;overflow:hidden;">
          <!-- Header -->
          <div style="padding:28px 32px 20px;border-bottom:1px solid var(--border-light);">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:16px;">
              <div style="flex:1;">
                <h1 style="font-size:24px;font-weight:700;margin-bottom:12px;line-height:1.3;">{subject}</h1>
                <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                  <span class="badge {pb}" style="font-size:12px;">{pi} {mail['priority'].title()}</span>
                  <span class="badge badge-gray" style="font-size:12px;">{ci} {cl}</span>
                </div>
              </div>
              <div style="display:flex;gap:8px;align-items:center;flex-shrink:0;">
                <span style="cursor:pointer;font-size:24px;{star_color}" id="detail-star"
                      onclick="toggleDetailStar({mail['id']}, this);">{star_icon}</span>
                <button class="btn btn-ghost btn-sm" onclick="archiveAndBack({mail['id']})" title="Archive" style="font-size:16px;">&#128230;</button>
              </div>
            </div>
          </div>

          <!-- Sender info -->
          <div style="padding:20px 32px;border-bottom:1px solid var(--border-light);display:flex;justify-content:space-between;align-items:center;">
            <div style="display:flex;align-items:center;gap:14px;">
              <div style="width:48px;height:48px;border-radius:50%;background:linear-gradient(135deg,var(--primary),#8B5CF6);display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:20px;">
                {_esc(from_display[:1].upper())}
              </div>
              <div>
                <div style="font-weight:600;font-size:16px;">{from_display}{'  <a href="/contacts/' + str(saved_contact['id']) + '" class="badge badge-blue" style="font-size:10px;text-decoration:none;vertical-align:middle;">' + _esc(saved_contact.get('relationship','').title() or 'Contact') + ' &#8599;</a>' if saved_contact else ''}</div>
                <div style="font-size:13px;color:var(--text-muted);">{_esc(mail['from_email'])}</div>
              </div>
            </div>
            <div style="display:flex;align-items:center;gap:12px;">
              <div style="text-align:right;">
                <div style="font-size:13px;color:var(--text-muted);">{time_str}</div>
                <div style="font-size:12px;color:var(--text-muted);margin-top:2px;">To: {_esc(mail['to_email'])}</div>
              </div>
              {'<a href="/contacts/' + str(saved_contact['id']) + '" class="btn btn-ghost btn-sm" style="font-size:12px;">&#128101; View Contact</a>' if saved_contact else '<button class="btn btn-primary btn-sm" onclick="saveContact()" id="save-contact-btn" style="font-size:12px;">&#128101; Save Contact</button>'}
            </div>
          </div>

          <!-- AI Summary -->
          {"<div style='padding:16px 32px;background:var(--primary-light);border-bottom:1px solid var(--border-light);'><div style=font-size:11px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;>AI Summary</div><div style=font-size:14px;color:var(--primary);font-style:italic;>" + _esc(mail['ai_summary']) + "</div></div>" if mail.get('ai_summary') else ""}

          <!-- Email body -->
          <div style="padding:28px 32px;font-size:15px;line-height:1.8;color:var(--text-secondary);word-break:break-word;overflow-wrap:break-word;overflow-x:auto;max-width:100%;">
            {body_html}
          </div>
        </div>

        <!-- Back link -->
        <div style="margin-top:8px;">
          <a href="/mail-hub" class="btn btn-ghost" style="font-size:14px;">&#8592; Back to Inbox</a>
        </div>
      </div>

      <!-- Reply panel -->
      <div style="position:sticky;top:76px;">
        <div class="card" style="padding:24px;">
          <h3 style="font-size:18px;font-weight:700;margin-bottom:16px;">&#9993; Reply</h3>

          {"" if len(all_accounts) < 2 else '<div style="margin-bottom:14px;"><label style="font-size:12px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;display:block;margin-bottom:6px;">From</label><select id="reply-account" style="font-size:14px;width:100%;padding:10px 14px;border-radius:var(--radius-xs);border:1px solid var(--border-light);background:var(--card);">' + ''.join(f'<option value="{a["id"]}" {"selected" if (mail_account and a["id"] == mail_account["id"]) or (not mail_account and a["is_default"]) else ""}>{_esc(a["label"] or a["email"])} ({_esc(a["email"])})</option>' for a in all_accounts) + '</select></div>'}

          <div style="margin-bottom:14px;">
            <label style="font-size:12px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;display:block;margin-bottom:6px;">To</label>
            <div style="font-size:14px;padding:10px 14px;background:var(--bg);border-radius:var(--radius-xs);border:1px solid var(--border-light);">{_esc(mail['from_email'])}</div>
          </div>

          <div style="margin-bottom:14px;">
            <label style="font-size:12px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;display:block;margin-bottom:6px;">Subject</label>
            <input type="text" id="reply-subject" value="Re: {subject}" style="font-size:14px;width:100%;padding:10px 14px;border-radius:var(--radius-xs);border:1px solid var(--border-light);background:var(--card);">
          </div>

          <div style="margin-bottom:14px;">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
              <label style="font-size:12px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;">Message</label>
              <button class="btn btn-ghost btn-sm" onclick="generateDraft({mail['id']})" id="ai-draft-btn" style="font-size:12px;">&#9889; AI Draft</button>
            </div>
            <textarea id="reply-body" rows="10" placeholder="Type your reply..." style="font-size:14px;width:100%;padding:12px 14px;border-radius:var(--radius-xs);border:1px solid var(--border-light);background:var(--card);resize:vertical;line-height:1.6;font-family:inherit;"></textarea>
          </div>

          <div style="margin-bottom:14px;">
            <label style="cursor:pointer;display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;">
              &#128206; Attachments
            </label>
            <input type="file" id="reply-attachments" multiple style="font-size:13px;margin-top:4px;width:100%;">
            <div id="reply-file-list" style="font-size:12px;color:var(--text-muted);margin-top:4px;"></div>
          </div>

          <div style="display:flex;gap:8px;">
            <button class="btn btn-primary" onclick="sendReply({mail['id']})" id="send-btn" style="flex:1;font-size:15px;padding:12px 0;">&#9993; Send Reply</button>
            <button class="btn btn-yellow" onclick="toggleReplySchedule()" id="reply-sched-toggle" style="font-size:14px;padding:12px 14px;">&#128340;</button>
          </div>
          <div id="reply-schedule-section" style="display:none;margin-top:10px;">
            <label style="font-size:11px;font-weight:600;color:var(--text-muted);text-transform:uppercase;">Schedule for</label>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
              <input type="date" id="reply-sched-date" style="margin-bottom:0;font-size:13px;">
              <input type="time" id="reply-sched-time" value="09:00" style="margin-bottom:0;font-size:13px;">
            </div>
          </div>
          <div id="reply-status" style="margin-top:10px;font-size:13px;text-align:center;"></div>
        </div>
      </div>
    </div>

    <script>
    function toggleDetailStar(id, el) {{
      const isStarred = el.innerHTML.charCodeAt(0) === 9734;
      fetch('/api/mail-hub/' + id + '/update', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{field: 'is_starred', value: isStarred ? 1 : 0}})
      }}).then(() => {{
        el.innerHTML = isStarred ? '&#9733;' : '&#9734;';
        el.style.color = isStarred ? 'var(--yellow)' : 'var(--text-muted)';
      }});
    }}

    function archiveAndBack(id) {{
      fetch('/api/mail-hub/' + id + '/update', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{field: 'is_archived', value: 1}})
      }}).then(() => window.location = '/mail-hub');
    }}

    function saveContact() {{
      const btn = document.getElementById('save-contact-btn');
      btn.innerHTML = '&#8987; Saving...';
      btn.disabled = true;
      fetch('/api/contacts/quick-save', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{email: '{mail["from_email"]}', name: '{_esc(mail.get("from_name", ""))}', from_mail_id: {mail['id']}}})
      }})
        .then(r => r.json())
        .then(data => {{
          if (data.id) {{
            btn.outerHTML = '<a href="/contacts/' + data.id + '" class="btn btn-ghost btn-sm" style="font-size:12px;">&#128101; View Contact</a>';
          }} else {{
            btn.innerHTML = '&#9888; Failed';
            btn.disabled = false;
          }}
        }})
        .catch(() => {{ btn.innerHTML = '&#128101; Save Contact'; btn.disabled = false; }});
    }}

    function generateDraft(id) {{
      const btn = document.getElementById('ai-draft-btn');
      btn.innerHTML = '&#8987; Generating...';
      btn.disabled = true;
      fetch('/api/mail-hub/' + id + '/draft', {{method: 'POST'}})
        .then(r => r.json())
        .then(data => {{
          if (data.draft) {{
            document.getElementById('reply-body').value = data.draft;
            btn.innerHTML = '&#9889; Regenerate';
          }} else {{
            btn.innerHTML = '&#9888; Failed';
          }}
          btn.disabled = false;
        }})
        .catch(() => {{
          btn.innerHTML = '&#9889; AI Draft';
          btn.disabled = false;
        }});
    }}

    function sendReply(id) {{
      const btn = document.getElementById('send-btn');
      const body = document.getElementById('reply-body').value.trim();
      const subject = document.getElementById('reply-subject').value.trim();
      const status = document.getElementById('reply-status');
      const acctSelect = document.getElementById('reply-account');
      const accountId = acctSelect ? acctSelect.value : null;

      if (!body) {{
        status.innerHTML = '<span style="color:var(--red);">Please write a message first.</span>';
        return;
      }}

      // Check if scheduling
      const schedSection = document.getElementById('reply-schedule-section');
      if (schedSection.style.display !== 'none') {{
        const date = document.getElementById('reply-sched-date').value;
        const time = document.getElementById('reply-sched-time').value || '09:00';
        if (!date) {{
          status.innerHTML = '<span style="color:var(--red);">Please select a date for scheduling.</span>';
          return;
        }}
        btn.innerHTML = '&#8987; Scheduling...';
        btn.disabled = true;
        fetch('/api/mail-hub/schedule', {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{to_email: '{mail["from_email"]}', subject: subject, body: body, scheduled_at: date + ' ' + time + ':00', reply_to_mail_id: id, account_id: accountId}})
        }}).then(r => r.json()).then(data => {{
          if (data.id) {{
            status.innerHTML = '<span style="color:var(--green);">&#10003; Reply scheduled for ' + date + ' ' + time + '</span>';
            btn.innerHTML = '&#10003; Scheduled';
          }} else {{
            status.innerHTML = '<span style="color:var(--red);">&#9888; ' + (data.error || 'Failed') + '</span>';
            btn.innerHTML = '&#128340; Schedule Reply';
            btn.disabled = false;
          }}
        }});
        return;
      }}

      btn.innerHTML = '&#8987; Sending...';
      btn.disabled = true;
      const fileInput = document.getElementById('reply-attachments');
      const fd = new FormData();
      fd.append('subject', subject);
      fd.append('body', body);
      if (accountId) fd.append('account_id', accountId);
      if (fileInput && fileInput.files.length > 0) {{
        for (const f of fileInput.files) fd.append('attachments', f);
      }}
      fetch('/api/mail-hub/' + id + '/send-reply', {{
        method: 'POST',
        body: fd
      }})
        .then(r => r.json())
        .then(data => {{
          if (data.ok) {{
            status.innerHTML = '<span style="color:var(--green);">&#10003; Reply sent successfully!</span>';
            btn.innerHTML = '&#10003; Sent';
            document.getElementById('reply-body').value = '';
          }} else {{
            status.innerHTML = '<span style="color:var(--red);">&#9888; ' + (data.error || 'Send failed') + '</span>';
            btn.innerHTML = '&#9993; Send Reply';
            btn.disabled = false;
          }}
        }})
        .catch(() => {{
          status.innerHTML = '<span style="color:var(--red);">&#9888; Network error</span>';
          btn.innerHTML = '&#9993; Send Reply';
          btn.disabled = false;
        }});
    }}

    let replyScheduleMode = false;
    function toggleReplySchedule() {{
      replyScheduleMode = !replyScheduleMode;
      const sec = document.getElementById('reply-schedule-section');
      const btn = document.getElementById('send-btn');
      if (replyScheduleMode) {{
        sec.style.display = 'block';
        btn.innerHTML = '&#128340; Schedule Reply';
        const tom = new Date();
        tom.setDate(tom.getDate() + 1);
        document.getElementById('reply-sched-date').value = tom.getFullYear() + '-' + String(tom.getMonth()+1).padStart(2,'0') + '-' + String(tom.getDate()).padStart(2,'0');
      }} else {{
        sec.style.display = 'none';
        btn.innerHTML = '&#9993; Send Reply';
      }}
    }}

    // File list preview
    document.getElementById('reply-attachments').addEventListener('change', function() {{
      const list = document.getElementById('reply-file-list');
      if (this.files.length === 0) {{ list.innerHTML = ''; return; }}
      const names = Array.from(this.files).map(f => f.name + ' (' + (f.size/1024 < 1024 ? (f.size/1024).toFixed(0) + ' KB' : (f.size/1024/1024).toFixed(1) + ' MB') + ')');
      list.innerHTML = '&#128206; ' + names.join(', ');
    }});
    </script>
    """, active_page="mail_hub", wide=True)


# ---------------------------------------------------------------------------
# API — Mail Hub: AI draft & send reply
# ---------------------------------------------------------------------------

@app.route("/api/mail-hub/<int:mail_id>/draft", methods=["POST"])
def api_mail_draft(mail_id):
    """Generate an AI reply draft for a mail hub email, enriched with contact context."""
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401
    from outreach.db import get_mail_item, get_contact_by_email
    mail = get_mail_item(mail_id, session["client_id"])
    if not mail:
        return jsonify({"error": "not found"}), 404
    try:
        contact = get_contact_by_email(session["client_id"], mail["from_email"])
        from outreach.ai import _get_mail_reply_draft
        draft = _get_mail_reply_draft(
            from_name=mail["from_name"],
            from_email=mail["from_email"],
            subject=mail["subject"],
            body=mail["body_preview"],
            priority=mail["priority"],
            category=mail["category"],
            sender_name=SENDER_NAME,
            contact_context=contact,
        )
        return jsonify({"draft": draft})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/mail-hub/<int:mail_id>/send-reply", methods=["POST"])
def api_mail_send_reply(mail_id):
    """Send a reply to a mail hub email via SMTP — supports file attachments."""
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401
    from outreach.db import get_mail_item, get_email_account, get_default_email_account
    mail = get_mail_item(mail_id, session["client_id"])
    if not mail:
        return jsonify({"error": "not found"}), 404

    # Support both JSON and multipart/form-data (for attachments)
    if request.content_type and 'multipart/form-data' in request.content_type:
        body = request.form.get("body", "")
        subject = request.form.get("subject", f"Re: {mail['subject']}")
        acct_id = request.form.get("account_id") or mail.get("account_id")
        files = request.files.getlist("attachments")
    else:
        data = request.get_json() or {}
        body = data.get("body", "")
        subject = data.get("subject", f"Re: {mail['subject']}")
        acct_id = data.get("account_id") or mail.get("account_id")
        files = []

    if not body:
        return jsonify({"error": "empty body"}), 400

    MAX_ATTACHMENT_SIZE = 25 * 1024 * 1024  # 25 MB total
    total_size = sum(f.seek(0, 2) or f.tell() for f in files)
    for f in files:
        f.seek(0)
    if total_size > MAX_ATTACHMENT_SIZE:
        return jsonify({"error": "Attachments exceed 25 MB limit."}), 400

    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        from email.mime.base import MIMEBase
        from email import encoders
        from outreach.config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD

        # Use the account the email was received on, or a specified account, or default
        smtp_host, smtp_port, smtp_user, smtp_pw = SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD
        if acct_id:
            acct = get_email_account(acct_id, session["client_id"])
            if acct:
                smtp_host, smtp_port = acct["smtp_host"], acct["smtp_port"]
                smtp_user, smtp_pw = acct["email"], acct["password"]
        elif not smtp_user:
            acct = get_default_email_account(session["client_id"])
            if acct:
                smtp_host, smtp_port = acct["smtp_host"], acct["smtp_port"]
                smtp_user, smtp_pw = acct["email"], acct["password"]

        msg = MIMEMultipart()
        msg["From"] = smtp_user
        msg["To"] = mail["from_email"]
        msg["Subject"] = subject
        if mail.get("message_id"):
            msg["In-Reply-To"] = mail["message_id"]
            msg["References"] = mail["message_id"]
        msg.attach(MIMEText(body, "plain", "utf-8"))

        for f in files:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
            encoders.encode_base64(part)
            safe_name = f.filename.replace('"', '_') if f.filename else "attachment"
            part.add_header("Content-Disposition", f'attachment; filename="{safe_name}"')
            msg.attach(part)

        if smtp_port == 587:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as srv:
                srv.starttls()
                srv.login(smtp_user, smtp_pw)
                srv.send_message(msg)
        else:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30) as srv:
                srv.login(smtp_user, smtp_pw)
                srv.send_message(msg)

        # Update last_contacted on the contact if they exist
        from outreach.db import get_contact_by_email, update_contact
        contact = get_contact_by_email(session["client_id"], mail["from_email"])
        if contact:
            from datetime import datetime
            update_contact(contact["id"], session["client_id"],
                           last_contacted=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ---------------------------------------------------------------------------
# Routes — Contacts Book
# ---------------------------------------------------------------------------

@app.route("/contacts")
def contacts_page():
    if not _logged_in():
        return redirect(url_for("login"))
    from outreach.db import get_contacts

    search = request.args.get("q", "")
    rel_filter = request.args.get("rel", "")
    tag_filter = request.args.get("tag", "")
    contacts = get_contacts(session["client_id"], search=search,
                            relationship=rel_filter, tag=tag_filter)

    # Collect all unique tags and relationships for filters
    all_tags = set()
    all_rels = set()
    for c in contacts:
        if c.get("tags"):
            all_tags.update(t.strip() for t in c["tags"].split(",") if t.strip())
        if c.get("relationship"):
            all_rels.add(c["relationship"])

    rel_options = ""
    for r in sorted(all_rels):
        sel = "selected" if rel_filter == r else ""
        rel_options += f'<option value="{_esc(r)}" {sel}>{_esc(r.title())}</option>'

    tag_badges = ""
    for t in sorted(all_tags):
        active = "background:var(--primary);color:#fff;" if tag_filter == t else ""
        tag_badges += f'<a href="?tag={_esc(t)}" class="badge badge-gray" style="text-decoration:none;font-size:12px;cursor:pointer;{active}">{_esc(t)}</a> '

    # Build contact cards
    contact_cards = ""
    for c in contacts:
        initials = (c["name"][:1] if c["name"] else c["email"][:1]).upper()
        rel_badge = f'<span class="badge badge-blue" style="font-size:10px;">{_esc(c["relationship"].title())}</span>' if c.get("relationship") else ""
        tags_html = ""
        if c.get("tags"):
            for t in c["tags"].split(","):
                t = t.strip()
                if t:
                    tags_html += f'<span class="badge badge-gray" style="font-size:9px;">{_esc(t)}</span> '
        last = c["last_contacted"][:10] if c.get("last_contacted") else "Never"
        notes_preview = _esc(c["notes"][:80]) + "..." if len(c.get("notes", "")) > 80 else _esc(c.get("notes", ""))

        contact_cards += f"""
        <a href="/contacts/{c['id']}" class="card" style="text-decoration:none;color:inherit;display:flex;align-items:flex-start;gap:16px;padding:20px;cursor:pointer;transition:box-shadow 0.15s;">
          <div style="width:50px;height:50px;border-radius:50%;background:linear-gradient(135deg,var(--primary),#8B5CF6);display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:20px;flex-shrink:0;">
            {initials}
          </div>
          <div style="flex:1;min-width:0;">
            <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
              <span style="font-weight:600;font-size:16px;">{_esc(c['name'] or c['email'])}</span>
              {rel_badge}
            </div>
            <div style="font-size:13px;color:var(--text-muted);margin-top:2px;">{_esc(c['email'])}</div>
            {'<div style="font-size:13px;color:var(--text-secondary);margin-top:2px;">' + _esc(c['company']) + (' — ' + _esc(c['role']) if c.get('role') else '') + '</div>' if c.get('company') else ''}
            {'<div style="font-size:12px;color:var(--text-secondary);margin-top:4px;font-style:italic;">' + notes_preview + '</div>' if notes_preview else ''}
            <div style="display:flex;align-items:center;gap:6px;margin-top:6px;flex-wrap:wrap;">
              {tags_html}
              <span style="font-size:11px;color:var(--text-muted);margin-left:auto;">Last contact: {last}</span>
            </div>
          </div>
        </a>"""

    if not contact_cards:
        contact_cards = f"""
        <div class="empty" style="padding:60px;">
          <div class="empty-icon">&#128101;</div>
          <h3>{'No contacts match your search' if search or rel_filter or tag_filter else 'No contacts yet'}</h3>
          <p>{'Try different filters.' if search or rel_filter or tag_filter else 'Open an email in Mail Hub and click "Save Contact" to start building your contact book.'}</p>
        </div>"""

    return _render(t("contacts.title"), f"""
    <div class="breadcrumb"><a href="/dashboard">{t("dash.title")}</a> / {t("contacts.title")}</div>
    <div class="page-header" style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:16px;">
      <div>
        <h1 style="font-size:30px;">&#128101; {t("contacts.title")}</h1>
        <p class="subtitle" style="font-size:16px;">Your personal contact book — AI uses this context to write perfect replies.</p>
      </div>
      <div class="btn-group">
        <button onclick="document.getElementById('add-modal').style.display='flex'" class="btn btn-primary" style="font-size:15px;padding:10px 22px;">&#43; Add Contact</button>
      </div>
    </div>

    <!-- Filters -->
    <div class="card" style="padding:16px;display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:20px;">
      <form method="GET" style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;flex:1;">
        <input type="text" name="q" value="{_esc(search)}" placeholder="Search name, email, company..." style="font-size:14px;padding:10px 14px;border-radius:var(--radius-xs);border:1px solid var(--border-light);min-width:250px;">
        <select name="rel" style="font-size:14px;padding:10px 14px;border-radius:var(--radius-xs);border:1px solid var(--border-light);">
          <option value="">All Relationships</option>
          {rel_options}
        </select>
        <button type="submit" class="btn btn-ghost" style="font-size:14px;">&#128269; Filter</button>
        {'<a href="/contacts" class="btn btn-ghost" style="font-size:13px;">Clear</a>' if search or rel_filter or tag_filter else ''}
      </form>
    </div>
    {'<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:16px;">' + tag_badges + '</div>' if tag_badges else ''}

    <div style="font-size:13px;color:var(--text-muted);margin-bottom:12px;">{len(contacts)} contact{'s' if len(contacts) != 1 else ''}</div>

    <!-- Contact cards -->
    <div style="display:grid;grid-template-columns:repeat(auto-fill, minmax(480px, 1fr));gap:12px;">
      {contact_cards}
    </div>

    <!-- Add Contact Modal -->
    <div id="add-modal" style="display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.5);z-index:200;justify-content:center;align-items:center;" onclick="if(event.target===this)this.style.display='none'">
      <div style="background:var(--card);border-radius:var(--radius);padding:32px;width:500px;max-width:90vw;max-height:90vh;overflow-y:auto;box-shadow:var(--shadow-lg);">
        <h2 style="font-size:20px;margin-bottom:20px;">&#128101; New Contact</h2>
        <form method="POST" action="/contacts/add">
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
            <div class="form-group"><label>Email *</label><input name="email" type="email" required style="font-size:14px;"></div>
            <div class="form-group"><label>Name</label><input name="name" style="font-size:14px;"></div>
            <div class="form-group"><label>Company</label><input name="company" style="font-size:14px;"></div>
            <div class="form-group"><label>Role</label><input name="role" style="font-size:14px;"></div>
            <div class="form-group"><label>Relationship</label>
              <select name="relationship" style="font-size:14px;">
                <option value="">Select...</option>
                <option value="client">Client</option>
                <option value="colleague">Colleague</option>
                <option value="vendor">Vendor</option>
                <option value="lead">Lead</option>
                <option value="friend">Friend</option>
                <option value="other">Other</option>
              </select>
            </div>
            <div class="form-group"><label>Tags</label><input name="tags" placeholder="comma,separated" style="font-size:14px;"></div>
          </div>
          <div class="form-group" style="margin-top:8px;"><label>Notes</label><textarea name="notes" rows="3" placeholder="Who are they? What do they care about?" style="font-size:14px;"></textarea></div>
          <div class="form-group"><label>Personality / Communication Style</label><textarea name="personality" rows="2" placeholder="e.g. 'Direct and concise, prefers bullet points, casual tone'" style="font-size:14px;"></textarea></div>
          <div style="display:flex;gap:8px;margin-top:16px;">
            <button type="submit" class="btn btn-primary" style="flex:1;font-size:15px;">Save Contact</button>
            <button type="button" class="btn btn-ghost" onclick="document.getElementById('add-modal').style.display='none'" style="font-size:15px;">Cancel</button>
          </div>
        </form>
      </div>
    </div>
    """, active_page="contacts", wide=True)


@app.route("/contacts/add", methods=["POST"])
def contacts_add():
    if not _logged_in():
        return redirect(url_for("login"))
    from outreach.db import upsert_contact
    email = request.form.get("email", "").strip()
    if not email:
        session.setdefault("_flashes", []).append(("error", "Email is required."))
        return redirect(url_for("contacts_page"))
    upsert_contact(
        session["client_id"], email,
        name=request.form.get("name", "").strip(),
        company=request.form.get("company", "").strip(),
        role=request.form.get("role", "").strip(),
        relationship=request.form.get("relationship", "").strip(),
        notes=request.form.get("notes", "").strip(),
        personality=request.form.get("personality", "").strip(),
        tags=request.form.get("tags", "").strip(),
    )
    session.setdefault("_flashes", []).append(("success", f"Contact {email} saved."))
    return redirect(url_for("contacts_page"))


@app.route("/contacts/<int:contact_id>")
def contact_detail(contact_id):
    if not _logged_in():
        return redirect(url_for("login"))
    from outreach.db import get_contact, get_contact_email_history

    contact = get_contact(contact_id, session["client_id"])
    if not contact:
        session.setdefault("_flashes", []).append(("error", "Contact not found."))
        return redirect(url_for("contacts_page"))

    history = get_contact_email_history(session["client_id"], contact["email"])
    initials = (contact["name"][:1] if contact["name"] else contact["email"][:1]).upper()

    # Check if this contact's emails are already marked important
    all_important = all(h.get("priority") == "important" for h in history) if history else False

    # Build email history
    history_html = ""
    for h in history:
        pri_colors = {"urgent": "badge-red", "important": "badge-yellow", "normal": "badge-blue", "low": "badge-gray"}
        pb = pri_colors.get(h["priority"], "badge-blue")
        history_html += f"""
        <a href="/mail-hub/{h['id']}" class="card" style="text-decoration:none;color:inherit;padding:14px 20px;display:block;">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;">
            <div style="flex:1;">
              <div style="font-weight:600;font-size:14px;">{_esc(h['subject'] or '(no subject)')}</div>
              <div style="font-size:13px;color:var(--text-secondary);margin-top:3px;max-height:18px;overflow:hidden;">{_esc(h['body_preview'][:100])}</div>
              {'<div style="font-size:12px;color:var(--primary);margin-top:3px;font-style:italic;">' + _esc(h['ai_summary']) + '</div>' if h.get('ai_summary') else ''}
            </div>
            <div style="text-align:right;flex-shrink:0;margin-left:16px;">
              <span class="badge {pb}" style="font-size:10px;">{h['priority']}</span>
              <div style="font-size:11px;color:var(--text-muted);margin-top:4px;">{h['received_at'][:10] if h.get('received_at') else ''}</div>
            </div>
          </div>
        </a>"""

    if not history_html:
        history_html = '<div style="padding:20px;text-align:center;color:var(--text-muted);font-size:14px;">No emails from this contact yet.</div>'

    # Tags display
    tags_html = ""
    if contact.get("tags"):
        for t in contact["tags"].split(","):
            t = t.strip()
            if t:
                tags_html += f'<span class="badge badge-gray" style="font-size:12px;">{_esc(t)}</span> '

    rel_badge = f'<span class="badge badge-blue" style="font-size:14px;">{_esc(contact["relationship"].title())}</span>' if contact.get("relationship") else ""

    return _render("Contact", f"""
    <div class="breadcrumb"><a href="/dashboard">Dashboard</a> / <a href="/contacts">Contacts</a> / {_esc(contact['name'] or contact['email'])}</div>

    <div style="display:grid;grid-template-columns:1fr 400px;gap:24px;align-items:start;">
      <!-- Left: Profile -->
      <div>
        <div class="card" style="padding:0;overflow:hidden;">
          <!-- Header -->
          <div style="padding:32px;background:linear-gradient(135deg,var(--primary-light),#F0EAFF);border-bottom:1px solid var(--border-light);">
            <div style="display:flex;align-items:center;gap:20px;">
              <div style="width:72px;height:72px;border-radius:50%;background:linear-gradient(135deg,var(--primary),#8B5CF6);display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:28px;flex-shrink:0;">
                {initials}
              </div>
              <div>
                <h1 style="font-size:26px;font-weight:700;">{_esc(contact['name'] or contact['email'])}</h1>
                <div style="font-size:14px;color:var(--text-muted);margin-top:2px;">{_esc(contact['email'])}</div>
                <div style="display:flex;gap:8px;margin-top:8px;align-items:center;flex-wrap:wrap;">
                  {rel_badge}
                  {tags_html}
                </div>
              </div>
            </div>
          </div>

          <!-- Details -->
          <div style="padding:24px 32px;">
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
              <div>
                <div style="font-size:11px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">Company</div>
                <div style="font-size:15px;">{_esc(contact['company']) or '<span style="color:var(--text-muted);">Not set</span>'}</div>
              </div>
              <div>
                <div style="font-size:11px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">Role</div>
                <div style="font-size:15px;">{_esc(contact['role']) or '<span style="color:var(--text-muted);">Not set</span>'}</div>
              </div>
              <div>
                <div style="font-size:11px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">Added</div>
                <div style="font-size:15px;">{contact['created_at'][:10] if contact.get('created_at') else '—'}</div>
              </div>
              <div>
                <div style="font-size:11px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">Last Contacted</div>
                <div style="font-size:15px;">{contact['last_contacted'][:10] if contact.get('last_contacted') else 'Never'}</div>
              </div>
            </div>

            {'<div style="margin-top:20px;"><div style="font-size:11px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">Notes</div><div style="font-size:14px;line-height:1.6;color:var(--text-secondary);background:var(--bg);padding:14px;border-radius:var(--radius-xs);">' + _esc(contact['notes']).replace(chr(10), '<br>') + '</div></div>' if contact.get('notes') else ''}

            {'<div style="margin-top:16px;"><div style="font-size:11px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">&#129504; AI Personality Guide</div><div style="font-size:14px;line-height:1.6;color:var(--primary);font-style:italic;background:var(--primary-light);padding:14px;border-radius:var(--radius-xs);">' + _esc(contact['personality']) + '</div></div>' if contact.get('personality') else ''}
          </div>
        </div>

        <!-- Back -->
        <div style="margin-top:8px;display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
          <a href="/contacts" class="btn btn-ghost" style="font-size:14px;">&#8592; Back to Contacts</a>
          <button class="btn {'btn-ghost' if all_important else 'btn-yellow'} btn-sm" onclick="toggleImportant({contact['id']}, {'true' if all_important else 'false'})" id="mark-imp-btn" title="{'Remove important priority' if all_important else 'Mark all emails from this contact as important'}">{'&#10003; Marked Important' if all_important else '&#11088; Mark All Emails Important'}</button>
        </div>
      </div>

      <!-- Right column -->
      <div style="position:sticky;top:76px;">
        <!-- Edit card -->
        <div class="card" style="padding:24px;margin-bottom:16px;">
          <h3 style="font-size:16px;font-weight:700;margin-bottom:14px;">&#9998; Edit Contact</h3>
          <form method="POST" action="/contacts/{contact['id']}/edit">
            <div class="form-group"><label>Name</label><input name="name" value="{_esc(contact['name'])}" style="font-size:14px;"></div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
              <div class="form-group"><label>Company</label><input name="company" value="{_esc(contact['company'])}" style="font-size:14px;"></div>
              <div class="form-group"><label>Role</label><input name="role" value="{_esc(contact['role'])}" style="font-size:14px;"></div>
            </div>
            <div class="form-group"><label>Relationship</label>
              <select name="relationship" style="font-size:14px;">
                <option value="">Select...</option>
                <option value="client" {'selected' if contact.get('relationship')=='client' else ''}>Client</option>
                <option value="colleague" {'selected' if contact.get('relationship')=='colleague' else ''}>Colleague</option>
                <option value="vendor" {'selected' if contact.get('relationship')=='vendor' else ''}>Vendor</option>
                <option value="lead" {'selected' if contact.get('relationship')=='lead' else ''}>Lead</option>
                <option value="friend" {'selected' if contact.get('relationship')=='friend' else ''}>Friend</option>
                <option value="other" {'selected' if contact.get('relationship')=='other' else ''}>Other</option>
              </select>
            </div>
            <div class="form-group"><label>Tags</label><input name="tags" value="{_esc(contact['tags'])}" placeholder="comma,separated" style="font-size:14px;"></div>
            <div class="form-group"><label>Notes</label><textarea name="notes" rows="3" style="font-size:14px;">{_esc(contact['notes'])}</textarea></div>
            <div class="form-group"><label>Personality / Communication Style</label><textarea name="personality" rows="2" style="font-size:14px;" placeholder="e.g. 'Direct, prefers bullet points, casual tone'">{_esc(contact['personality'])}</textarea></div>
            <button type="submit" class="btn btn-primary" style="width:100%;font-size:14px;">Save Changes</button>
          </form>
          <form method="POST" action="/contacts/{contact['id']}/delete" style="margin-top:8px;" onsubmit="return confirm('Delete this contact?')">
            <button type="submit" class="btn btn-ghost" style="width:100%;font-size:13px;color:var(--red);">&#128465; Delete Contact</button>
          </form>
        </div>

        <!-- Email History -->
        <div class="card" style="padding:0;overflow:hidden;">
          <div style="padding:16px 20px;border-bottom:1px solid var(--border-light);">
            <h3 style="font-size:16px;font-weight:700;">&#128236; Email History ({len(history)})</h3>
          </div>
          <div style="max-height:400px;overflow-y:auto;">
            {history_html}
          </div>
        </div>
      </div>
    </div>
    <script>
    function toggleImportant(contactId, isCurrentlyImportant) {{
      const btn = document.getElementById('mark-imp-btn');
      btn.disabled = true;
      btn.textContent = 'Updating...';
      const newPriority = isCurrentlyImportant ? 'normal' : 'important';
      fetch('/api/contacts/' + contactId + '/mark-important', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{priority: newPriority}})
      }}).then(r => r.json()).then(data => {{
        if (data.ok) {{
          if (newPriority === 'important') {{
            btn.textContent = '\u2705 ' + data.updated + ' emails marked important';
            btn.style.background = 'var(--green)';
          }} else {{
            btn.textContent = '\u2705 ' + data.updated + ' emails set to normal';
            btn.style.background = 'var(--card)';
          }}
          setTimeout(() => location.reload(), 1200);
        }} else {{
          btn.textContent = 'Error';
          btn.disabled = false;
        }}
      }}).catch(() => {{
        btn.textContent = 'Error';
        btn.disabled = false;
      }});
    }}
    </script>
    """, active_page="contacts", wide=True)


@app.route("/contacts/<int:contact_id>/edit", methods=["POST"])
def contacts_book_edit(contact_id):
    if not _logged_in():
        return redirect(url_for("login"))
    from outreach.db import update_contact
    update_contact(
        contact_id, session["client_id"],
        name=request.form.get("name", "").strip(),
        company=request.form.get("company", "").strip(),
        role=request.form.get("role", "").strip(),
        relationship=request.form.get("relationship", "").strip(),
        notes=request.form.get("notes", "").strip(),
        personality=request.form.get("personality", "").strip(),
        tags=request.form.get("tags", "").strip(),
    )
    session.setdefault("_flashes", []).append(("success", "Contact updated."))
    return redirect(url_for("contact_detail", contact_id=contact_id))


@app.route("/contacts/<int:contact_id>/delete", methods=["POST"])
def contacts_book_delete(contact_id):
    if not _logged_in():
        return redirect(url_for("login"))
    from outreach.db import delete_contact_book
    delete_contact_book(contact_id, session["client_id"])
    session.setdefault("_flashes", []).append(("success", "Contact deleted."))
    return redirect(url_for("contacts_page"))


@app.route("/api/contacts/quick-save", methods=["POST"])
def api_contact_quick_save():
    """Quick-save a contact from the Mail Hub detail page."""
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401
    from outreach.db import upsert_contact, get_contact_by_email
    data = request.get_json()
    if not data or not data.get("email"):
        return jsonify({"error": "email required"}), 400
    upsert_contact(
        session["client_id"],
        data["email"],
        name=data.get("name", ""),
    )
    c = get_contact_by_email(session["client_id"], data["email"])
    return jsonify({"id": c["id"] if c else None})


@app.route("/api/contacts/<int:contact_id>/mark-important", methods=["POST"])
def api_contact_mark_important(contact_id):
    """Mark all emails from this contact as important."""
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401
    from outreach.db import get_contact, mark_contact_emails_priority
    contact = get_contact(contact_id, session["client_id"])
    if not contact:
        return jsonify({"error": "not found"}), 404
    data = request.get_json() or {}
    priority = data.get("priority", "important")
    count = mark_contact_emails_priority(session["client_id"], contact["email"], priority)
    return jsonify({"ok": True, "updated": count})


# ---------------------------------------------------------------------------
# Routes — Billing & Lemon Squeezy
# ---------------------------------------------------------------------------

@app.route("/billing")
def billing_page():
    if not _logged_in():
        return redirect(url_for("login"))
    from outreach.db import get_subscription, get_usage, check_limit
    from outreach.config import PLAN_LIMITS

    sub = get_subscription(session["client_id"])
    usage = get_usage(session["client_id"])
    plan = sub.get("plan", "free")
    limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])

    # Flash messages for redirect params
    if request.args.get("upgraded"):
        flash(("success", "Payment successful! Your plan has been upgraded."))
    if request.args.get("canceled"):
        flash(("info", "Checkout canceled. No changes made."))

    # Build usage bars
    def _bar(used, limit, label):
        if limit == -1:
            return f'<div class="usage-row"><span class="usage-label">{label}</span><span class="usage-val">{used} used &middot; <b>Unlimited</b></span><div class="progress-wrap" style="width:100%;margin-top:4px;"><div class="progress-bar bar-green" style="width:0%"></div></div></div>'
        pct = min(used / limit * 100, 100) if limit else 100
        color = "bar-green" if pct < 75 else ("bar-yellow" if pct < 90 else "bar-red")
        return f'<div class="usage-row"><span class="usage-label">{label}</span><span class="usage-val">{used} / {limit:,}</span><div class="progress-wrap" style="width:100%;margin-top:4px;"><div class="progress-bar {color}" style="width:{pct:.0f}%"></div></div></div>'

    usage_html = _bar(usage.get("emails_sent", 0), limits["emails_per_month"], "Emails Sent")
    usage_html += _bar(usage.get("mail_hub_syncs", 0), limits["mail_hub_syncs"], "Mail Hub Syncs")

    # Plan cards
    plan_order = ["free", "growth", "pro", "unlimited"]
    plan_labels = {"free": "Free", "growth": "Growth", "pro": "Pro", "unlimited": "Unlimited"}
    plan_prices = {"free": "$0", "growth": "$8.000", "pro": "$20.000", "unlimited": "$40.000"}
    plan_features = {
        "free": ["200 emails/month", "1 mailbox", "2 campaigns", "10 Mail Hub syncs/month", "Basic analytics"],
        "growth": ["2,000 emails/month", "3 mailboxes", "Unlimited campaigns", "Unlimited Mail Hub syncs", "AI email classification", "Reply sentiment analysis"],
        "pro": ["10,000 emails/month", "5 mailboxes", "Unlimited campaigns", "Unlimited Mail Hub syncs", "AI everything", "Priority support", "A/B testing insights"],
        "unlimited": ["Unlimited emails", "Unlimited mailboxes", "Unlimited everything", "AI everything", "Priority support", "CSV export", "All future features"],
    }

    cards = ""
    for p in plan_order:
        is_current = (p == plan)
        badge = ' <span class="badge badge-green">Current</span>' if is_current else ""
        features_html = "".join(f"<li>{f}</li>" for f in plan_features[p])

        if is_current:
            btn = '<button class="btn btn-outline btn-sm" disabled style="width:100%;justify-content:center;opacity:0.5;">Current Plan</button>'
        elif p == "free":
            btn = '<form method="post" action="/billing/downgrade"><button class="btn btn-outline btn-sm" style="width:100%;justify-content:center;">Downgrade</button></form>'
        else:
            btn = f'<form method="post" action="/billing/checkout"><input type="hidden" name="plan" value="{p}"><button class="btn btn-primary btn-sm" style="width:100%;justify-content:center;">{"Upgrade" if plan_order.index(p) > plan_order.index(plan) else "Switch"} to {plan_labels[p]}</button></form>'

        border = "border:2px solid var(--primary);" if is_current else ""
        cards += f"""
        <div class="card" style="flex:1;min-width:220px;max-width:280px;{border}">
          <div style="padding:20px;text-align:center;">
            <h3 style="margin:0;">{plan_labels[p]}{badge}</h3>
            <div style="font-size:36px;font-weight:800;margin:12px 0;">{plan_prices[p]}<span style="font-size:14px;font-weight:400;color:var(--text-muted);"> CLP/mo</span></div>
            <ul style="text-align:left;list-style:none;padding:0;margin:16px 0;">
              {features_html}
            </ul>
            {btn}
          </div>
        </div>"""

    status_badge = {"active": "badge-green", "past_due": "badge-yellow", "canceled": "badge-red"}.get(sub.get("status", "active"), "badge-gray")

    return _render(t("billing.title"), f"""
    <div class="page-header">
      <h1>&#128179; {t("billing.title")}</h1>
    </div>

    <div class="card" style="margin-bottom:24px;">
      <div class="card-header"><h2>{t("billing.current_usage")}</h2><span class="badge {status_badge}">{sub.get('status', 'active').replace('_', ' ').title()}</span></div>
      <div style="padding:20px;">
        {usage_html}
      </div>
    </div>

    <h2 style="margin-bottom:16px;">{t("billing.choose_plan")}</h2>
    <div style="display:flex;gap:16px;flex-wrap:wrap;justify-content:center;margin-bottom:32px;">
      {cards}
    </div>

    <style>
      .usage-row {{ margin-bottom:16px; }}
      .usage-label {{ font-weight:600;font-size:14px; }}
      .usage-val {{ float:right;font-size:13px;color:var(--text-muted); }}
      .bar-red {{ background:var(--red, #EF4444) !important; }}
      ul li {{ padding:6px 0;font-size:14px;color:var(--text-muted);border-bottom:1px solid var(--border); }}
      ul li:last-child {{ border-bottom:none; }}
    </style>
    """, active_page="billing")


@app.route("/billing/checkout", methods=["POST"])
def billing_checkout():
    """Create a Lemon Squeezy checkout for upgrading."""
    if not _logged_in():
        return redirect(url_for("login"))

    from outreach.config import LS_API_KEY, LS_STORE_ID, LS_VARIANT_GROWTH, LS_VARIANT_PRO, LS_VARIANT_UNLIMITED, BASE_URL
    from outreach.db import get_subscription, get_client

    plan = request.form.get("plan", "")
    variant_map = {"growth": LS_VARIANT_GROWTH, "pro": LS_VARIANT_PRO, "unlimited": LS_VARIANT_UNLIMITED}
    variant_id = variant_map.get(plan)

    if not variant_id or not LS_API_KEY:
        flash(("error", "Billing is not configured yet. Add your Lemon Squeezy keys to .env to enable payments."))
        return redirect(url_for("billing_page"))

    client = get_client(session["client_id"])
    sub = get_subscription(session["client_id"])

    try:
        import urllib.request, json
        checkout_data = {
            "data": {
                "type": "checkouts",
                "attributes": {
                    "test_mode": True,
                    "checkout_data": {
                        "email": client["email"],
                        "name": client.get("name", ""),
                        "custom": {
                            "client_id": str(session["client_id"]),
                            "plan": plan,
                        },
                    },
                    "product_options": {
                        "redirect_url": BASE_URL + "/billing?upgraded=1",
                    },
                },
                "relationships": {
                    "store": {"data": {"type": "stores", "id": LS_STORE_ID}},
                    "variant": {"data": {"type": "variants", "id": variant_id}},
                },
            }
        }
        req = urllib.request.Request(
            "https://api.lemonsqueezy.com/v1/checkouts",
            data=json.dumps(checkout_data).encode(),
            headers={
                "Authorization": f"Bearer {LS_API_KEY}",
                "Content-Type": "application/vnd.api+json",
                "Accept": "application/vnd.api+json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read())
            checkout_url = result["data"]["attributes"]["url"]
            return redirect(checkout_url)
    except Exception as e:
        import traceback; traceback.print_exc()
        if hasattr(e, 'read'):
            err_body = e.read().decode()
            print("LS API error body:", err_body)
            flash(("error", f"Payment error: {err_body}"))
        else:
            flash(("error", f"Payment error: {e}"))
        return redirect(url_for("billing_page"))


@app.route("/billing/downgrade", methods=["POST"])
def billing_downgrade():
    """Cancel Lemon Squeezy subscription and revert to free plan."""
    if not _logged_in():
        return redirect(url_for("login"))

    from outreach.config import LS_API_KEY
    from outreach.db import get_subscription, update_subscription

    sub = get_subscription(session["client_id"])

    if sub.get("stripe_subscription_id") and LS_API_KEY:
        try:
            import urllib.request, json
            ls_sub_id = sub["stripe_subscription_id"]
            cancel_data = json.dumps({
                "data": {"type": "subscriptions", "id": ls_sub_id, "attributes": {"cancelled": True}}
            }).encode()
            req = urllib.request.Request(
                f"https://api.lemonsqueezy.com/v1/subscriptions/{ls_sub_id}",
                data=cancel_data,
                headers={
                    "Authorization": f"Bearer {LS_API_KEY}",
                    "Content-Type": "application/vnd.api+json",
                    "Accept": "application/vnd.api+json",
                },
                method="PATCH",
            )
            urllib.request.urlopen(req)
        except Exception as e:
            print(f"[BILLING] Cancel error: {e}")

    update_subscription(session["client_id"], plan="free", stripe_subscription_id="", status="active")
    flash(("success", "Downgraded to Free plan."))
    return redirect(url_for("billing_page"))


@app.route("/ls/webhook", methods=["POST"])
def ls_webhook():
    """Handle Lemon Squeezy webhook events."""
    from outreach.config import LS_WEBHOOK_SECRET
    import hashlib, hmac, json

    payload = request.get_data()

    # Verify signature if secret is set
    if LS_WEBHOOK_SECRET:
        sig = request.headers.get("X-Signature", "")
        expected = hmac.new(LS_WEBHOOK_SECRET.encode(), payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            print("[LS] Webhook signature mismatch")
            return "Invalid signature", 400

    try:
        body = json.loads(payload)
    except Exception:
        return "Bad JSON", 400

    event_name = body.get("meta", {}).get("event_name", "")
    custom = body.get("meta", {}).get("custom_data", {})
    client_id = custom.get("client_id")
    plan = custom.get("plan", "growth")
    attrs = body.get("data", {}).get("attributes", {})
    ls_sub_id = str(body.get("data", {}).get("id", ""))
    ls_customer_id = str(attrs.get("customer_id", ""))

    from outreach.db import update_subscription, get_subscription_by_stripe_sub

    if event_name == "subscription_created":
        if client_id:
            update_subscription(int(client_id),
                                plan=plan,
                                stripe_customer_id=ls_customer_id,
                                stripe_subscription_id=ls_sub_id,
                                status="active")
            print(f"[LS] Client {client_id} upgraded to {plan}")

    elif event_name == "subscription_updated":
        sub_rec = get_subscription_by_stripe_sub(ls_sub_id)
        if sub_rec:
            status = attrs.get("status", "active")
            new_status = "active" if status == "active" else ("past_due" if status == "past_due" else "canceled")
            update_subscription(sub_rec["client_id"], status=new_status)

    elif event_name in ("subscription_cancelled", "subscription_expired"):
        sub_rec = get_subscription_by_stripe_sub(ls_sub_id)
        if sub_rec:
            update_subscription(sub_rec["client_id"], plan="free",
                                stripe_subscription_id="", status="active")
            print(f"[LS] Client {sub_rec['client_id']} subscription ended → free")

    elif event_name == "subscription_payment_failed":
        sub_rec = get_subscription_by_stripe_sub(ls_sub_id)
        if sub_rec:
            update_subscription(sub_rec["client_id"], status="past_due")
            print(f"[LS] Payment failed for client {sub_rec['client_id']}")

    return "ok", 200


@app.route("/pricing")
def pricing_page():
    """Public pricing page."""
    plan_data = [
        ("Free", "$0", "Get started with the basics", [
            "200 emails/month", "1 mailbox", "2 campaigns", "10 Mail Hub syncs/month",
            "Open tracking", "Reply detection", "Basic analytics"
        ]),
        ("Growth", "$8.000", "Scale your outreach", [
            "2,000 emails/month", "3 mailboxes", "Unlimited campaigns", "Unlimited Mail Hub syncs",
            "AI email classification", "AI reply sentiment", "A/B testing"
        ]),
        ("Pro", "$20.000", "Full power for pros", [
            "10,000 emails/month", "5 mailboxes", "Unlimited campaigns", "Unlimited Mail Hub syncs",
            "AI everything", "Smart send times", "Priority support", "CSV export"
        ]),
        ("Unlimited", "$40.000", "No limits, ever", [
            "Unlimited emails", "Unlimited mailboxes", "Unlimited everything", "AI everything",
            "Priority support", "All current & future features", "Custom integrations"
        ]),
    ]

    cards = ""
    for name, price, desc, features in plan_data:
        feat_html = "".join(f"<li>&#10003; {f}</li>" for f in features)
        highlight = "border:2px solid var(--primary);" if name == "Pro" else ""
        pop = ' <span class="badge badge-green" style="font-size:10px;">POPULAR</span>' if name == "Pro" else ""
        cards += f"""
        <div class="card" style="flex:1;min-width:240px;max-width:300px;{highlight}">
          <div style="padding:24px;text-align:center;">
            <h3 style="margin:0;font-size:20px;">{name}{pop}</h3>
            <p style="color:var(--text-muted);font-size:13px;margin:4px 0 16px;">{desc}</p>
            <div style="font-size:42px;font-weight:800;">{price}<span style="font-size:14px;font-weight:400;color:var(--text-muted);"> CLP/mo</span></div>
            <ul style="text-align:left;list-style:none;padding:0;margin:20px 0;">
              {feat_html}
            </ul>
            <a href="/register" class="btn btn-primary btn-sm" style="width:100%;justify-content:center;">{t("landing.start_free") if name == 'Free' else t("nav.get_started")}</a>
          </div>
        </div>"""

    return render_template_string(LAYOUT, title="Pricing", logged_in=_logged_in(),
        messages=[], active_page="pricing", client_name=session.get("client_name", ""),
        nav=t_dict("nav"), lang=session.get("lang", "en"),
        content=Markup(f"""
    <div style="max-width:1200px;margin:0 auto;padding:40px 20px;">
      <div style="text-align:center;margin-bottom:40px;">
        <h1 style="font-size:36px;margin-bottom:8px;">{t("pricing.title")}</h1>
        <p style="color:var(--text-muted);font-size:18px;">{t("pricing.subtitle")}</p>
      </div>
      <div style="display:flex;gap:20px;flex-wrap:wrap;justify-content:center;">
        {cards}
      </div>
      <div style="text-align:center;margin-top:32px;color:var(--text-muted);font-size:14px;">
        All plans include open tracking, reply detection, and AI-generated sequences.<br>
        Cancel anytime. No contracts.
      </div>
    </div>
    <style>
      ul li {{ padding:8px 0;font-size:14px;color:var(--text-muted);border-bottom:1px solid var(--border); }}
      ul li:last-child {{ border-bottom:none; }}
    </style>
    """))


# ---------------------------------------------------------------------------
# API — Usage check
# ---------------------------------------------------------------------------

@app.route("/api/usage")
def api_usage():
    """Return current usage and limits for the logged-in client."""
    if not _logged_in():
        return jsonify({"error": "unauthorized"}), 401
    from outreach.db import get_subscription, get_usage
    from outreach.config import PLAN_LIMITS
    sub = get_subscription(session["client_id"])
    usage = get_usage(session["client_id"])
    plan = sub.get("plan", "free")
    limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
    return jsonify({"plan": plan, "usage": usage, "limits": limits})


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=os.environ.get("FLASK_DEBUG", "0") == "1", host="0.0.0.0", port=port)

web: gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --preload --timeout 120 --max-requests 5000 --max-requests-jitter 500
worker: python worker.py

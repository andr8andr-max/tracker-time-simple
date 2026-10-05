"""
Точка входа локального запуска:  python main.py

(На VPS/PaaS используйте gunicorn:  gunicorn -w 2 -b 127.0.0.1:8000 backend.wsgi:app)
"""

import os

from backend.app import create_app

app = create_app()

if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    debug = str(os.environ.get("FLASK_DEBUG", "false")).lower() in {"1", "true", "yes"}
    print(f"Тайм-трекер запущен: http://{host}:{port}  (debug={debug})")
    app.run(host=host, port=port, debug=debug)

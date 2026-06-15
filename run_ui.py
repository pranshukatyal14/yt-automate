"""
AutoYT Web UI entry point.

Usage:
    .venv/bin/python run_ui.py
Then open http://localhost:5000 in your browser.
"""
from src.web.app import app

if __name__ == "__main__":
    print("\n🎬 AutoYT Web UI")
    print("   Open http://localhost:5001 in your browser\n")
    app.run(host="0.0.0.0", port=5001, debug=False, threaded=True)

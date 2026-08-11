import subprocess
import sys
import os
import threading
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.join(ROOT, "backend")

def run_backend():
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", "requirements.txt", "-q"],
        cwd=BACKEND
    )
    subprocess.run(
        [sys.executable, "-m", "uvicorn", "main:app", "--reload", "--port", "8000"],
        cwd=BACKEND
    )

def run_frontend():
    time.sleep(3)
    subprocess.run(
        [sys.executable, "-m", "streamlit", "run", "frontend_streamlit.py",
         "--server.port", "8501"],
        cwd=ROOT
    )

if __name__ == "__main__":
    print("RSL Scanner startet...")
    print("Frontend → http://localhost:8501")
    print("Backend  → http://localhost:8000")
    print("Ctrl+C zum Beenden\n")

    t1 = threading.Thread(target=run_backend, daemon=True)
    t2 = threading.Thread(target=run_frontend, daemon=True)

    t1.start()
    t2.start()

    try:
        t1.join()
        t2.join()
    except KeyboardInterrupt:
        print("\nBeendet.")
from pathlib import Path
import os

from dotenv import load_dotenv
from waitress import serve

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from app import create_app

app = create_app()

if __name__ == "__main__":
    serve(app, host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "8000")))

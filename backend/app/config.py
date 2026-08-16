import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parents[1] / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "suppliers.db"
FAISS_PATH = DATA_DIR / "docs.faiss"
META_PATH = DATA_DIR / "docs_meta.json"
COMPILED_PATH = DATA_DIR / "compiled_synthesizer.json"
TRACE_PATH = DATA_DIR / "compile_report.json"

EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
TEACHER_MODEL = os.getenv("TEACHER_MODEL", "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free")
STUDENT_MODEL = os.getenv("STUDENT_MODEL", "openrouter/openai/gpt-oss-20b:free")

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "supplierrisk")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

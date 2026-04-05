import os
import logging
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from dotenv import load_dotenv

# Basic logging setup
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
log = logging.getLogger(__name__)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROJECT_DOTENV_PATH = os.path.join(PROJECT_ROOT, ".env")
load_dotenv(dotenv_path=PROJECT_DOTENV_PATH, override=False, encoding="utf-8")

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    # Fallback for local development if DATABASE_URL is not in .env
    # This constructs the URL from individual components we set earlier
    db_user = os.getenv("POSTGRES_USER", "user")
    db_password = os.getenv("POSTGRES_PASSWORD", "password")
    db_name = os.getenv("POSTGRES_DB", "braingirl_db")
    db_port = os.getenv("DB_PORT", "5432")
    # In docker-compose, the hostname is the service name ('db').
    # For local scripts connecting to the Docker container, it's 'localhost'.
    if os.getenv("RUNNING_IN_DOCKER"):
        db_host = "odysseia_pg_db"
        log.info("Running inside Docker, connecting to 'db' host.")
    else:
        db_host = "localhost"
        log.info("Running on host machine, connecting to 'localhost'.")

    DATABASE_URL = (
        f"postgresql+asyncpg://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
    )

# 关闭 SQLAlchemy SQL echo，避免向量检索时把原始查询文本和向量参数打印到日志里
engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(autocommit=False, autoflush=False, bind=engine)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

import os
from functools import lru_cache
from pathlib import Path

import boto3
from aws_requests_auth.aws_auth import AWSRequestsAuth
from dotenv import load_dotenv
from opensearchpy import OpenSearch, RequestsHttpConnection

# Load environment variables
load_dotenv()


def _env_required(name: str) -> str:
    """Fetch a required environment variable as a non-empty string.

    Raises:
        OSError: if the variable is missing or empty.
    """
    value = os.getenv(name)
    if value is None or not isinstance(value, str) or value.strip() == "":
        raise OSError(f"{name} environment variable is required and must be a non-empty string")
    return value


class Settings:
    """Application settings and configuration"""

    # Database Configuration
    DB_BACKEND: str = os.getenv("DB_BACKEND", "sqlite")
    POSTGRES_USER: str = os.getenv("POSTGRES_USER", "esf")
    POSTGRES_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "esfpass")
    POSTGRES_HOST: str = os.getenv("POSTGRES_HOST", "localhost")
    POSTGRES_PORT: str = os.getenv("POSTGRES_PORT", "5432")
    POSTGRES_DB: str = os.getenv("POSTGRES_DB", "esf_rag")

    # Data directories (override base with DATA_DIR)
    DATA_DIR: Path = Path.cwd() / "data"
    DATABASE_DIR: Path = DATA_DIR / "databases"
    JSON_DATA_DIR: Path = DATA_DIR / "json"
    DTI_DATA_JSON: str = str(JSON_DATA_DIR / "dtidata.json")

    # AWS / OpenSearch
    AWS_ACCESS_KEY: str = _env_required("AWS_ACCESS_KEY")
    AWS_SECRET_KEY: str = _env_required("AWS_SECRET_KEY")
    AWS_REGION: str = _env_required("AWS_REGION")
    S3_BUCKET: str = _env_required("S3_BUCKET")
    OPENSEARCH_ENDPOINT: str = _env_required("OPENSEARCH_ENDPOINT")

    # OpenAI / Firecrawl
    OPENAI_API_KEY: str = _env_required("OPENAI_API_KEY")
    # If some libraries read from env
    os.environ["OPENAI_API_KEY"] = OPENAI_API_KEY
    FIRECRAWL_API_KEY: str = _env_required("FIRECRAWL_API_KEY")

    @property
    def DATA_CONTEXT_DB(self) -> str:
        if self.DB_BACKEND == "postgres":
            return self.POSTGRES_DB
        return str(self.DATABASE_DIR / "data_context.db")

    @property
    def postgres_dsn(self) -> str:
        return f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    def _db_name(self, name: str) -> str:
        if self.DB_BACKEND == "postgres":
            return name
        # sqlite path
        return str(self.DATABASE_DIR / f"{name}.db")


settings = Settings()


@lru_cache(maxsize=1)
def get_aws_auth() -> AWSRequestsAuth:
    """Create AWS authentication object (cached)."""
    return AWSRequestsAuth(
        aws_access_key=settings.AWS_ACCESS_KEY,
        aws_secret_access_key=settings.AWS_SECRET_KEY,
        aws_host=settings.OPENSEARCH_ENDPOINT,
        aws_region=settings.AWS_REGION,
        aws_service="aoss",
    )


@lru_cache(maxsize=1)
def get_s3_client():
    """Create S3 client (cached)."""
    return boto3.client(
        "s3",
        aws_access_key_id=settings.AWS_ACCESS_KEY,
        aws_secret_access_key=settings.AWS_SECRET_KEY,
        region_name=settings.AWS_REGION,
    )


@lru_cache(maxsize=1)
def get_firecrawl_app():
    """Create Firecrawl app instance (cached)."""
    from firecrawl import FirecrawlApp

    return FirecrawlApp(api_key=settings.FIRECRAWL_API_KEY)


@lru_cache(maxsize=1)
def get_opensearch_client():
    """Create OpenSearch client (cached)."""
    auth = get_aws_auth()
    return OpenSearch(
        hosts=[{"host": settings.OPENSEARCH_ENDPOINT, "port": 443}],
        http_auth=auth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
    )

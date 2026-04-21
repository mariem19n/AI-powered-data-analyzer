"""
Configuration centralisée — chargée depuis .env
Toutes les valeurs sont modifiables via le fichier .env
sans toucher au code.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict



class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore"
    )
    # PostgreSQL
    database_url: str = "postgresql+asyncpg://analyzer:analyzer_pwd@postgres:5432/analyzer_db"

    # Neo4j
    neo4j_uri: str = "bolt://neo4j:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "neo4j_secret"

    # Redis
    redis_url: str = "redis://redis:6379/0"

    # Cache TTL (en secondes) — modifiable via .env
    cache_ttl_transactional: int = 3600      # 1h — données transactionnelles (ventes du jour, etc.)
    cache_ttl_historical: int = 86400        # 24h — données historiques (CA trimestriel, etc.)
    cache_ttl_execution_plan: int = 86400    # 24h — plans d'exécution réutilisables
    cache_ttl_session: int = 7200            # 2h — sessions conversationnelles (inactivité)

    # Rate limiting
    rate_limit_per_minute: int = 30          # Requêtes max par utilisateur par minute

    # API
    secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expiration_minutes: int = 60



settings = Settings()
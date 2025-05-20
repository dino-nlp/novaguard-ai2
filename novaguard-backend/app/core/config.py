from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache

class Settings(BaseSettings):
    # Database settings
    DATABASE_URL: str = "postgresql://novaguard_user:novaguard_password@postgres_db:5432/novaguard_db"

    # JWT settings
    SECRET_KEY: str = "default_jwt_secret_needs_override_from_env" # Sẽ bị ghi đè bởi .env
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 # 1 day

    # Fernet Encryption Key for GitHub Tokens
    FERNET_ENCRYPTION_KEY: str | None = None # Sẽ load từ .env

    # GitHub OAuth App Settings
    GITHUB_CLIENT_ID: str | None = None
    GITHUB_CLIENT_SECRET: str | None = None
    GITHUB_REDIRECT_URI: str = "http://localhost:8000/api/auth/github/callback"
    
    # GitHub Webhook Secret (cho webhook của repository)
    GITHUB_WEBHOOK_SECRET: str | None = None # Sẽ load từ .env
    
    SESSION_SECRET_KEY: str | None = None

    # Kafka settings
    KAFKA_BOOTSTRAP_SERVERS: str = "kafka:29092"
    KAFKA_PR_ANALYSIS_TOPIC: str = "pr_analysis_tasks"

    # Ollama settings
    OLLAMA_BASE_URL: str = "http://ollama:11434" # Map tới 11435 trên host nếu dùng lựa chọn 2
    OLLAMA_DEFAULT_MODEL: str = "codellama:7b-instruct-q4_K_M"
    
    OPENAI_API_KEY: str | None = None
    GEMINI_API_KEY: str | None = None
    GOOGLE_API_KEY: str | None = None
    DEFAULT_LLM_PROVIDER: str | None = "ollama" #(ollama, openai, gemini)
    OPENAI_DEFAULT_MODEL: str | None = "gpt-3.5-turbo"
    GEMINI_DEFAULT_MODEL: str | None = "gemini-2.0-flash-exp"
    
    # Neo4j Settings
    NEO4J_URI: str = "neo4j://neo4j_db:7687" # Sử dụng neo4j:// cho driver v5+
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "your_default_neo4j_password" # Sẽ bị override bởi .env
    # NEO4J_AUTH: str | None = None

    
    
    NOVAGUARD_PUBLIC_URL: str | None = None # Ví dụ: https://abcdef123.ngrok.io hoặc https://novaguard.yourcompany.com
    
    DEBUG: bool = True

    # Pydantic-Settings V2 configuration to load from .env file
    model_config = SettingsConfigDict(
        env_file=".env", 
        env_file_encoding='utf-8', 
        extra='ignore' # Bỏ qua các biến không được định nghĩa trong Settings class
    )

@lru_cache()
def get_settings() -> Settings:
    return Settings()

settings = get_settings()

if __name__ == "__main__":
    print("Loaded Settings:")
    print(f"  DATABASE_URL: {settings.DATABASE_URL}")
    print(f"  SECRET_KEY: {'********' if settings.SECRET_KEY else 'Not Set'}") # Che secret key
    print(f"  ALGORITHM: {settings.ALGORITHM}")
    print(f"  ACCESS_TOKEN_EXPIRE_MINUTES: {settings.ACCESS_TOKEN_EXPIRE_MINUTES}")
    print(f"  FERNET_ENCRYPTION_KEY: {'********' if settings.FERNET_ENCRYPTION_KEY else 'Not Set'}")
    print(f"  GITHUB_CLIENT_ID: {settings.GITHUB_CLIENT_ID}")
    print(f"  GITHUB_CLIENT_SECRET: {'********' if settings.GITHUB_CLIENT_SECRET else 'Not Set'}")
    print(f"  GITHUB_REDIRECT_URI: {settings.GITHUB_REDIRECT_URI}")
    print(f"  GITHUB_WEBHOOK_SECRET: {'********' if settings.GITHUB_WEBHOOK_SECRET else 'Not Set'}")
    print(f"  KAFKA_BOOTSTRAP_SERVERS: {settings.KAFKA_BOOTSTRAP_SERVERS}")
    print(f"  KAFKA_PR_ANALYSIS_TOPIC: {settings.KAFKA_PR_ANALYSIS_TOPIC}")
    print(f"  OLLAMA_BASE_URL: {settings.OLLAMA_BASE_URL}")

    if settings.SECRET_KEY == "default_jwt_secret_needs_override_from_env":
        print("\nWARNING: Default JWT SECRET_KEY is used. Ensure it's set via .env or environment variable!")
    if not settings.FERNET_ENCRYPTION_KEY:
        print("\nWARNING: FERNET_ENCRYPTION_KEY is not set. GitHub token encryption/decryption will fail.")
        print("  Generate a key with: from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
    if not settings.GITHUB_CLIENT_ID or not settings.GITHUB_CLIENT_SECRET:
        print("\nWARNING: GitHub OAuth Client ID or Secret is not set. GitHub OAuth flow will fail.")
    if not settings.GITHUB_WEBHOOK_SECRET:
        print("\nWARNING: GITHUB_WEBHOOK_SECRET is not set. Webhook signature verification will be skipped or fail if enforced.")
        
    print(f"  NEO4J_URI: {settings.NEO4J_URI}")
    print(f"  NEO4J_USER: {settings.NEO4J_USER}")
    print(f"  NEO4J_PASSWORD: {'********' if settings.NEO4J_PASSWORD else 'Not Set'}")
    if settings.NEO4J_PASSWORD == "your_default_neo4j_password" or not settings.NEO4J_PASSWORD:
        print("\nWARNING: Default or empty NEO4J_PASSWORD is used. Ensure it's set via .env and matches docker-compose NEO4J_AUTH!")

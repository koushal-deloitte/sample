from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: str = "postgresql+asyncpg://user:password@localhost:5432/fallout_agent"

    # SQS
    aws_region: str = "us-east-1"
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    sqs_queue_url: str = ""
    sqs_poll_interval_seconds: int = 5
    sqs_max_messages: int = 10

    # EHAP
    ehap_base_url: str = "https://ehap.internal.example.com"
    ehap_api_key: str = ""
    ehap_model: str = "claude-sonnet-4-6"
    ehap_timeout_seconds: int = 60

    # Subscriber API
    subscriber_api_base_url: str = ""
    subscriber_api_key: str = ""

    # Agent
    max_tool_calls_per_run: int = 20
    dry_run: bool = False
    confidence_gate_enabled: bool = True

    # Self-learning
    unmatched_fallout_threshold: int = 5
    skill_effectiveness_min_rate: float = 0.5

    # Metrics
    metrics_sink: str = ""
    cloudwatch_namespace: str = "FalloutAgent"


settings = Settings()

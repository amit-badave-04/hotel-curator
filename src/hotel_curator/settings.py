"""Environment-based configuration via pydantic-settings."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="CURATOR_",
    )

    # --- Model ---
    model_dir: Path = Path("artifacts/hotel_curator_clf")
    max_seq_length: int = 256
    inference_batch_size: int = 64
    use_bf16: bool = True
    threshold_set: str = "val"  # "val" or "gold"

    # --- Attributes ---
    attributes: list[str] = [
        "cleanliness", "room", "staff", "location",
        "noise", "wifi", "value", "breakfast",
    ]
    critical_attributes: list[str] = [
        "cleanliness", "room", "staff",
    ]

    # --- Tier rules ---
    elite_min_avg: float = 0.215
    elite_min_critical: float = 0.30
    superior_min_avg: float = 0.190
    superior_min_critical: float = 0.20
    premium_min_avg: float = 0.165
    premium_min_critical: float = 0.10

    # --- Database ---
    database_url: str = "sqlite+aiosqlite:///./hotel_curator.db"

    # --- API ---
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_workers: int = 1

    # --- Bootstrap ---
    bootstrap_n: int = 1_000
    bootstrap_seed: int = 42


settings = Settings()

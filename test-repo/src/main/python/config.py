"""Application configuration module.""""

import os
from typing import Optional


class DatabaseConfig:
    """Database connection configuration."""

    def __init__(self, host: str = "localhost", port: int = 5432):
        self.host = host
        self.port = port

    def get_connection_string(self) -> str:
        """Build connection string."""
        return f"postgresql://{self.host}:{self.port}/mydb"

    @classmethod
    def from_env(cls) -> "DatabaseConfig":
        """Create config from environment variables."""
        return cls(
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", "5432")),
        )

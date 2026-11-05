"""Backend configuration loaded from environment variables."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    api_token: str
    db_path: str
    llm_base_url: str
    llm_model: str
    llm_api_key: str
    llm_timeout_s: float

    @classmethod
    def from_env(cls) -> "Config":
        """Build Config from LENG_* environment variables.

        LENG_LLM_MODEL and LENG_LLM_TIMEOUT_S have defaults; the rest
        are required and raise KeyError when missing.
        """
        return cls(
            api_token=os.environ["LENG_API_TOKEN"],
            db_path=os.environ["LENG_DB_PATH"],
            llm_base_url=os.environ["LENG_LLM_BASE_URL"],
            llm_model=os.environ.get("LENG_LLM_MODEL", "glm5.3-flash:claude"),
            llm_api_key=os.environ["LENG_LLM_API_KEY"],
            llm_timeout_s=float(os.environ.get("LENG_LLM_TIMEOUT_S", "10.0")),
        )

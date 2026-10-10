"""全局配置：从环境变量 / .env 加载运行参数。"""

from functools import lru_cache
from contextlib import contextmanager
from contextvars import ContextVar

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import AliasChoices, Field
from typing import Literal


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ---- LLM ----
    openai_api_key: str = Field(default='sk-xxxxxxxxxxxxxxxx', validation_alias=AliasChoices('LLM_API_KEY', 'OPENAI_API_KEY', 'openai_api_key'))
    openai_base_url: str = Field(default='https://api.openai.com/v1', validation_alias=AliasChoices('LLM_BASE_URL', 'OPENAI_BASE_URL', 'openai_base_url'))
    llm_model: str = "gpt-4o-mini"
    llm_temperature: float = 0.1
    llm_max_attempts: int = Field(default=3, ge=1, le=10)
    llm_retry_delay: float = Field(default=1.0, ge=0, le=30)
    llm_max_format_repairs: int = Field(default=1, ge=0, le=2)
    llm_structured_output_mode: Literal['prompt', 'json_object'] = 'prompt'
    embedding_model: str = "text-embedding-3-small"
    provider_config_path: str = 'data/provider.json'
    browser_backend: Literal['http', 'chromium'] = 'http'
    agent_max_steps: int = Field(default=12, ge=1, le=100)
    agent_max_children: int = Field(default=6, ge=0, le=20)
    agent_child_concurrency: int = Field(default=2, ge=1, le=4)
    subagent_max_steps: int = Field(default=8, ge=1, le=50)
    retrieval_read_limit: int = Field(default=3, ge=0, le=20)
    mcp_config_path: str = 'config/mcp.json'
    mcp_max_result_chars: int = Field(default=30000, ge=1000, le=200000)
    skill_dirs: list[str] = ['skills']
    skill_max_chars: int = Field(default=24000, ge=1000, le=100000)
    skill_context_chars: int = Field(default=48000, ge=1000, le=200000)
    workspace_allowed_roots: list[str] = []  # Empty means the server's cwd, not the whole disk.
    agent_memory_top_k: int = Field(default=5, ge=0, le=20)
    agent_memory_max_bytes: int = Field(default=12000, ge=1000, le=64000)
    context_input_limit: int = Field(default=12000, ge=1000, le=200000)
    execution_enabled: bool = False
    execution_root: str = 'data/execution'
    execution_helper_path: str = ''
    execution_rg_path: str = ''
    execution_timeout_seconds: int = Field(default=15, ge=1, le=15)
    execution_output_bytes: int = Field(default=16384, ge=1024, le=16384)
    workspace_allowed_roots: list[str] = []  # Empty means the server's cwd, not the whole disk.

    # ---- 多模型路由（为空则回退 llm_model）----
    planner_model: str = ""   # 规划/反思用强模型
    reviewer_model: str = ""  # 审核用强模型
    writer_model: str = ""    # 写作用强模型
    summary_model: str = ""   # 阅读/摘要用弱模型（省成本）

    # ---- Milvus ----
    milvus_host: str = "localhost"
    milvus_port: int = 19530
    milvus_collection: str = "research_papers"

    # ---- Redis ----
    redis_url: str = "redis://localhost:6379/0"

    # ---- 检索服务 ----
    s2_api_key: str = ""
    serpapi_api_key: str = ""
    tavily_api_key: str = ""

    # ---- 工作流参数 ----
    max_iterations: int = 3
    request_timeout: int = 60
    review_threshold: float = 0.7
    require_human_approval: bool = False  # 为 True 时 human_confirm 节点暂停等待人工审批
    database_url: str = "sqlite:///data/research.db"
    memory_markdown_dir: str = "data/memories"
    checkpoint_url: str = "sqlite:///data/checkpoints.db"
    task_deadline_seconds: int = 900
    max_model_calls: int = 100
    max_task_tokens: int = 200000
    max_output_tokens: int = 4096
    max_documents: int = 20
    worker_lease_seconds: int = 120
    worker_poll_seconds: float = 2.0

    # ---- LangSmith 可观测性 ----
    langsmith_api_key: str = ""
    langsmith_project: str = "research-agent"
    langsmith_tracing: bool = False
    langsmith_endpoint: str = 'https://api.smith.langchain.com'
    langsmith_workspace_id: str = ''

    # ---- 评测 ----
    eval_dataset_path: str = "data/eval_dataset.json"


@lru_cache
def _environment_settings() -> Settings:
    return Settings()


_model_overrides = ContextVar('model_overrides', default=None)


@contextmanager
def model_settings_scope(overrides):
    token = _model_overrides.set(overrides)
    try:
        yield
    finally:
        _model_overrides.reset(token)


def get_settings() -> Settings:
    # Read on every access so an independently running Worker observes API saves.
    from research_agent.models.provider_settings import read_overrides
    base = _environment_settings()
    overrides = _model_overrides.get()
    if overrides is None:
        overrides = read_overrides(base.provider_config_path)
    return base.model_copy(update=overrides) if overrides else base


get_settings.cache_clear = _environment_settings.cache_clear

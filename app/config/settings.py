from functools import lru_cache
import os

os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost,::1")
os.environ.setdefault("no_proxy", "127.0.0.1,localhost,::1")

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "Enterprise Business Agent Platform"
    app_env: str = "development"
    app_port: int = 8000
    app_debug: bool = True
    log_level: str = "DEBUG"

    # LLM (OpenAI 兼容接口)
    llm_provider: str = "ollama_native"
    llm_base_url: str = "http://127.0.0.1:11434"
    llm_model: str = "qwen2.5-1.5b:latest"
    llm_api_key: str = ""

    # Embedding
    embedding_provider: str = "ollama_native"
    embedding_base_url: str = "http://127.0.0.1:11434"
    embedding_model: str = "bge-m3:latest"
    embedding_api_key: str = ""

    # Ollama 原生兼容字段
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_llm_model: str = "qwen2.5-1.5b:latest"
    ollama_embedding_model: str = "bge-m3:latest"

    # Chroma 向量库
    chroma_mode: str = "embedded"
    chroma_host: str = "127.0.0.1"
    chroma_port: int = 8001
    chroma_persist_dir: str = "./data/chroma"
    chroma_collection: str = "knowledge_base"

    # RAG 参数
    chunk_size: int = 512
    chunk_overlap: int = 64
    top_k_retrieve: int = 20
    top_k_rerank: int = 5
    rrf_k: int = 60
    min_relevance_score: float = 0.55
    min_vector_score: float = 0.3

    # Redis
    redis_host: str = "127.0.0.1"
    redis_port: int = 6379
    redis_db: int = 0

    # MySQL
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3307
    mysql_user: str = "agent"
    mysql_password: str = "agent_password"
    mysql_database: str = "after_sales_agent"

    # OpenClaw 网关
    openclaw_enabled: bool = True
    openclaw_gateway_url: str = "http://127.0.0.1:18000"
    openclaw_api_key: str = "local-rag-key"
    # 是否强制校验网关调用方带的 API Key
    # /v1/* 与 /threads/* 是给 OpenClaw 调的，不能用 JWT 会话鉴权。
    # 开启后网关必须在 Provider 里配好与 OPENCLAW_API_KEY 一致的 Key，
    # 否则入站链路会收到 401。网关侧暂未配置时可临时设为 false。
    openclaw_require_key: bool = True
    openclaw_feishu_app_id: str = ""
    openclaw_feishu_app_secret: str = ""

    # HITL 超时（秒）
    hitl_timeout_seconds: int = 1800

    # 转人工：多久没人认领就算超时（秒），会被标记 timeout 并升级通知主管
    handoff_timeout_seconds: int = 300
    # 转人工后台巡检间隔（秒）
    handoff_patrol_interval: int = 60
    # AI 自动捕获线索：命中「线索意图」时是否自动建线索
    lead_auto_capture: bool = True

    # 缓存后端：sqlite / redis
    cache_backend: str = "sqlite"
    redis_url: str = "redis://127.0.0.1:6379/0"

    # 数据库模式：mysql / sqlite
    db_mode: str = "sqlite"

    # JWT
    jwt_secret: str = "CHANGE_ME_IN_ENV"
    jwt_expire_hours: int = 168

    llm_router_mode: str = "hybrid"


@lru_cache
def get_settings() -> Settings:
    return Settings()

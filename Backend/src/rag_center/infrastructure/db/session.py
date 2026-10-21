from pgvector.psycopg import register_vector_async
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from rag_center.core.config import Settings


def create_db_engine(settings: Settings) -> AsyncEngine:
    """创建异步数据库引擎；连接建立时注册 pgvector 类型。"""

    engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def register_pgvector(dbapi_connection, _connection_record) -> None:
        dbapi_connection.run_async(register_vector_async)

    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """创建会话工厂；业务事务由服务层显式提交或回滚。"""

    return async_sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        class_=AsyncSession,
    )

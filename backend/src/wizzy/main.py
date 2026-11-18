from contextlib import asynccontextmanager

from alembic.script import ScriptDirectory
from fastapi import FastAPI
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from wizzy.account_governance import router as account_governance_router
from wizzy.config import Settings, migration_config
from wizzy.corrections import router as corrections_router
from wizzy.daily_homework import router as daily_homework_router
from wizzy.errors import DomainError, install_errors
from wizzy.evaluation_consent import router as evaluation_consent_router
from wizzy.evaluation_datasets import router as evaluation_datasets_router
from wizzy.evaluation_runs import router as evaluation_runs_router
from wizzy.grading_prompts import router as grading_prompts_router
from wizzy.identity import router
from wizzy.learning_devices import router as learning_devices_router
from wizzy.media import router as media_router
from wizzy.mistake_bank import router as mistake_bank_router
from wizzy.mistakes import router as mistakes_router
from wizzy.model_catalog import router as model_catalog_router
from wizzy.model_configs import router as model_configs_router
from wizzy.passkeys import router as passkeys_router
from wizzy.platform_identity import router as platform_router
from wizzy.questions import router as questions_router
from wizzy.reports import router as reports_router
from wizzy.review_plans import router as review_plans_router
from wizzy.reviews import router as reviews_router
from wizzy.school_roster import router as school_roster_router
from wizzy.school_structure import router as school_structure_router
from wizzy.schools import router as schools_router
from wizzy.submissions import router as submissions_router
from wizzy.textbook_content import router as textbook_content_router
from wizzy.textbook_documents import router as textbook_documents_router
from wizzy.textbook_processing import router as textbook_processing_router
from wizzy.textbooks import router as textbooks_router
from wizzy.timetables import router as timetables_router


def create_app(settings: Settings | None = None):
    settings = settings or Settings()
    engine = create_engine(settings.db_url, pool_pre_ping=True, hide_parameters=True)
    expected_heads = set(ScriptDirectory.from_config(migration_config()).get_heads())

    @asynccontextmanager
    async def lifespan(app):
        with engine.connect() as connection:
            role = connection.execute(text("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")).one()
            owner = connection.execute(text("SELECT EXISTS(SELECT 1 FROM pg_tables WHERE schemaname='public' AND tableowner=current_user)")).scalar_one()
            if role.rolsuper or role.rolbypassrls or owner:
                raise RuntimeError("Runtime role must not own tables, be superuser or bypass RLS")
        settings.storage_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(title="小知 API", version="0.1.0", lifespan=lifespan)
    app.state.engine, app.state.settings = engine, settings
    install_errors(app)
    app.include_router(router)
    app.include_router(learning_devices_router)
    app.include_router(passkeys_router)
    app.include_router(media_router)
    app.include_router(daily_homework_router)
    app.include_router(submissions_router)
    app.include_router(questions_router)
    app.include_router(mistakes_router)
    app.include_router(mistake_bank_router)
    app.include_router(corrections_router)
    app.include_router(reviews_router)
    app.include_router(review_plans_router)
    app.include_router(reports_router)
    app.include_router(timetables_router)
    app.include_router(platform_router)
    app.include_router(account_governance_router)
    app.include_router(model_catalog_router)
    app.include_router(model_configs_router)
    app.include_router(grading_prompts_router)
    app.include_router(schools_router)
    app.include_router(textbooks_router)
    app.include_router(textbook_documents_router)
    app.include_router(textbook_processing_router)
    app.include_router(textbook_content_router)
    app.include_router(school_structure_router)
    app.include_router(school_roster_router)
    app.include_router(evaluation_consent_router)
    app.include_router(evaluation_datasets_router)
    app.include_router(evaluation_runs_router)

    @app.get("/health/live", tags=["health"])
    def live():
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"])
    def ready():
        try:
            with engine.connect() as connection:
                actual_heads = set(connection.execute(text("SELECT version_num FROM alembic_version")).scalars())
                if actual_heads != expected_heads:
                    raise DomainError(503, "schema_mismatch", "数据库结构版本不匹配，请先执行迁移", retryable=True)
        except SQLAlchemyError as exc:
            raise DomainError(503, "database_unavailable", "数据库尚未就绪", retryable=True) from exc
        return {"status": "ok", "ai_provider": "unconfigured"}

    return app

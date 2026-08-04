from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging
from logging.config import dictConfig
from app.api.apis import api_router
from app.core.config import settings
from agents import enable_verbose_stdout_logging

def configure_logging() -> None:
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "standard": {
                    "format": "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
                }
            },
            "handlers": {
                "default": {
                    "class": "logging.StreamHandler",
                    "formatter": "standard",
                }
            },
            "loggers": {
                "app": {
                    "handlers": ["default"],
                    "level": "INFO",
                    "propagate": False,
                },
                "": {
                    "handlers": ["default"],
                    "level": "INFO",
                },
            },
        }
    )


configure_logging()
logging.getLogger(__name__).info("Logging configured")

@asynccontextmanager
async def lifespan(app: FastAPI):

    yield

    logging.getLogger("app").info("Dọn dẹp tài nguyên hệ thống...")

app = FastAPI(
    title=settings.PROJECT_NAME,
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.API_V1_STR)
enable_verbose_stdout_logging()

@app.get("/health", tags=["Health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8001,
        reload=True
    )

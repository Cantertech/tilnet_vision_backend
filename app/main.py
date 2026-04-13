from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.api.routes import vision, auth

app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json"
)

# Set all middleware parameters
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify the frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Routers
app.include_router(vision.router, prefix=settings.API_V1_STR + "/vision", tags=["vision"])
app.include_router(auth.router, prefix=settings.API_V1_STR + "/auth", tags=["auth"])

@app.get("/")
async def root():
    return {"status": "alive", "service": settings.PROJECT_NAME}

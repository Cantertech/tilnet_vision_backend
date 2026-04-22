import asyncio
import httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.api.routes import vision, auth

async def keep_alive():
    """Background task to ping the server every 14 minutes to prevent sleep."""
    url = "https://tilnet-vision-backend.onrender.com"
    while True:
        await asyncio.sleep(840) # 14 minutes
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url)
                print(f"Heartbeat: {response.status_code}")
        except Exception as e:
            print(f"Heartbeat failed: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Run the keep_alive task in the background
    asyncio.create_task(keep_alive())
    yield

app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan
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

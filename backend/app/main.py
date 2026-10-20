"""Точка входа FastAPI: health, visual-analysis, подключение роутеров."""
from __future__ import annotations

import logging
import os

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials
from starlette.responses import JSONResponse

from .api.admin_products import router as admin_products_router
from .api.affiliate import router as affiliate_router
from .api.analytics import router as analytics_router
from .api.auth import router as auth_router
from .api.catalog_admin import router as catalog_admin_router
from .api.deps import auth_scheme, get_db
from .api.fit_profile import router as fit_profile_router
from .api.onboarding import router as onboarding_router
from .api.media_proxy import router as media_proxy_router
from .api.outfits import router as outfits_router
from .api.products import router as products_router
from .api.profile import router as profile_router
from .api.recommendations import router as recommendations_router
from .api.taste_profile import router as taste_profile_router
from .api.shopping_intents import router as shopping_intents_router
from .api.catalog_discovery import router as catalog_discovery_router
from .middleware import RateLimitMiddleware, RequestLogMiddleware
from .log_redaction import install_log_redaction
from .services.visual_analysis_service import generate_style_visual
from .services.photo_analysis_jobs import detached_visual_analysis
from .services.health_probe import database_probe_succeeds
from .config import style_visual_enabled

logging.basicConfig(
  level=logging.INFO,
  format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
install_log_redaction()

API_VERSION = "1.0.1-build15"
STYLE_VISUAL_DISABLED_MESSAGE = (
  'Визуализация стиля пока недоступна. Продолжите подбор вещей.'
)
app = FastAPI(title="Modish API", version=API_VERSION)

_cors_raw = (os.getenv("CORS_ORIGINS") or "*").strip()
_cors_origins = [o.strip() for o in _cors_raw.split(",") if o.strip()] or ["*"]
app.add_middleware(
  CORSMiddleware,
  allow_origins=_cors_origins,
  allow_credentials=True,
  allow_methods=["*"],
  allow_headers=["*"],
)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestLogMiddleware)

app.include_router(auth_router)
app.include_router(media_proxy_router)
app.include_router(products_router)
app.include_router(recommendations_router)
app.include_router(profile_router)
app.include_router(fit_profile_router)
app.include_router(onboarding_router)
app.include_router(taste_profile_router)
app.include_router(shopping_intents_router)
app.include_router(catalog_discovery_router)
app.include_router(outfits_router)
app.include_router(affiliate_router)
app.include_router(catalog_admin_router)
app.include_router(analytics_router)
app.include_router(admin_products_router)


@app.get("/health")
def health(request: Request, response: Response) -> dict[str, str]:
  """Legacy HTTP-200 envelope, including a degraded database response."""
  response.headers['Cache-Control'] = 'no-store'
  return _database_health(request)


def _database_health(request: Request) -> dict[str, str]:
  provider = request.app.dependency_overrides.get(get_db, get_db)
  ready = database_probe_succeeds(provider)
  return {'status': 'ok' if ready else 'degraded',
      'database': 'ok' if ready else 'error', 'version': API_VERSION}


@app.get('/health/live')
def liveness(response: Response) -> dict[str, str]:
  """Process responsiveness only: no database dependency or authentication."""
  response.headers['Cache-Control'] = 'no-store'
  return {'status': 'ok', 'version': API_VERSION}


@app.get('/health/ready')
def readiness(request: Request):
  result = _database_health(request)
  return JSONResponse(status_code=200 if result['status'] == 'ok' else 503,
      content=result, headers={'Cache-Control': 'no-store'})


@app.post("/visual-analysis")
async def visual_analysis(
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, str]:
  # This optional provider has no durable per-user cost reservation yet. Keep
  # it off unless deployment explicitly accepts that budget exposure.
  if not style_visual_enabled():
    raise HTTPException(status_code=503, detail=STYLE_VISUAL_DISABLED_MESSAGE,
                        headers={'Cache-Control': 'no-store'})
  analysis = await detached_visual_analysis(credentials)
  try:
    image_url = await generate_style_visual(analysis)
  except RuntimeError as e:
    raise HTTPException(status_code=502, detail="Не удалось создать визуализацию. Попробуйте ещё раз.") from e
  return {"image_url": image_url, "type": "style_visual"}

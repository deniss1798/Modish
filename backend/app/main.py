"""Точка входа FastAPI: health, visual-analysis, подключение роутеров."""
from __future__ import annotations

import logging
import os

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import text
from sqlalchemy.orm import Session

from .api.admin_products import router as admin_products_router
from .api.affiliate import router as affiliate_router
from .api.analytics import router as analytics_router
from .api.auth import router as auth_router
from .api.catalog_admin import router as catalog_admin_router
from .api.deps import auth_scheme, get_db, user_from_token
from .api.fit_profile import router as fit_profile_router
from .api.onboarding import router as onboarding_router
from .api.media_proxy import router as media_proxy_router
from .api.outfits import router as outfits_router
from .api.products import router as products_router
from .api.profile import router as profile_router
from .api.recommendations import router as recommendations_router
from .api.taste_profile import router as taste_profile_router
from .middleware import RateLimitMiddleware, RequestLogMiddleware
from .services.recommendation_engine import ensure_style_profile
from .services.visual_analysis_service import generate_style_visual

logging.basicConfig(
  level=logging.INFO,
  format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

app = FastAPI(title="Modish API", version="1.0.0-alpha")

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
app.include_router(outfits_router)
app.include_router(affiliate_router)
app.include_router(catalog_admin_router)
app.include_router(analytics_router)
app.include_router(admin_products_router)


@app.get("/health")
def health(db: Session = Depends(get_db)) -> dict[str, str]:
  try:
    db.execute(text("SELECT 1"))
    db_ok = "ok"
  except Exception:
    db_ok = "error"
  status = "ok" if db_ok == "ok" else "degraded"
  return {"status": status, "database": db_ok, "version": "1.0.0-alpha"}


@app.post("/visual-analysis")
async def visual_analysis(
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, str]:
  user = user_from_token(credentials, db)
  profile = ensure_style_profile(db, user.id)
  analysis = profile.profile_json.get("analysis") if isinstance(profile.profile_json, dict) else None
  if not isinstance(analysis, dict) or not analysis:
    raise HTTPException(status_code=400, detail="Сначала выполните анализ стиля по фото.")
  try:
    image_url = await generate_style_visual(analysis)
  except RuntimeError as e:
    raise HTTPException(status_code=502, detail=str(e)) from e
  return {"image_url": image_url, "type": "style_visual"}

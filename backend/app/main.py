"""Точка входа FastAPI: health, visual-analysis, подключение роутеров."""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.orm import Session

from .api.admin_products import router as admin_products_router
from .api.affiliate import router as affiliate_router
from .api.auth import router as auth_router
from .api.catalog_admin import router as catalog_admin_router
from .api.deps import auth_scheme, get_db, user_from_token
from .api.fit_profile import router as fit_profile_router
from .api.media_proxy import router as media_proxy_router
from .api.outfits import router as outfits_router
from .api.products import router as products_router
from .api.profile import router as profile_router
from .api.recommendations import router as recommendations_router
from .api.taste_profile import router as taste_profile_router
from .models import StyleProfile
from .services.visual_analysis_service import generate_style_visual

app = FastAPI(title="Modish API", version="0.9.0-pre")

app.include_router(auth_router)
app.include_router(media_proxy_router)
app.include_router(products_router)
app.include_router(recommendations_router)
app.include_router(profile_router)
app.include_router(fit_profile_router)
app.include_router(taste_profile_router)
app.include_router(outfits_router)
app.include_router(affiliate_router)
app.include_router(catalog_admin_router)
app.include_router(admin_products_router)


@app.get("/health")
def health() -> dict[str, str]:
  return {"status": "ok"}


@app.post("/visual-analysis")
async def visual_analysis(
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, str]:
  user = user_from_token(credentials, db)
  profile = db.execute(select(StyleProfile).where(StyleProfile.user_id == user.id)).scalar_one()
  analysis = profile.profile_json.get("analysis") if isinstance(profile.profile_json, dict) else None
  if not isinstance(analysis, dict) or not analysis:
    raise HTTPException(status_code=400, detail="Сначала выполните анализ стиля по фото.")
  try:
    image_url = await generate_style_visual(analysis)
  except RuntimeError as e:
    raise HTTPException(status_code=502, detail=str(e)) from e
  return {"image_url": image_url, "type": "style_visual"}

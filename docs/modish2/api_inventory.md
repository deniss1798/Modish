# API и модели на базовом HEAD

Декларации маршрутов из исходников; наличие декларации не доказывает корректный routing или доступность endpoint. Порядок статического /products/recommended отдельно отмечен в F16.

| Файл | Метод | Путь |
|---|---|---|
| `backend/app/api/admin_products.py` | POST | `/import` |
| `backend/app/api/affiliate.py` | POST | `/affiliate/click/{product_id}` |
| `backend/app/api/analytics.py` | GET | `/analytics/summary` |
| `backend/app/api/auth.py` | POST | `/auth/register` |
| `backend/app/api/auth.py` | POST | `/auth/login` |
| `backend/app/api/catalog_admin.py` | GET | `/sources` |
| `backend/app/api/catalog_admin.py` | POST | `/sources` |
| `backend/app/api/catalog_admin.py` | PATCH | `/sources/{source_id}` |
| `backend/app/api/catalog_admin.py` | POST | `/sources/{source_id}/sync` |
| `backend/app/api/catalog_admin.py` | POST | `/sources/{source_id}/sync-upload` |
| `backend/app/api/catalog_admin.py` | GET | `/sources/{source_id}/rules` |
| `backend/app/api/catalog_admin.py` | POST | `/sources/{source_id}/rules` |
| `backend/app/api/catalog_admin.py` | PATCH | `/rules/{rule_id}` |
| `backend/app/api/catalog_admin.py` | DELETE | `/rules/{rule_id}` |
| `backend/app/api/catalog_admin.py` | GET | `/sync-runs` |
| `backend/app/api/catalog_admin.py` | GET | `/sync-runs/{run_id}` |
| `backend/app/api/catalog_admin.py` | GET | `/products/stats` |
| `backend/app/api/catalog_admin.py` | POST | `/demo-seed` |
| `backend/app/api/catalog_admin.py` | POST | `/demo-rewrite-image-urls` |
| `backend/app/api/catalog_admin.py` | POST | `/sources/{source_id}/sync-mock-lamoda` |
| `backend/app/api/catalog_admin.py` | POST | `/renormalize` |
| `backend/app/api/catalog_admin.py` | POST | `/bootstrap-admitad-csv` |
| `backend/app/api/catalog_admin.py` | POST | `/alpha-bootstrap` |
| `backend/app/api/fit_profile.py` | GET | `/fit-profile/me` |
| `backend/app/api/fit_profile.py` | PATCH | `/fit-profile/me` |
| `backend/app/api/media_proxy.py` | GET | `/proxy-image` |
| `backend/app/api/onboarding.py` | GET | `/status` |
| `backend/app/api/onboarding.py` | PATCH | `/step1` |
| `backend/app/api/onboarding.py` | POST | `/photo` |
| `backend/app/api/onboarding.py` | PATCH | `/photo/confirm` |
| `backend/app/api/onboarding.py` | PATCH | `/step3` |
| `backend/app/api/onboarding.py` | POST | `/complete` |
| `backend/app/api/outfits.py` | POST | `/outfits/generate` |
| `backend/app/api/outfits.py` | GET | `/outfits` |
| `backend/app/api/outfits.py` | POST | `/outfits/save` |
| `backend/app/api/outfits.py` | POST | `/outfits/unsave` |
| `backend/app/api/products.py` | GET | `/products` |
| `backend/app/api/products.py` | GET | `/products/brands` |
| `backend/app/api/products.py` | GET | `/products/{product_id}` |
| `backend/app/api/products.py` | GET | `/products/recommended` |
| `backend/app/api/products.py` | GET | `/feed` |
| `backend/app/api/profile.py` | GET | `/users/me` |
| `backend/app/api/profile.py` | GET | `/profile/brief` |
| `backend/app/api/profile.py` | POST | `/metrics/events` |
| `backend/app/api/profile.py` | GET | `/saved-products` |
| `backend/app/api/profile.py` | PATCH | `/users/me/preferences` |
| `backend/app/api/profile.py` | GET | `/billing/status` |
| `backend/app/api/profile.py` | GET | `/style-profile/me` |
| `backend/app/api/profile.py` | PATCH | `/style-profile/me` |
| `backend/app/api/profile.py` | PATCH | `/style-profile/target` |
| `backend/app/api/profile.py` | POST | `/style-profile/analyze` |
| `backend/app/api/recommendations.py` | POST | `/recommendations/events` |
| `backend/app/api/recommendations.py` | POST | `/recommendations/generate` |
| `backend/app/api/recommendations.py` | GET | `/recommendations/feed` |
| `backend/app/api/recommendations.py` | GET | `/recommendations/outfits-legacy` |
| `backend/app/api/recommendations.py` | GET | `/recommendations/saved` |
| `backend/app/api/recommendations.py` | GET | `/recommendations/summary` |
| `backend/app/api/recommendations.py` | POST | `/recommendations/summary/rebuild` |
| `backend/app/api/recommendations.py` | GET | `/recommendations/{recommendation_id}` |
| `backend/app/api/recommendations.py` | POST | `/recommendations/{recommendation_id}/feedback` |
| `backend/app/api/taste_profile.py` | GET | `/taste-profile/me` |
| `backend/app/api/taste_profile.py` | PATCH | `/taste-profile/me` |

Префиксы APIRouter применяются дополнительно; таблица показывает аргументы декораторов.

## ORM-модели

- `User` — `backend/app/models.py:11`
- `StyleProfile` — `backend/app/models.py:29`
- `Recommendation` — `backend/app/models.py:51`
- `RecommendationEvent` — `backend/app/models.py:65`
- `SavedRecommendation` — `backend/app/models.py:75`
- `UserPreference` — `backend/app/models.py:84`
- `UserRecommendationSummary` — `backend/app/models.py:97`
- `UserLimits` — `backend/app/models.py:108`
- `ProductSource` — `backend/app/models.py:120`
- `CatalogSyncRun` — `backend/app/models.py:136`
- `AffiliateClick` — `backend/app/models.py:153`
- `SourceRule` — `backend/app/models.py:166`
- `Product` — `backend/app/models.py:177`
- `ProductEmbedding` — `backend/app/models.py:233`
- `TasteProfile` — `backend/app/models.py:246`
- `UserTasteFeature` — `backend/app/models.py:272`
- `RecommendationEventV2` — `backend/app/models.py:293`
- `FitProfile` — `backend/app/models.py:305`
- `RecommendationCandidatePool` — `backend/app/models.py:330`
- `UserRecommendationCache` — `backend/app/models.py:340`
- `Outfit` — `backend/app/models.py:350`
- `MetricEvent` — `backend/app/models.py:364`
- `ProductImpression` — `backend/app/models.py:373`
- `UserProductState` — `backend/app/models.py:388`

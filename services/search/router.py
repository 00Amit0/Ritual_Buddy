"""
services/search/router.py
Pandit search using Elasticsearch: geo_distance, multi-filter,
full-text on name/bio, sorting by distance/rating/price.
Falls back to PostgreSQL PostGIS if Elasticsearch unavailable.
"""

import json
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, cast, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.types import String

from config.database import get_db
from config.redis_client import RedisCache, get_redis
from config.settings import settings
from shared.models.models import PanditProfile, Pooja, User, VerificationStatus
from shared.schemas.schemas import PanditProfileResponse, PanditSearchResponse

router = APIRouter(prefix="/search", tags=["Search"])


# ── Elasticsearch Client ──────────────────────────────────────

async def get_es_client():
    """Lazy Elasticsearch client. Returns None if not configured."""
    try:
        from elasticsearch import AsyncElasticsearch
        client = AsyncElasticsearch(
            settings.ELASTICSEARCH_URL,
            basic_auth=(
                settings.ELASTICSEARCH_USERNAME or "elastic",
                settings.ELASTICSEARCH_PASSWORD or "",
            ) if settings.ELASTICSEARCH_PASSWORD else None,
        )
        return client
    except Exception:
        return None


# ── Elasticsearch Index Mapping ───────────────────────────────

PANDIT_INDEX_MAPPING = {
    "mappings": {
        "properties": {
            "id": {"type": "keyword"},
            "user_id": {"type": "keyword"},
            "name": {"type": "text", "analyzer": "standard"},
            "bio": {"type": "text", "analyzer": "standard"},
            "languages": {"type": "keyword"},
            "poojas_offered": {"type": "keyword"},
            "location": {"type": "geo_point"},
            "city": {"type": "keyword"},
            "state": {"type": "keyword"},
            "rating_avg": {"type": "float"},
            "rating_count": {"type": "integer"},
            "experience_years": {"type": "integer"},
            "base_fee": {"type": "float"},
            "service_radius_km": {"type": "float"},
            "is_available": {"type": "boolean"},
            "verification_status": {"type": "keyword"},
            "avatar_url": {"type": "keyword", "index": False},
        }
    },
    "settings": {
        "number_of_shards": 2,
        "number_of_replicas": 1,
    },
}

POOJA_INDEX_MAPPING = {
    "mappings": {
        "properties": {
            "id": {"type": "keyword"},
            "name_en": {
                "type": "text",
                "fields": {"keyword": {"type": "keyword"}},
            },
            "name_hi": {
                "type": "text",
                "fields": {"keyword": {"type": "keyword"}},
            },
            "slug": {"type": "keyword"},
            "category": {"type": "keyword"},
            "description_en": {"type": "text"},
            "description_hi": {"type": "text"},
            "avg_duration_hrs": {"type": "float"},
            "image_url": {"type": "keyword", "index": False},
            "is_active": {"type": "boolean"},
        }
    },
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 1,
    },
}


async def ensure_pandit_index(es_client) -> None:
    """Create Elasticsearch index if it doesn't exist."""
    exists = await es_client.indices.exists(index=settings.ELASTICSEARCH_INDEX_PANDITS)
    if not exists:
        await es_client.indices.create(
            index=settings.ELASTICSEARCH_INDEX_PANDITS,
            body=PANDIT_INDEX_MAPPING,
        )


async def ensure_pooja_index(es_client) -> None:
    """Create pooja index if it doesn't exist."""
    exists = await es_client.indices.exists(index=settings.ELASTICSEARCH_INDEX_POOJAS)
    if not exists:
        await es_client.indices.create(
            index=settings.ELASTICSEARCH_INDEX_POOJAS,
            body=POOJA_INDEX_MAPPING,
        )


async def ensure_search_indices(es_client) -> None:
    """Create all Elasticsearch indices required by the app."""
    await ensure_pandit_index(es_client)
    await ensure_pooja_index(es_client)


async def _get_pandit_coordinates(db: AsyncSession, pandit: PanditProfile) -> tuple[Optional[float], Optional[float]]:
    if pandit.location is None:
        return None, None

    geo_json = await db.scalar(select(func.ST_AsGeoJSON(pandit.location)))
    if not geo_json:
        return None, None

    coordinates = json.loads(geo_json).get("coordinates", [])
    if len(coordinates) != 2:
        return None, None

    longitude, latitude = coordinates
    return latitude, longitude


async def index_pandit(
    es_client,
    pandit: PanditProfile,
    user: User,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
) -> None:
    """Index or update a pandit document in Elasticsearch."""
    doc = {
        "id": str(pandit.id),
        "user_id": str(pandit.user_id),
        "name": user.name,
        "bio": pandit.bio or "",
        "languages": pandit.languages or [],
        "poojas_offered": [str(p) for p in (pandit.poojas_offered or [])],
        "city": pandit.city,
        "state": pandit.state,
        "rating_avg": float(pandit.rating_avg),
        "rating_count": pandit.rating_count,
        "experience_years": pandit.experience_years,
        "base_fee": float(pandit.base_fee),
        "service_radius_km": float(pandit.service_radius_km),
        "is_available": pandit.is_available,
        "verification_status": pandit.verification_status.value,
        "avatar_url": user.avatar_url,
    }

    if latitude is not None and longitude is not None:
        doc["location"] = {"lat": latitude, "lon": longitude}

    await es_client.index(
        index=settings.ELASTICSEARCH_INDEX_PANDITS,
        id=str(pandit.id),
        body=doc,
    )


async def delete_pandit(es_client, pandit_id: UUID) -> None:
    """Remove a pandit document from Elasticsearch if it exists."""
    await es_client.delete(
        index=settings.ELASTICSEARCH_INDEX_PANDITS,
        id=str(pandit_id),
    )


async def index_pooja(es_client, pooja: Pooja) -> None:
    """Index or update a pooja document in Elasticsearch."""
    await es_client.index(
        index=settings.ELASTICSEARCH_INDEX_POOJAS,
        id=str(pooja.id),
        body={
            "id": str(pooja.id),
            "name_en": pooja.name_en,
            "name_hi": pooja.name_hi,
            "slug": pooja.slug,
            "category": pooja.category.value,
            "description_en": pooja.description_en or "",
            "description_hi": pooja.description_hi or "",
            "avg_duration_hrs": float(pooja.avg_duration_hrs),
            "image_url": pooja.image_url,
            "is_active": pooja.is_active,
        },
    )


async def delete_pooja(es_client, pooja_id: UUID) -> None:
    """Remove a pooja document from Elasticsearch if it exists."""
    try:
        await es_client.delete(
            index=settings.ELASTICSEARCH_INDEX_POOJAS,
            id=str(pooja_id),
        )
    except Exception:
        pass


async def sync_all_pandits_to_index(db: AsyncSession, es_client) -> int:
    """Backfill all verified pandits into Elasticsearch."""
    result = await db.execute(
        select(PanditProfile, User)
        .join(User, User.id == PanditProfile.user_id)
        .where(PanditProfile.verification_status == VerificationStatus.VERIFIED)
    )
    rows = result.all()

    count = 0
    for pandit, user in rows:
        latitude, longitude = await _get_pandit_coordinates(db, pandit)
        await index_pandit(
            es_client,
            pandit,
            user,
            latitude=latitude,
            longitude=longitude,
        )
        count += 1

    await es_client.indices.refresh(index=settings.ELASTICSEARCH_INDEX_PANDITS)
    return count


async def sync_all_poojas_to_index(db: AsyncSession, es_client) -> int:
    """Backfill all active poojas into Elasticsearch."""
    result = await db.execute(select(Pooja).where(Pooja.is_active == True))
    poojas = result.scalars().all()

    count = 0
    for pooja in poojas:
        await index_pooja(es_client, pooja)
        count += 1

    await es_client.indices.refresh(index=settings.ELASTICSEARCH_INDEX_POOJAS)
    return count


async def bootstrap_search_indices(db: AsyncSession, es_client) -> dict:
    """Create required indices and backfill current DB state."""
    await ensure_search_indices(es_client)
    pandits_indexed = await sync_all_pandits_to_index(db, es_client)
    poojas_indexed = await sync_all_poojas_to_index(db, es_client)
    return {
        "pandits_indexed": pandits_indexed,
        "poojas_indexed": poojas_indexed,
        "pandit_index": settings.ELASTICSEARCH_INDEX_PANDITS,
        "pooja_index": settings.ELASTICSEARCH_INDEX_POOJAS,
    }


# ── Main Search Endpoints ─────────────────────────────────────

@router.get("/pandits", response_model=PanditSearchResponse)
async def search_pandits(
    # Required: User's current location
    lat: float = Query(..., ge=-90, le=90, description="User latitude"),
    lng: float = Query(..., ge=-180, le=180, description="User longitude"),
    radius_km: float = Query(default=25.0, ge=1, le=500, description="Search radius in km"),
    # Optional filters
    pooja_id: Optional[str] = Query(None, description="Filter by pooja type UUID"),
    languages: Optional[str] = Query(None, description="Comma-separated languages, e.g. 'Hindi,Sanskrit'"),
    experience_min: Optional[int] = Query(None, ge=0),
    experience_max: Optional[int] = Query(None, ge=0),
    price_min: Optional[float] = Query(None, ge=0),
    price_max: Optional[float] = Query(None, ge=0),
    available_date: Optional[str] = Query(None, description="YYYY-MM-DD — filter by availability"),
    q: Optional[str] = Query(None, description="Text search on name/bio"),
    sort_by: str = Query(default="distance", pattern="^(distance|rating|price|experience)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    """
    Search pandits by geo-proximity with optional filters.
    Tries Elasticsearch first; falls back to PostGIS if ES unavailable.
    """
    lang_list = [l.strip() for l in languages.split(",")] if languages else None

    es_client = await get_es_client()
    if es_client:
        try:
            return await _search_elasticsearch(
                es_client, lat, lng, radius_km, pooja_id, lang_list,
                experience_min, experience_max, price_min, price_max,
                available_date, q, sort_by, page, page_size, db, redis,
            )
        except Exception as e:
            # Graceful degradation to PostgreSQL
            pass

    # PostgreSQL PostGIS fallback
    return await _search_postgis(
        db, lat, lng, radius_km, pooja_id, lang_list,
        experience_min, experience_max, price_min, price_max,
        available_date, sort_by, page, page_size, redis,
    )


async def _search_elasticsearch(
    es_client,
    lat: float, lng: float, radius_km: float,
    pooja_id: Optional[str], languages: Optional[List[str]],
    experience_min: Optional[int], experience_max: Optional[int],
    price_min: Optional[float], price_max: Optional[float],
    available_date: Optional[str], q: Optional[str],
    sort_by: str, page: int, page_size: int,
    db: AsyncSession, redis,
) -> PanditSearchResponse:
    """Build and execute Elasticsearch query with geo_distance + filters."""

    # Build filter clauses
    filters = [
        {"term": {"verification_status": "VERIFIED"}},
        {"term": {"is_available": True}},
        {
            "geo_distance": {
                "distance": f"{radius_km}km",
                "location": {"lat": lat, "lon": lng},
            }
        },
    ]

    if pooja_id:
        filters.append({"term": {"poojas_offered": pooja_id}})

    if languages:
        filters.append({"terms": {"languages": languages}})

    if experience_min is not None or experience_max is not None:
        exp_range = {}
        if experience_min is not None:
            exp_range["gte"] = experience_min
        if experience_max is not None:
            exp_range["lte"] = experience_max
        filters.append({"range": {"experience_years": exp_range}})

    if price_min is not None or price_max is not None:
        price_range = {}
        if price_min is not None:
            price_range["gte"] = price_min
        if price_max is not None:
            price_range["lte"] = price_max
        filters.append({"range": {"base_fee": price_range}})

    # Full-text query
    must_clauses = []
    if q:
        must_clauses.append({
            "multi_match": {
                "query": q,
                "fields": ["name^2", "bio"],
                "fuzziness": "AUTO",
            }
        })

    query_body = {
        "bool": {
            "must": must_clauses or [{"match_all": {}}],
            "filter": filters,
        }
    }

    # Sorting
    sort_options = {
        "distance": [{"_geo_distance": {"location": {"lat": lat, "lon": lng}, "order": "asc", "unit": "km"}}],
        "rating": [{"rating_avg": {"order": "desc"}}],
        "price": [{"base_fee": {"order": "asc"}}],
        "experience": [{"experience_years": {"order": "desc"}}],
    }

    response = await es_client.search(
        index=settings.ELASTICSEARCH_INDEX_PANDITS,
        body={
            "query": query_body,
            "sort": sort_options.get(sort_by, sort_options["distance"]),
            "from": (page - 1) * page_size,
            "size": page_size,
        },
    )

    total = response["hits"]["total"]["value"]
    hits = response["hits"]["hits"]

    items = []
    for hit in hits:
        src = hit["_source"]
        distance = None
        if hit.get("sort") and sort_by == "distance":
            distance = round(hit["sort"][0], 2)

        items.append(PanditProfileResponse(
            id=src["id"],
            user_id=src["user_id"],
            bio=src.get("bio"),
            experience_years=src.get("experience_years", 0),
            languages=src.get("languages", []),
            poojas_offered=src.get("poojas_offered", []),
            service_radius_km=src.get("service_radius_km", 25.0),
            city=src.get("city"),
            state=src.get("state"),
            base_fee=src.get("base_fee", 500),
            rating_avg=src.get("rating_avg", 0),
            rating_count=src.get("rating_count", 0),
            verification_status=src.get("verification_status"),
            is_available=src.get("is_available", True),
            name=src.get("name"),
            avatar_url=src.get("avatar_url"),
            distance_km=distance,
        ))

    return PanditSearchResponse(items=items, total=total, page=page, page_size=page_size)


async def _search_postgis(
    db: AsyncSession,
    lat: float, lng: float, radius_km: float,
    pooja_id: Optional[str], languages: Optional[List[str]],
    experience_min: Optional[int], experience_max: Optional[int],
    price_min: Optional[float], price_max: Optional[float],
    available_date: Optional[str],
    sort_by: str, page: int, page_size: int, redis,
) -> PanditSearchResponse:
    """
    PostGIS-powered geo search fallback.
    Uses ST_DWithin for efficient radius queries with GiST index.
    """
    # User location as PostGIS geography
    user_point = func.ST_SetSRID(func.ST_MakePoint(lng, lat), 4326)

    # Distance expression (meters → km)
    distance_expr = (
        func.ST_Distance(
            cast(PanditProfile.location, type_=func.ST_Geography.__class__),
            cast(user_point, type_=func.ST_Geography.__class__),
        ) / 1000
    ).label("distance_km")

    # Base query
    query = (
        select(PanditProfile, User, text(
            f"ST_Distance(pandit_profiles.location::geography, "
            f"ST_SetSRID(ST_MakePoint({lng}, {lat}), 4326)::geography) / 1000 AS distance_km"
        ))
        .join(User, User.id == PanditProfile.user_id)
        .where(
            PanditProfile.verification_status == VerificationStatus.VERIFIED,
            PanditProfile.is_available == True,
            PanditProfile.location != None,
            text(
                f"ST_DWithin("
                f"pandit_profiles.location::geography, "
                f"ST_SetSRID(ST_MakePoint({lng}, {lat}), 4326)::geography, "
                f"{radius_km * 1000}"
                f")"
            ),
        )
    )

    # Apply filters
    if experience_min is not None:
        query = query.where(PanditProfile.experience_years >= experience_min)
    if experience_max is not None:
        query = query.where(PanditProfile.experience_years <= experience_max)
    if price_min is not None:
        query = query.where(PanditProfile.base_fee >= price_min)
    if price_max is not None:
        query = query.where(PanditProfile.base_fee <= price_max)
    if languages:
        query = query.where(PanditProfile.languages.overlap(languages))
    if pooja_id:
        query = query.where(
            PanditProfile.poojas_offered.any(pooja_id)
        )

    # Apply availability filter
    if available_date:
        from shared.models.models import PanditAvailability
        from datetime import datetime as dt
        try:
            target = dt.strptime(available_date, "%Y-%m-%d")
            query = query.where(
                PanditProfile.id.in_(
                    select(PanditAvailability.pandit_id).where(
                        func.date(PanditAvailability.date) == target.date(),
                        PanditAvailability.is_booked == False,
                        PanditAvailability.is_blocked == False,
                    )
                )
            )
        except ValueError:
            pass

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total = await db.scalar(count_query) or 0

    # Sorting
    sort_map = {
        "distance": text("distance_km ASC"),
        "rating": PanditProfile.rating_avg.desc(),
        "price": PanditProfile.base_fee.asc(),
        "experience": PanditProfile.experience_years.desc(),
    }
    query = query.order_by(sort_map.get(sort_by, text("distance_km ASC")))
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    rows = result.all()

    items = []
    for row in rows:
        pandit, user = row[0], row[1]
        distance = float(row[2]) if len(row) > 2 and row[2] else None

        items.append(PanditProfileResponse(
            id=pandit.id,
            user_id=pandit.user_id,
            bio=pandit.bio,
            experience_years=pandit.experience_years,
            languages=pandit.languages or [],
            poojas_offered=pandit.poojas_offered or [],
            service_radius_km=float(pandit.service_radius_km),
            city=pandit.city,
            state=pandit.state,
            base_fee=pandit.base_fee,
            rating_avg=pandit.rating_avg,
            rating_count=pandit.rating_count,
            verification_status=pandit.verification_status.value,
            is_available=pandit.is_available,
            name=user.name,
            avatar_url=user.avatar_url,
            distance_km=distance,
        ))

    return PanditSearchResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/poojas", response_model=List[dict])
async def search_poojas(
    q: Optional[str] = Query(None, description="Search text"),
    category: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Search available pooja types. Cached results."""
    es_client = await get_es_client()
    if es_client:
        try:
            must_clauses = []
            filters = [{"term": {"is_active": True}}]

            if q:
                must_clauses.append({
                    "multi_match": {
                        "query": q,
                        "fields": ["name_en^3", "name_hi^3", "slug^2", "description_en", "description_hi"],
                        "fuzziness": "AUTO",
                    }
                })
            if category:
                filters.append({"term": {"category": category}})

            response = await es_client.search(
                index=settings.ELASTICSEARCH_INDEX_POOJAS,
                body={
                    "query": {
                        "bool": {
                            "must": must_clauses or [{"match_all": {}}],
                            "filter": filters,
                        }
                    },
                    "sort": [{"name_en.keyword": {"order": "asc"}}],
                    "size": 100,
                },
            )

            return [hit["_source"] for hit in response["hits"]["hits"]]
        except Exception:
            pass
        finally:
            await es_client.close()

    query = select(Pooja).where(Pooja.is_active == True)

    if q:
        search_term = f"%{q}%"
        query = query.where(
            or_(
                Pooja.name_en.ilike(search_term),
                Pooja.name_hi.ilike(search_term),
            )
        )
    if category:
        query = query.where(Pooja.category == category)

    result = await db.execute(query.order_by(Pooja.name_en))
    poojas = result.scalars().all()

    return [
        {
            "id": str(p.id),
            "name_en": p.name_en,
            "name_hi": p.name_hi,
            "slug": p.slug,
            "category": p.category.value,
            "avg_duration_hrs": float(p.avg_duration_hrs),
            "image_url": p.image_url,
        }
        for p in poojas
    ]


@router.get("/pandits/suggestions")
async def pandit_suggestions(
    q: str = Query(..., min_length=2, description="Autocomplete query"),
    lat: Optional[float] = Query(None, ge=-90, le=90),
    lng: Optional[float] = Query(None, ge=-180, le=180),
    db: AsyncSession = Depends(get_db),
):
    """Fast autocomplete for pandit names. Returns top 5 matches."""
    query = (
        select(PanditProfile, User)
        .join(User, User.id == PanditProfile.user_id)
        .where(
            PanditProfile.verification_status == VerificationStatus.VERIFIED,
            User.name.ilike(f"%{q}%"),
        )
        .limit(5)
    )

    result = await db.execute(query)
    rows = result.all()

    return [
        {
            "id": str(row[0].id),
            "name": row[1].name,
            "city": row[0].city,
            "avatar_url": row[1].avatar_url,
            "rating_avg": float(row[0].rating_avg),
        }
        for row in rows
    ]

"""
services/search/router.py
Pandit search using Elasticsearch with a local projected-SQL fallback.
"""

from math import asin, cos, radians, sin, sqrt
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config.database import get_db
from config.redis_client import get_redis
from config.settings import settings
from shared.models.models import Pooja, SearchPanditAvailabilityProjection, SearchPanditProjection
from shared.schemas.schemas import PanditProfileResponse, PanditSearchResponse

router = APIRouter(prefix="/search", tags=["Search"])


async def get_es_client():
    """Lazy Elasticsearch client. Returns None if not configured."""
    try:
        from elasticsearch import AsyncElasticsearch

        client = AsyncElasticsearch(
            settings.ELASTICSEARCH_URL,
            basic_auth=(
                settings.ELASTICSEARCH_USERNAME or "elastic",
                settings.ELASTICSEARCH_PASSWORD or "",
            )
            if settings.ELASTICSEARCH_PASSWORD
            else None,
        )
        return client
    except Exception:
        return None


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


async def ensure_pandit_index(es_client) -> None:
    exists = await es_client.indices.exists(index=settings.ELASTICSEARCH_INDEX_PANDITS)
    if not exists:
        await es_client.indices.create(
            index=settings.ELASTICSEARCH_INDEX_PANDITS,
            body=PANDIT_INDEX_MAPPING,
        )


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    c = 2 * asin(sqrt(a))
    return 6371 * c


def _to_response(pandit: SearchPanditProjection, distance_km: float | None = None) -> PanditProfileResponse:
    return PanditProfileResponse(
        id=pandit.pandit_id,
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
        verification_status=pandit.verification_status,
        is_available=pandit.is_available,
        latitude=float(pandit.latitude) if pandit.latitude is not None else None,
        longitude=float(pandit.longitude) if pandit.longitude is not None else None,
        name=pandit.name,
        avatar_url=pandit.avatar_url,
        distance_km=round(distance_km, 2) if distance_km is not None else None,
    )


@router.get("/pandits", response_model=PanditSearchResponse)
async def search_pandits(
    lat: float = Query(..., ge=-90, le=90, description="User latitude"),
    lng: float = Query(..., ge=-180, le=180, description="User longitude"),
    radius_km: float = Query(default=25.0, ge=1, le=500, description="Search radius in km"),
    pooja_id: Optional[str] = Query(None, description="Filter by pooja type UUID"),
    languages: Optional[str] = Query(None, description="Comma-separated languages"),
    experience_min: Optional[int] = Query(None, ge=0),
    experience_max: Optional[int] = Query(None, ge=0),
    price_min: Optional[float] = Query(None, ge=0),
    price_max: Optional[float] = Query(None, ge=0),
    available_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    q: Optional[str] = Query(None, description="Text search on name/bio"),
    sort_by: str = Query(default="distance", pattern="^(distance|rating|price|experience)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    lang_list = [l.strip() for l in languages.split(",")] if languages else None

    es_client = await get_es_client()
    if es_client:
        try:
            return await _search_elasticsearch(
                es_client,
                lat,
                lng,
                radius_km,
                pooja_id,
                lang_list,
                experience_min,
                experience_max,
                price_min,
                price_max,
                available_date,
                q,
                sort_by,
                page,
                page_size,
            )
        except Exception:
            pass

    return await _search_projection_fallback(
        db,
        lat,
        lng,
        radius_km,
        pooja_id,
        lang_list,
        experience_min,
        experience_max,
        price_min,
        price_max,
        available_date,
        q,
        sort_by,
        page,
        page_size,
    )


async def _search_elasticsearch(
    es_client,
    lat: float,
    lng: float,
    radius_km: float,
    pooja_id: Optional[str],
    languages: Optional[List[str]],
    experience_min: Optional[int],
    experience_max: Optional[int],
    price_min: Optional[float],
    price_max: Optional[float],
    available_date: Optional[str],
    q: Optional[str],
    sort_by: str,
    page: int,
    page_size: int,
) -> PanditSearchResponse:
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

    must_clauses = []
    if q:
        must_clauses.append(
            {
                "multi_match": {
                    "query": q,
                    "fields": ["name^2", "bio"],
                    "fuzziness": "AUTO",
                }
            }
        )

    sort_options = {
        "distance": [{"_geo_distance": {"location": {"lat": lat, "lon": lng}, "order": "asc", "unit": "km"}}],
        "rating": [{"rating_avg": {"order": "desc"}}],
        "price": [{"base_fee": {"order": "asc"}}],
        "experience": [{"experience_years": {"order": "desc"}}],
    }

    response = await es_client.search(
        index=settings.ELASTICSEARCH_INDEX_PANDITS,
        body={
            "query": {"bool": {"must": must_clauses or [{"match_all": {}}], "filter": filters}},
            "sort": sort_options.get(sort_by, sort_options["distance"]),
            "from": (page - 1) * page_size,
            "size": page_size,
        },
    )

    total = response["hits"]["total"]["value"]
    items = []
    for hit in response["hits"]["hits"]:
        src = hit["_source"]
        distance = round(hit["sort"][0], 2) if hit.get("sort") and sort_by == "distance" else None
        items.append(
            PanditProfileResponse(
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
            )
        )

    return PanditSearchResponse(items=items, total=total, page=page, page_size=page_size)


async def _search_projection_fallback(
    db: AsyncSession,
    lat: float,
    lng: float,
    radius_km: float,
    pooja_id: Optional[str],
    languages: Optional[List[str]],
    experience_min: Optional[int],
    experience_max: Optional[int],
    price_min: Optional[float],
    price_max: Optional[float],
    available_date: Optional[str],
    q: Optional[str],
    sort_by: str,
    page: int,
    page_size: int,
) -> PanditSearchResponse:
    query = select(SearchPanditProjection).where(
        SearchPanditProjection.verification_status == "VERIFIED",
        SearchPanditProjection.is_available == True,
        SearchPanditProjection.latitude.is_not(None),
        SearchPanditProjection.longitude.is_not(None),
    )

    if experience_min is not None:
        query = query.where(SearchPanditProjection.experience_years >= experience_min)
    if experience_max is not None:
        query = query.where(SearchPanditProjection.experience_years <= experience_max)
    if price_min is not None:
        query = query.where(SearchPanditProjection.base_fee >= price_min)
    if price_max is not None:
        query = query.where(SearchPanditProjection.base_fee <= price_max)
    if languages:
        query = query.where(SearchPanditProjection.languages.overlap(languages))
    if pooja_id:
        query = query.where(SearchPanditProjection.poojas_offered.any(UUID(str(pooja_id))))
    if q:
        like = f"%{q}%"
        query = query.where(
            (SearchPanditProjection.name.ilike(like)) | (SearchPanditProjection.bio.ilike(like))
        )
    if available_date:
        from datetime import datetime

        try:
            target = datetime.strptime(available_date, "%Y-%m-%d")
            query = query.where(
                SearchPanditProjection.pandit_id.in_(
                    select(SearchPanditAvailabilityProjection.pandit_id).where(
                        func.date(SearchPanditAvailabilityProjection.date) == target.date(),
                        SearchPanditAvailabilityProjection.is_booked == False,
                        SearchPanditAvailabilityProjection.is_blocked == False,
                    )
                )
            )
        except ValueError:
            pass

    result = await db.execute(query)
    projections = result.scalars().all()
    items = []
    for pandit in projections:
        distance = _haversine_km(lat, lng, float(pandit.latitude), float(pandit.longitude))
        if distance > radius_km:
            continue
        items.append(_to_response(pandit, distance))

    sort_map = {
        "distance": lambda item: item.distance_km or 0,
        "rating": lambda item: -(float(item.rating_avg or 0)),
        "price": lambda item: float(item.base_fee or 0),
        "experience": lambda item: -(item.experience_years or 0),
    }
    items.sort(key=sort_map.get(sort_by, sort_map["distance"]))
    total = len(items)
    items = items[(page - 1) * page_size : page * page_size]
    return PanditSearchResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/poojas", response_model=List[dict])
async def search_poojas(
    q: Optional[str] = Query(None, description="Search text"),
    category: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    query = select(Pooja).where(Pooja.is_active == True)
    if q:
        search_term = f"%{q}%"
        query = query.where(
            (Pooja.name_en.ilike(search_term)) | (Pooja.name_hi.ilike(search_term))
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
    query = (
        select(SearchPanditProjection)
        .where(
            SearchPanditProjection.verification_status == "VERIFIED",
            SearchPanditProjection.name.ilike(f"%{q}%"),
        )
        .limit(5)
    )
    result = await db.execute(query)
    rows = result.scalars().all()
    return [
        {
            "id": str(row.pandit_id),
            "name": row.name,
            "city": row.city,
            "avatar_url": row.avatar_url,
            "rating_avg": float(row.rating_avg),
        }
        for row in rows
    ]

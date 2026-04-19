"""
Bootstrap Elasticsearch indices and backfill search documents from PostgreSQL.

Usage:
    python scripts/bootstrap_elasticsearch.py
"""

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.database import AsyncSessionLocal
from services.search.router import bootstrap_search_indices, get_es_client


async def main() -> None:
    es_client = await get_es_client()
    if not es_client:
        raise RuntimeError("Elasticsearch client could not be created. Check ELASTICSEARCH_URL and credentials.")

    try:
        async with AsyncSessionLocal() as db:
            result = await bootstrap_search_indices(db, es_client)
            print("Elasticsearch bootstrap complete")
            print(f"Pandit index: {result['pandit_index']}")
            print(f"Pooja index: {result['pooja_index']}")
            print(f"Pandits indexed: {result['pandits_indexed']}")
            print(f"Poojas indexed: {result['poojas_indexed']}")
    finally:
        await es_client.close()


if __name__ == "__main__":
    asyncio.run(main())

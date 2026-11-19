from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from ..auth import User, current_user
from ..deps import get_search
from ..schemas.api import SearchRequest, SearchResponse
from ..services.search import SearchService

router = APIRouter(tags=["search"])


@router.post("/search", response_model=SearchResponse, summary="Rank candidates for a JD")
async def search(body: SearchRequest, user: User = Depends(current_user), svc: SearchService = Depends(get_search)):
    return await svc.search(user.id, body)


@router.get("/search/{search_id}", response_model=SearchResponse, summary="Reload a past search")
async def get_search_run(search_id: str, user: User = Depends(current_user), svc: SearchService = Depends(get_search)):
    return await svc.get_run(user.id, search_id)


@router.get("/search/{search_id}/export.csv", response_class=Response, summary="CSV export (formula-injection safe)",
            responses={200: {"content": {"text/csv": {}}}})
async def export_csv(search_id: str, user: User = Depends(current_user), svc: SearchService = Depends(get_search)):
    data = await svc.export_csv(user.id, search_id)
    return Response(data, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="verihire-search-{search_id[:8]}.csv"'})

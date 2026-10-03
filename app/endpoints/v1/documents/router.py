"""Knowledge base ("Conoscenza"): upload after explicit authorisation, list, preview, search."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.core.knowledge.ingest import MAX_UPLOAD_BYTES
from app.core.knowledge.service import UploadRejected
from app.core.services import Services
from app.core.suggestions.engine import SuggestionEngine
from app.data.models.document import Document, DocumentSearchResult, DocumentWithUsage, TabularProfile, UsedBy
from app.endpoints.deps import CurrentUser, current_user, get_services
from app.gateways.sqlite.repositories import AutomationRepo
from app.i18n import t
from app.i18n.errors import api_error, request_lang

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


def _used_by(services: Services, org_id: str) -> dict[str, list[UsedBy]]:
    out: dict[str, list[UsedBy]] = {}
    for a in AutomationRepo(services.db, org_id).list():
        for did in a.source_document_ids:
            out.setdefault(did, []).append(UsedBy(automation_id=a.id, title=a.title))
    return out


@router.get("", response_model=list[DocumentWithUsage])
def list_documents(cu: CurrentUser = Depends(current_user), services: Services = Depends(get_services)):
    used = _used_by(services, cu.org_id)
    return [DocumentWithUsage(**d.model_dump(), used_by=used.get(d.id, [])) for d in services.knowledge(cu.org_id).docs.list()]


class UploadResult(BaseModel):
    documents: list[Document]
    rejected: list[dict]


@router.post("", response_model=UploadResult)
async def upload(
    files: list[UploadFile] = File(...),
    authorized: bool = Form(False),
    paths: list[str] | None = Form(None),  # folder upload: relative path of each file, same order
    lang: str = Depends(request_lang),
    cu: CurrentUser = Depends(current_user),
    services: Services = Depends(get_services),
) -> UploadResult:
    if not authorized:
        api_error(400, "errors.upload_not_authorized")
    ks = services.knowledge(cu.org_id)
    known = {d.id for d in ks.docs.list()}
    docs, rejected = [], []
    for i, f in enumerate(files):
        content = await f.read(MAX_UPLOAD_BYTES + 1)
        rel = paths[i] if paths and i < len(paths) else None
        try:
            docs.append(await asyncio.to_thread(ks.add, f.filename or "document", content, cu.id, True,
                                                cu.settings.language.value, rel))
        except UploadRejected as exc:
            rejected.append({"filename": f.filename, "code": exc.key, "reason": t(exc.key, lang, **exc.params)})
    # new documents may change automations or the company profile: suggestions run in the background
    services.suggestions.enqueue(cu.org_id, cu.id, [d.id for d in docs if d.id not in known])
    return UploadResult(documents=docs, rejected=rejected)


@router.get("/search", response_model=list[DocumentSearchResult])
def search(q: str, cu: CurrentUser = Depends(current_user), services: Services = Depends(get_services)):
    return services.knowledge(cu.org_id).search_documents(q)


class DocumentDetail(BaseModel):
    document: DocumentWithUsage
    profiles: list[TabularProfile]
    parts: int


@router.get("/{document_id}", response_model=DocumentDetail)
def detail(document_id: str, cu: CurrentUser = Depends(current_user), services: Services = Depends(get_services)):
    ks = services.knowledge(cu.org_id)
    doc = ks.docs.get(document_id)
    if not doc:
        api_error(404, "errors.document_not_found")
    used = _used_by(services, cu.org_id).get(doc.id, [])
    md = ks.docs.markdown(doc.id)
    return DocumentDetail(
        document=DocumentWithUsage(**doc.model_dump(), used_by=used),
        profiles=ks.docs.profiles(doc.id),
        parts=max(1, -(-len(md) // 6000)),
    )


@router.get("/{document_id}/content")
def content(document_id: str, part: int = 0, cu: CurrentUser = Depends(current_user),
            services: Services = Depends(get_services)) -> dict:
    ks = services.knowledge(cu.org_id)
    if not ks.docs.get(document_id):
        api_error(404, "errors.document_not_found")
    md = ks.docs.markdown(document_id)
    return {"part": part, "parts": max(1, -(-len(md) // 6000)), "markdown": md[part * 6000 : (part + 1) * 6000]}


@router.get("/{document_id}/file")
def original(document_id: str, cu: CurrentUser = Depends(current_user), services: Services = Depends(get_services)):
    ks = services.knowledge(cu.org_id)
    res = ks.resolve(document_id)
    if not res or not res[1].exists():
        api_error(404, "errors.file_not_found")
    return FileResponse(res[1], filename=res[0].filename)


@router.delete("/{document_id}")
async def delete(document_id: str, cu: CurrentUser = Depends(current_user), services: Services = Depends(get_services)) -> dict:
    ks = services.knowledge(cu.org_id)
    doc = ks.docs.get(document_id)
    if doc:
        SuggestionEngine(services, cu.org_id, cu.id).on_document_deleted(doc)
    await asyncio.to_thread(ks.delete, document_id)
    return {"ok": True}

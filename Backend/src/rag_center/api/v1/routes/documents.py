import json

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from rag_center.api.v1.deps import ApiDependencies
from rag_center.core.auth import TenantContext
from rag_center.modules.documents.schemas.document import (
    DocumentDetailData,
    ReindexDocumentData,
    UploadDocumentData,
    UploadDocumentRequest,
)
from rag_center.modules.documents.services.document_service import DocumentService
from rag_center.schemas.common import ApiResponse, accepted_response, success_response

router = APIRouter(tags=["documents"])


@router.post(
    "/knowledge-bases/{kb_id}/documents",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ApiResponse[UploadDocumentData],
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "required": ["title", "content"],
                        "properties": {
                            "title": {"type": "string", "maxLength": 255, "minLength": 1},
                            "content": {"type": "string", "minLength": 1},
                        },
                    }
                },
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["file"],
                        "properties": {
                            "file": {"type": "string", "format": "binary"},
                            "title": {"type": "string"},
                        },
                    }
                },
            },
        }
    },
)
async def upload_document(
    request: Request,
    kb_id: str,
    db: AsyncSession = Depends(ApiDependencies.session),
    tenant: TenantContext = Depends(ApiDependencies.tenant),
) -> dict:
    content_type = request.headers.get("content-type", "").lower()
    service = DocumentService(db)

    if "multipart/form-data" in content_type:
        form = await request.form()
        upload_file = form.get("file")
        read = getattr(upload_file, "read", None)
        if read is None:
            from rag_center.core.exceptions import AppError, ErrorCode

            raise AppError(ErrorCode.PARAM_ERROR, msg="file 不能为空")

        filename = getattr(upload_file, "filename", None) or "upload.bin"
        file_bytes = await read()
        title_field = form.get("title")
        title = str(title_field).strip() if title_field else None

        data = await service.upload_file(
            tenant.tenant_id,
            kb_id=kb_id,
            title=title,
            filename=filename,
            file_bytes=file_bytes,
        )
    else:
        try:
            body = await request.json()
            payload = UploadDocumentRequest.model_validate({**body, "kb_id": kb_id})
        except (ValidationError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise RequestValidationError(
                [{"loc": ["body"], "msg": "请提供有效的 title 和 content", "type": "value_error"}]
            ) from exc
        data = await service.upload(tenant.tenant_id, payload)

    return accepted_response(data.model_dump(mode="json"))


@router.get("/documents/{document_id}", response_model=ApiResponse[DocumentDetailData])
async def get_document(
    document_id: str,
    db: AsyncSession = Depends(ApiDependencies.session),
    tenant: TenantContext = Depends(ApiDependencies.tenant),
) -> dict:
    data = await DocumentService(db).get_detail(tenant.tenant_id, document_id)
    return success_response(data.model_dump(mode="json"))


@router.delete("/documents/{document_id}", status_code=204, response_class=Response)
async def delete_document(
    document_id: str,
    db: AsyncSession = Depends(ApiDependencies.session),
    tenant: TenantContext = Depends(ApiDependencies.tenant),
) -> Response:
    await DocumentService(db).delete(tenant.tenant_id, document_id)
    return Response(status_code=204)


@router.post(
    "/documents/{document_id}/index-jobs",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ApiResponse[ReindexDocumentData],
)
async def reindex_document(
    document_id: str,
    reparse: bool = False,
    db: AsyncSession = Depends(ApiDependencies.session),
    tenant: TenantContext = Depends(ApiDependencies.tenant),
) -> dict:
    data = await DocumentService(db).reindex(tenant.tenant_id, document_id, reparse=reparse)
    return accepted_response(data.model_dump(mode="json"))

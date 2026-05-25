from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel
from sqlalchemy.orm import Session

import agent
import parser as docparser
from database import ImageAsset, Template, get_db, init_db

UPLOAD_DIR = Path(os.environ.get("VITEGRID_UPLOAD_DIR", "static/uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_DOC_SUFFIXES = {".pdf", ".docx"}
ALLOWED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}

@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Vitegrid Backend", version="0.1.0", lifespan=lifespan)

allow_origins = [
    origin.strip()
    for origin in os.environ.get("VITEGRID_ALLOW_ORIGINS", "http://localhost:5173").split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

app.mount("/static", StaticFiles(directory="static"), name="static")


class GenerateRequest(BaseModel):
    goal: str


class GenerateResponse(BaseModel):
    layout: dict[str, Any]
    audit: dict[str, Any]


class RefineRequest(BaseModel):
    text: str
    instruction: str


class RefineResponse(BaseModel):
    text: str


class TemplateCreate(BaseModel):
    name: str
    source_type: str
    layout: dict[str, Any]
    lock_tier: int = 3
    original_file_path: str | None = None
    thumbnail_path: str | None = None


class TemplateUpdate(BaseModel):
    name: str | None = None
    layout: dict[str, Any] | None = None
    lock_tier: int | None = None


class TemplateSummary(BaseModel):
    id: int
    name: str
    source_type: str
    lock_tier: int
    thumbnail_path: str | None
    created_at: str
    updated_at: str


class ImageUploadResponse(BaseModel):
    id: int
    local_path: str
    width_px: int
    height_px: int


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/generate", response_model=GenerateResponse)
def generate(req: GenerateRequest) -> GenerateResponse:
    if not req.goal.strip():
        raise HTTPException(status_code=400, detail="goal must not be empty")
    layout, report = agent.generate_from_prompt(req.goal)
    return GenerateResponse(layout=layout.model_dump(), audit=report.model_dump())


def _save_upload(file: UploadFile, allowed: set[str]) -> Path:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in allowed:
        raise HTTPException(status_code=415, detail=f"Unsupported file type: {suffix}")
    target = UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
    with target.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    return target


@app.post("/api/import", response_model=GenerateResponse)
async def import_layout(
    document: UploadFile = File(...),
    template_image: UploadFile | None = File(None),
) -> GenerateResponse:
    doc_path = _save_upload(document, ALLOWED_DOC_SUFFIXES)
    image_path: Path | None = None
    if template_image is not None:
        image_path = _save_upload(template_image, ALLOWED_IMAGE_SUFFIXES)

    if doc_path.suffix.lower() == ".pdf":
        extraction = docparser.extract_pdf_layout(doc_path)
        pipeline = os.environ.get("VITEGRID_PIPELINE", "ensemble").lower()
        if extraction.is_scanned or pipeline == "ocr+ensemble":
            extraction = docparser.augment_extraction_with_ocr(extraction)
            layout, report = agent.ensemble_pdf_import(extraction)
        elif pipeline == "vision":
            layout, report = agent.import_from_pdf_extraction(extraction)
        elif pipeline == "heuristic":
            classified = docparser.classify_pdf_layout(extraction)
            page_w = extraction.pages[0].page_width_pt if extraction.pages else 612.0
            page_h = extraction.pages[0].page_height_pt if extraction.pages else 792.0
            layout, report = agent.import_from_classified_blocks(
                classified, page_w, page_h, len(extraction.pages)
            )
        else:
            layout, report = agent.ensemble_pdf_import(extraction)
    elif doc_path.suffix.lower() == ".docx":
        docx_blocks = docparser.extract_docx_layout(doc_path)
        layout, report = agent.import_from_docx_blocks(docx_blocks)
    else:
        parsed = docparser.parse_document(doc_path)
        layout, report = agent.import_from_parsed(
            markdown=parsed.markdown,
            tables=parsed.tables,
            template_image_path=image_path,
        )
    layout_dict = layout.model_dump()
    layout_dict["_source_file"] = "/" + doc_path.as_posix().lstrip("./")
    return GenerateResponse(layout=layout_dict, audit=report.model_dump())


class ChatRequest(BaseModel):
    layout: dict[str, Any]
    history: list[dict[str, str]]
    message: str


class ChatResponseModel(BaseModel):
    assistant_message: str
    updated_layout: dict[str, Any] | None = None


@app.post("/api/agent/chat", response_model=ChatResponseModel)
def agent_chat(req: ChatRequest) -> ChatResponseModel:
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="message must not be empty")
    layout_obj = agent.DocumentLayout.model_validate(req.layout)
    history = [agent.ChatTurn(role=h["role"], content=h["content"]) for h in req.history]  # type: ignore[arg-type]
    result = agent.agent6_chat(layout_obj, history, req.message)
    return ChatResponseModel(
        assistant_message=result.assistant_message,
        updated_layout=result.updated_layout.model_dump() if result.updated_layout else None,
    )


@app.post("/api/agent/refine", response_model=RefineResponse)
def refine_text(req: RefineRequest) -> RefineResponse:
    if not req.text.strip() or not req.instruction.strip():
        raise HTTPException(status_code=400, detail="text and instruction are required")
    prompt = (
        "Rewrite the following text following the instruction. "
        "Return only the rewritten text, no preface.\n\n"
        f"Instruction: {req.instruction}\n\nText:\n{req.text}"
    )
    response = agent._call_with_retry(  # type: ignore[attr-defined]
        agent._core_client(),  # type: ignore[attr-defined]
        model=agent._generation_model(),  # type: ignore[attr-defined]
        contents=[prompt],
    )
    rewritten = (getattr(response, "text", None) or "").strip()
    return RefineResponse(text=rewritten or req.text)


@app.post("/api/images", response_model=ImageUploadResponse)
def upload_image(
    file: UploadFile = File(...),
    template_id: int | None = Form(None),
    db: Session = Depends(get_db),
) -> ImageUploadResponse:
    saved = _save_upload(file, ALLOWED_IMAGE_SUFFIXES)
    with Image.open(saved) as im:
        width, height = im.size
        mime = Image.MIME.get(im.format or "", "application/octet-stream")

    asset = ImageAsset(
        template_id=template_id,
        local_path=str(saved.as_posix()),
        original_filename=file.filename or saved.name,
        mime_type=mime,
        width_px=width,
        height_px=height,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return ImageUploadResponse(
        id=asset.id,
        local_path=asset.local_path,
        width_px=width,
        height_px=height,
    )


@app.post("/api/templates")
def create_template(req: TemplateCreate, db: Session = Depends(get_db)) -> dict[str, Any]:
    if req.source_type not in {"imported", "generated"}:
        raise HTTPException(status_code=400, detail="source_type must be 'imported' or 'generated'")
    if req.lock_tier not in {1, 2, 3}:
        raise HTTPException(status_code=400, detail="lock_tier must be 1, 2, or 3")
    tpl = Template(
        name=req.name,
        source_type=req.source_type,
        layout_json=json.dumps(req.layout),
        lock_tier=req.lock_tier,
        original_file_path=req.original_file_path,
        thumbnail_path=req.thumbnail_path,
    )
    db.add(tpl)
    db.commit()
    db.refresh(tpl)
    return {"id": tpl.id}


@app.get("/api/templates", response_model=list[TemplateSummary])
def list_templates(db: Session = Depends(get_db)) -> list[TemplateSummary]:
    rows = db.query(Template).order_by(Template.updated_at.desc()).all()
    return [
        TemplateSummary(
            id=row.id,
            name=row.name,
            source_type=row.source_type,
            lock_tier=row.lock_tier,
            thumbnail_path=row.thumbnail_path,
            created_at=row.created_at.isoformat(),
            updated_at=row.updated_at.isoformat(),
        )
        for row in rows
    ]


@app.get("/api/templates/{template_id}")
def get_template(template_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    tpl = db.get(Template, template_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail="template not found")
    return {
        "id": tpl.id,
        "name": tpl.name,
        "source_type": tpl.source_type,
        "lock_tier": tpl.lock_tier,
        "original_file_path": tpl.original_file_path,
        "thumbnail_path": tpl.thumbnail_path,
        "layout": json.loads(tpl.layout_json),
        "created_at": tpl.created_at.isoformat(),
        "updated_at": tpl.updated_at.isoformat(),
    }


@app.put("/api/templates/{template_id}")
def update_template(
    template_id: int, req: TemplateUpdate, db: Session = Depends(get_db)
) -> dict[str, Any]:
    tpl = db.get(Template, template_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail="template not found")
    if req.name is not None:
        tpl.name = req.name
    if req.layout is not None:
        tpl.layout_json = json.dumps(req.layout)
    if req.lock_tier is not None:
        if req.lock_tier not in {1, 2, 3}:
            raise HTTPException(status_code=400, detail="lock_tier must be 1, 2, or 3")
        tpl.lock_tier = req.lock_tier
    db.commit()
    return {"id": tpl.id}


@app.delete("/api/templates/{template_id}")
def delete_template(template_id: int, db: Session = Depends(get_db)) -> dict[str, str]:
    tpl = db.get(Template, template_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail="template not found")
    db.delete(tpl)
    db.commit()
    return {"status": "deleted"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8001, reload=False)

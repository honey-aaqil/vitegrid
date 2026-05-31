from __future__ import annotations

import copy
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
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel
from sqlalchemy.orm import Session

import agent
import parser as docparser
from database import ImageAsset, Template, get_db, init_db
from streaming import demo_iteration_generator, sse_formatter

UPLOAD_DIR = Path(os.environ.get("VITEGRID_UPLOAD_DIR", "static/uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
# Images are also accepted as the primary document — they route through the
# three-step vision pipeline in :func:`agent.import_from_image`.
ALLOWED_DOC_SUFFIXES = {".pdf", ".docx"} | ALLOWED_IMAGE_SUFFIXES

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
            # Route through closed-loop visual optimization engine
            layout = agent.optimize_template_closed_loop(
                initial_layout=layout,
                ground_truth_pdf_path=doc_path,
                max_iterations=4,
                target_threshold=0.45,
            )
        elif pipeline == "closedloop":
            classified = docparser.classify_pdf_layout(extraction)
            page_w = extraction.pages[0].page_width_pt if extraction.pages else 612.0
            page_h = extraction.pages[0].page_height_pt if extraction.pages else 792.0
            layout, report = agent.import_from_classified_blocks(
                classified, page_w, page_h, len(extraction.pages)
            )
            layout = agent.optimize_template_closed_loop(
                initial_layout=layout,
                ground_truth_pdf_path=doc_path,
                max_iterations=4,
                target_threshold=0.45,
            )
        else:
            layout, report = agent.ensemble_pdf_import(extraction)
    elif doc_path.suffix.lower() == ".docx":
        docx_blocks = docparser.extract_docx_layout(doc_path)
        layout, report = agent.import_from_docx_blocks(docx_blocks)
    elif doc_path.suffix.lower() in ALLOWED_IMAGE_SUFFIXES:
        # Image-as-primary-document: full three-step vision pipeline
        # (spatial anchoring -> optical parsing -> per-element style mapping).
        layout, report = agent.import_from_image(doc_path)
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



@app.get("/api/documents/{document_id}/stream")
async def stream_document_reconstruction(
    document_id: str,
    source_file_path: str,
    max_iterations: int = 10,
    target_threshold: float = 0.01,
) -> StreamingResponse:
    """Server-Sent Events endpoint using GET requests for reliable event stream."""

    async def optimization_generator():
        """Stream real optimization loop events from closed-loop engine"""
        import base64
        import asyncio
        from pathlib import Path

        try:
            clean_path = source_file_path.lstrip("/")
            ground_truth_path = Path(clean_path)

            if not ground_truth_path.exists():
                yield f"event: error\ndata: {json.dumps({'error': f'Source not found: {source_file_path}'})}\n\n"
                return

            yield f"event: parsing_complete\ndata: {json.dumps({'status': 'initializing_loop', 'document_id': document_id})}\n\n"

            # Run optimization in executor to avoid blocking
            def run_optimization():
                try:
                    from parser import render_layout_screenshot, calculate_visual_regression, extract_pdf_layout, classify_pdf_layout
                    import pymupdf
                    from pathlib import Path
                    import uuid
                    import re

                    work_dir = ground_truth_path.parent / f"opt_{document_id}_{uuid.uuid4().hex}"
                    work_dir.mkdir(parents=True, exist_ok=True)

                    # Re-parse the source document to get initial layout
                    try:
                        extraction = extract_pdf_layout(ground_truth_path)
                        classified = classify_pdf_layout(extraction)
                        page_w = extraction.pages[0].page_width_pt if extraction.pages else 612.0
                        page_h = extraction.pages[0].page_height_pt if extraction.pages else 792.0
                        current_layout, _ = agent.import_from_classified_blocks(
                            classified, page_w, page_h, len(extraction.pages)
                        )
                    except Exception as e:
                        return f"Failed to parse source document: {e}"

                    # Rasterize ground truth
                    gt_image_path = work_dir / "ground_truth.png"
                    try:
                        doc = pymupdf.open(str(ground_truth_path))
                        pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(96 / 72.0, 96 / 72.0), alpha=False)
                        pix.save(str(gt_image_path))
                        doc.close()
                    except Exception as e:
                        return f"Failed to read ground truth: {e}"

                    gt_bytes = gt_image_path.read_bytes()
                    results = []

                    for iteration in range(max_iterations):
                        cand_path = work_dir / f"candidate_{iteration}.png"
                        diff_path = work_dir / f"diff_{iteration}.png"

                        try:
                            render_layout_screenshot(
                                current_layout.model_dump_json(),
                                cand_path,
                                width=int(current_layout.page_width_px),
                                height=int(current_layout.page_height_px),
                            )
                        except Exception as e:
                            return f"Render failed at iteration {iteration}: {e}"

                        if not cand_path.exists():
                            break

                        try:
                            error_score = calculate_visual_regression(gt_image_path, cand_path, diff_path)
                        except Exception as e:
                            return f"Regression calc failed: {e}"

                        cand_bytes = cand_path.read_bytes()
                        diff_bytes = diff_path.read_bytes() if diff_path.exists() else b""

                        results.append({
                            "iteration": iteration,
                            "divergence": error_score,
                            "candidate_b64": base64.b64encode(cand_bytes).decode(),
                            "diff_b64": base64.b64encode(diff_bytes).decode() if diff_bytes else "",
                        })

                        if error_score <= target_threshold:
                            break

                        try:
                            patch_report = agent.agent_refine_layout_schema(
                                gt_bytes, cand_bytes, diff_bytes, current_layout
                            )
                            block_map = {b.id: b for b in current_layout.blocks}

                            # Apply the layout patches back to the block bounding boxes
                            for patch in patch_report.patches:
                                if patch.element_id in block_map:
                                    target_block = block_map[patch.element_id]

                                    # 1. Apply Typographical Overrides
                                    if patch.font_size_pt and patch.font_size_pt != 11.0:
                                        target_block.style.font_size_pt = patch.font_size_pt
                                    if patch.text_align:
                                        target_block.style.align = patch.text_align
                                    if patch.color_hex:
                                        target_block.style.color_hex = patch.color_hex.replace("#", "")
                                    if patch.line_height_multiplier and patch.line_height_multiplier > 1.0:
                                        target_block.spacing.line_height_px = target_block.style.font_size_pt * patch.line_height_multiplier

                                    # 2. Convert and Apply Background Shading Colors
                                    if patch.background_color_rgba and "rgba(0,0,0,0)" not in patch.background_color_rgba:
                                        rgba_numbers = [int(x) for x in re.findall(r"\d+", patch.background_color_rgba)[:3]]
                                        if len(rgba_numbers) == 3:
                                            target_block.style.background_hex = f"{rgba_numbers[0]:02x}{rgba_numbers[1]:02x}{rgba_numbers[2]:02x}"

                                    # 3. Apply Absolute Spatial Bounding Updates to match Input Imagery
                                    if target_block.bbox:
                                        # Adjust positions dynamically based on red mask shift calculations
                                        if patch.margin and patch.margin.top_px != 0:
                                            target_block.bbox.y_px += patch.margin.top_px
                                        if patch.margin and patch.margin.left_px != 0:
                                            target_block.bbox.x_px += patch.margin.left_px

                                        # Apply absolute dimension adjustments
                                        if patch.top_px_offset is not None:
                                            target_block.bbox.y_px = patch.top_px_offset
                                        if patch.left_px_offset is not None:
                                            target_block.bbox.x_px = patch.left_px_offset
                                        if patch.width_pct and patch.width_pct > 0:
                                            target_block.bbox.width_px = (patch.width_pct / 100.0) * current_layout.page_width_px
                                        if patch.height_px_offset != 0:
                                            target_block.bbox.height_px += patch.height_px_offset

                            # RE-CALIBRATE DOWNSTREAM FLOW: Reflow blocks after patches
                            # Ensures elements shift vertically when upper blocks change height/position
                            margin_top = current_layout.margin_px.get("top", 72.0) if isinstance(current_layout.margin_px, dict) else current_layout.margin_px.top
                            margin_left = current_layout.margin_px.get("left", 72.0) if isinstance(current_layout.margin_px, dict) else current_layout.margin_px.left
                            margin_right = current_layout.margin_px.get("right", 72.0) if isinstance(current_layout.margin_px, dict) else current_layout.margin_px.right

                            full_width_threshold = current_layout.page_width_px - margin_left - margin_right
                            cursor_y = float(margin_top)

                            for block in current_layout.blocks:
                                if block.bbox:
                                    # If block is full-width (or nearly full-width), reflow it relative to cursor
                                    if abs(block.bbox.width_px - full_width_threshold) < 2.0:
                                        block.bbox.y_px = max(block.bbox.y_px, cursor_y)
                                    # Update cursor for next block's potential reflow
                                    cursor_y = max(cursor_y, block.bbox.y_px + block.bbox.height_px + 8)

                            current_layout = agent.auto_layout(current_layout)
                        except Exception as e:
                            return f"Patch application failed: {e}"

                    return (current_layout, results)
                except Exception as e:
                    return f"Optimization error: {str(e)}"

            # Run optimization
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, run_optimization)

            if isinstance(result, str):
                # Error occurred
                error_data = {"error": result}
                yield f"event: error\ndata: {json.dumps(error_data)}\n\n"
                return

            final_layout, iterations = result

            # Stream iteration results
            for it_data in iterations:
                event_data = {
                    "iteration": it_data["iteration"] + 1,
                    "divergence_percentage": round(it_data["divergence"], 2),
                    "candidate_render_b64": f"data:image/png;base64,{it_data['candidate_b64']}",
                    "diff_mask_b64": f"data:image/png;base64,{it_data['diff_b64']}" if it_data["diff_b64"] else "",
                }
                yield f"event: evaluation_loop\ndata: {json.dumps(event_data)}\n\n"

            # Final result
            final_data = {
                "status": "converged",
                "total_iterations": len(iterations),
                "final_divergence_percentage": iterations[-1]["divergence"] if iterations else 0,
                "final_layout": final_layout.model_dump(),
            }
            yield f"event: reconstruction_verified\ndata: {json.dumps(final_data)}\n\n"

        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'error': f'Stream error: {str(e)}'})}\n\n"

    return StreamingResponse(optimization_generator(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8001, reload=True)

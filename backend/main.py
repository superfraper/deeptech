import asyncio
import json
import logging
import logging.handlers
import os
import tempfile
import threading
import time
import uuid
from contextlib import suppress
from datetime import datetime
from pathlib import Path

import openai
import requests
from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from opensearchpy import helpers
from pydantic import BaseModel

from app.config import get_opensearch_client, get_s3_client, settings
from app.core.auth import get_current_user
from app.core.db_adapter import connect, execute, is_postgres_enabled
from app.core.db_handler import DatabaseHandler
from app.models import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ChatSession,
    ChatSessionListItem,
    ChecklistDefinition,
    ChecklistItem,
    ContractAuditCreate,
    ContractAuditStatus,
    DoraAuditCreate,
    DoraAuditListItem,
    DoraAuditResult,
    DoraAuditStatus,
    FieldFillResponse,
    FollowUpQuestionRequest,
    GenerateAnswerRequest,
    GenerateAnswerResponse,
    GenerateRequest,
    GenerationStatus,
    RegenerateRequest,
    ServiceMapping,
    UserContextRequest,
    UserContextResponse,
    UserProfileCreate,
    UserProfileResponse,
    Vendor,
    VendorContract,
    VendorContractCreate,
    VendorCreate,
    VendorQualification,
    VendorQualificationCreate,
    VendorQualificationListItem,
    VendorQualificationStepUpdate,
    VendorUpdate,
)
from app.utils.async_utils import run_in_thread
from app.utils.dependency_analyzer import DependencyAnalyzer
from app.utils.generate import generate_field_fill, regenerate_field_fill
from app.utils.generation_tracker import generation_tracker
from app.utils.helper_functions import delete_s3_file
from app.utils.json_loader import get_whitepaper_fields_by_section, preflight_json_validation
from app.utils.retrieve import chunk_text_tiktoken, generate_chunk_context, scrape_links
from app.utils.textract_extractor import TextractExtractor, create_chunks_from_layout
from data.test_data.dummy_data import DUMMY_DATA

# -------------------- Logging Configuration --------------------


def _pick_logs_dir():
    """Pick a writable logs directory that works in and outside Docker."""
    candidates = []
    with suppress(Exception):
        env_dir = os.getenv("LOGS_DIR")
        if env_dir:
            candidates.append(Path(env_dir))

    # Docker default
    candidates.append(Path("/app/logs"))

    # Repo root logs (backend/.. -> project root)
    with suppress(Exception):
        candidates.append(Path(__file__).resolve().parent.parent / "logs")

    # CWD logs
    with suppress(Exception):
        candidates.append(Path.cwd() / "logs")

    # Try to create and verify writability
    for p in candidates:
        try:
            p.mkdir(parents=True, exist_ok=True)
            test_file = p / ".write_test"
            with open(test_file, "w", encoding="utf-8") as f:
                f.write("ok")
            with suppress(Exception):
                test_file.unlink()
            return p
        except Exception:
            continue

    # Last resort: tmp
    tmp = Path(tempfile.gettempdir()) / "rag_api_logs"
    tmp.mkdir(parents=True, exist_ok=True)
    return tmp


def setup_logging():
    """Setup logging configuration for multi-worker environment"""
    # Get root logger
    root_logger = logging.getLogger()

    # Clear any existing handlers to avoid duplicates
    if root_logger.handlers:
        root_logger.handlers.clear()

    root_logger.setLevel(logging.INFO)

    # Create formatter
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s:%(lineno)d - %(message)s")

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    logs_dir = _pick_logs_dir()
    log_file = logs_dir / "rag_api.log"
    root_logger.info(f"Using logs directory: {logs_dir}")
    # Optionally truncate stale log file at startup to avoid confusion with legacy entries
    try:
        if os.getenv("TRUNCATE_LOG_ON_STARTUP", "1") == "1":
            with open(log_file, "w", encoding="utf-8"):
                pass
    except Exception as e:
        root_logger.warning(f"Could not truncate log file on startup at {log_file}: {e}")

    # File handler with rotation support for multi-worker environment
    try:
        # Use RotatingFileHandler which is safer for multi-process environments
        file_handler = logging.handlers.RotatingFileHandler(
            str(log_file),
            mode="a",
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5,
        )
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

        # Test the file handler by writing a startup message
        test_logger = logging.getLogger("STARTUP")
        test_logger.info("Logging system initialized successfully")

    except Exception as e:
        root_logger.error(f"Failed to configure file logging: {e}")
        # Also print to stderr for debugging
        print(f"LOGGING ERROR: Failed to configure file logging: {e}")


# Setup logging
setup_logging()

logger = logging.getLogger("RAG_API")
logger.info("Application starting")
dti_data = {"records": []}
dti_data_lock = threading.Lock()  # Thread-safe lock for dti_data access


# Thread-safe DTI data access functions
def get_dti_data():
    """Thread-safe getter for DTI data"""
    with dti_data_lock:
        return dti_data.copy()  # Return a copy to avoid external mutation


def set_dti_data(new_data):
    """Thread-safe setter for DTI data"""
    global dti_data
    with dti_data_lock:
        dti_data = new_data.copy() if isinstance(new_data, dict) else new_data


def get_dti_records():
    """Thread-safe getter for DTI records list"""
    with dti_data_lock:
        return dti_data.get("records", []).copy()


def get_dti_record_count():
    """Thread-safe getter for DTI record count"""
    with dti_data_lock:
        return len(dti_data.get("records", []))


# -------------------- Environment and App Setup --------------------

app = FastAPI(title="RAG System API")

origins = [
    "http://localhost",
    "http://localhost:8000",
    "http://localhost:8888",
    "https://localhost:3000",
    "http://localhost:3000",
    "https://localhost",
    "http://127.0.0.1:3000",
    "https://127.0.0.1:3000",
    "https://deeptech-ui.vercel.app",
]

# Add any additional origins from environment variable
extra_origins = os.getenv("CORS_ORIGINS", "")
if extra_origins:
    origins.extend([o.strip() for o in extra_origins.split(",") if o.strip()])
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=3600,
)

S3_BUCKET = settings.S3_BUCKET
openai.api_key = settings.OPENAI_API_KEY

s3_client = get_s3_client()
os_client = get_opensearch_client()


class LEIRequest(BaseModel):
    lei: str


class ELFRequest(BaseModel):
    elf_code: str


# Add this helper function after the imports


def get_db_handler_for_request(request_dict):
    """Helper function to get appropriate DatabaseHandler based on request parameters"""
    # Extract whitepaper type from request or default to "OTH"
    whitepaper_type = request_dict.get("whitepaperType", "OTH")

    # Map whitepaper type to database token classification
    token_classification = "OTH"  # Default

    # Convert to uppercase for consistency in mapping
    if whitepaper_type:
        whitepaper_type = whitepaper_type.upper()

    if "EMT" in whitepaper_type:
        token_classification = "EMT"
    elif "ART" in whitepaper_type:
        token_classification = "ART"

    logger.info(f"Creating DatabaseHandler for token classification: {token_classification} based on whitepaper type: {whitepaper_type}")
    return DatabaseHandler(token_classification=token_classification)


# -------------------- FastAPI Endpoints --------------------


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}


# -------------------- Generation Status Endpoints --------------------


@app.get("/api/generation/{generation_id}/status", response_model=GenerationStatus)
async def get_generation_status(generation_id: str, current_user: dict = Depends(get_current_user)):
    """Get the status of a specific generation"""
    try:
        status = generation_tracker.get_generation_status(generation_id)
        if not status:
            raise HTTPException(status_code=404, detail="Generation not found")

        # Verify the generation belongs to the current user
        if status.user_id != current_user["sub"]:
            raise HTTPException(status_code=403, detail="Access denied")

        return status
    except Exception as e:
        logger.error(f"Error getting generation status: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@app.get("/api/generation/user/active")
async def get_user_active_generation(current_user: dict = Depends(get_current_user)):
    """Get the active generation for the current user"""
    try:
        active_generation = generation_tracker.get_user_active_generation(current_user["sub"])
        if active_generation:
            return {"active_generation": active_generation}
        else:
            return {"active_generation": None}
    except Exception as e:
        logger.error(f"Error getting user active generation: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@app.post("/api/generation/{generation_id}/cancel")
async def cancel_generation(generation_id: str, current_user: dict = Depends(get_current_user)):
    """Cancel an active generation"""
    try:
        status = generation_tracker.get_generation_status(generation_id)
        if not status:
            raise HTTPException(status_code=404, detail="Generation not found")

        # Verify the generation belongs to the current user
        if status.user_id != current_user["sub"]:
            raise HTTPException(status_code=403, detail="Access denied")

        # Only allow canceling pending or in_progress generations
        if status.status not in ["pending", "in_progress"]:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot cancel generation with status: {status.status}",
            )

        # Update status to cancelled
        generation_tracker.update_generation_status(generation_id, status="failed", error_message="Generation cancelled by user")

        logger.info(f"Generation {generation_id} cancelled by user {current_user['sub']}")
        return {"message": "Generation cancelled successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error cancelling generation: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@app.delete("/api/upload/abort/{filename}")
async def abort_file_upload(filename: str, current_user: dict = Depends(get_current_user)):
    """
    Endpoint to abort file upload/processing.
    Note: This is mainly for client-side abort functionality.
    The actual file processing cannot be stopped once it reaches the backend,
    but the client can use this to clean up state.
    """
    try:
        logger.info(f"File upload abort requested for {filename} by user {current_user['sub']}")

        # In a real implementation, you might want to:
        # 1. Check if the file processing is still ongoing
        # 2. Mark it as cancelled in a database
        # 3. Clean up any temporary files

        return {"message": f"Upload abort request received for {filename}"}
    except Exception as e:
        logger.error(f"Error aborting file upload for {filename}: {e!s}")
        raise HTTPException(status_code=500, detail=f"Error aborting upload: {e!s}") from e


@app.post("/upload")
async def upload_file(file: UploadFile = File(...), current_user: dict = Depends(get_current_user)):
    """
    Endpoint to upload a PDF file, extract its text, generate embeddings for each chunk,
    and index the chunks in OpenSearch while uploading the file to S3.

    Args:
        file (UploadFile): The PDF file to be uploaded and processed.

    Returns:
        JSONResponse: A response indicating the success of the operation and the S3 key.
    """
    logger.info("Received file upload request: %s", file.filename)
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    try:
        file_bytes = await file.read()

        document_id = uuid.uuid4().hex
        user_id = current_user["sub"]

        safe_filename = file.filename.replace("/", "_").replace("\\", "_")
        s3_key = f"uploads/{user_id}/{document_id}_{safe_filename}"

        logger.info(
            "Uploading file for user %s: filename=%s, document_id=%s, s3_key=%s",
            user_id,
            file.filename,
            document_id,
            s3_key,
        )

        # Upload to S3
        try:
            s3_client.put_object(Bucket=S3_BUCKET, Key=s3_key, Body=file_bytes)
            logger.info("Uploaded file %s to S3 with key: %s", file.filename, s3_key)
        except Exception as e:
            logger.error("Failed to upload file %s to S3: %s", file.filename, e)
            raise HTTPException(status_code=500, detail="Failed to upload file to S3.") from e

        # ---- Text extraction & chunking (Textract first, then fallback to docling) ----
        chunks: list[str] = []
        full_text: str = ""

        # Try Textract path (from main)
        try:
            extractor = TextractExtractor()
            loop = asyncio.get_running_loop()
            analysis_result = await loop.run_in_executor(
                None,
                extractor.extract_text_from_pdf_bytes,
                file_bytes,
                file.filename,
            )

            layout_elems = analysis_result.get("layout_elements", []) or []
            if layout_elems:
                chunk_objs = create_chunks_from_layout(layout_elems, max_chunk_size=4000)
                chunks = [c.get("text", "") for c in chunk_objs if c.get("text")]
            else:
                logger.info("No layout elements found, using full text extraction.")
                full_text = analysis_result.get("text", "") or ""
                if full_text:
                    chunks = chunk_text_tiktoken(full_text, chunk_size=1000, overlap_tokens=50)

            if not full_text:
                # Keep a copy of full text if we didn't set it via the branch above
                full_text = analysis_result.get("text", "") or ""
        except Exception as e:
            logger.error("Textract analysis failed for %s: %s", file.filename, e)

        # Fallback text extraction if Textract didn't produce chunks
        if not chunks:
            logger.warning(
                "No chunks extracted from %s. Using fallback text chunking.",
                file.filename,
            )
            if full_text:
                chunks = chunk_text_tiktoken(full_text, chunk_size=1000, overlap_tokens=50)
            else:
                raise HTTPException(status_code=500, detail="Could not extract text from PDF.")

        if not chunks:
            raise HTTPException(status_code=500, detail="No text found to index from the PDF.")

        logger.info(
            "Processing %d chunks for document_id: %s, user_id: %s",
            len(chunks),
            document_id,
            user_id,
        )

        async def process_single_chunk(idx: int, chunk: str):
            # Build context using the full document text we extracted (works for both paths)
            context = await generate_chunk_context(
                document_text=full_text,
                chunk=chunk,
                source_type="document",
                logger=logger,
            )

            contextual_chunk = f"{context}\n\n{chunk}"
            # Run synchronous embedding creation in a thread to avoid blocking the event loop
            embedding_response = await run_in_thread(
                openai.embeddings.create,
                input=contextual_chunk,
                model="text-embedding-ada-002",  # consider updating if you have moved to newer models
            )
            embedding = embedding_response.data[0].embedding
            safe_chunk_id = uuid.uuid4().hex

            doc_body = {
                "text": contextual_chunk,
                "embedding": embedding,
                "user_id": user_id,
                "document_id": document_id,
                "chunk_id": safe_chunk_id,
                "chunk_index": idx,
                "original_filename": file.filename,
                "name": file.filename,
                "s3_key": s3_key,
            }

            try:
                # Indexing is also synchronous; run in thread
                response = await run_in_thread(
                    os_client.index,
                    index="openai-embeddings",
                    body=doc_body,  # Let the server assign the document ID
                )
                generated_id = response.get("_id")
                logger.info(
                    "Indexed chunk %d for document %s with id: %s.",
                    idx,
                    document_id,
                    generated_id,
                )
                return response
            except Exception as index_error:
                logger.error("Failed to index chunk %d: %s", idx, index_error)
                raise HTTPException(status_code=500, detail=f"Failed to index chunk: {index_error}") from index_error

        # Process all chunks concurrently
        chunk_tasks = [process_single_chunk(idx, chunk) for idx, chunk in enumerate(chunks)]
        chunk_results = await asyncio.gather(*chunk_tasks, return_exceptions=True)

        # Check for any failed chunks
        failed_chunks = [i for i, result in enumerate(chunk_results) if isinstance(result, Exception)]
        if failed_chunks:
            logger.error("Failed to process %d chunks: %s", len(failed_chunks), failed_chunks)
            raise HTTPException(status_code=500, detail=f"Failed to process {len(failed_chunks)} chunks")

        logger.info(
            "Successfully processed all %d chunks for document %s",
            len(chunks),
            file.filename,
        )
        return JSONResponse(content={"message": "File processed and indexed", "s3_key": s3_key})

    except HTTPException:
        # Bubble up HTTPExceptions as-is
        raise
    except Exception as e:
        logger.exception("Unhandled error while processing %s: %s", file.filename, e)
        raise HTTPException(status_code=500, detail="Internal server error.") from e


async def background_generation_task(generation_id: str, request: GenerateRequest, user_id: str):
    """
    Background task to generate field fill-outs with live progress tracking
    """
    try:
        logger.info("Starting background generation %s for user %s", generation_id, user_id)

        # Update status to in_progress
        generation_tracker.update_generation_status(
            generation_id,
            status="in_progress",
            progress=0,
        )

        # Get appropriate database handler based on request
        db_handler = get_db_handler_for_request(request.dict())

        # Check if the request contains links to scrape and scrape them
        scrapedChunks = []
        if request.links:
            try:
                generation_tracker.update_generation_status(
                    generation_id,
                    current_field="Scraping links",
                    progress=5,
                )
                scrapedChunks = await scrape_links(request.links, logger, os_client)
                logger.info(
                    "Generation %s: Scraped %d chunks from links",
                    generation_id,
                    len(scrapedChunks),
                )
            except Exception as e:
                logger.error("Generation %s: Error scraping links: %s", generation_id, e)
                generation_tracker.update_generation_status(
                    generation_id,
                    status="failed",
                    error_message=f"Error scraping links: {e!s}",
                )
                return

        # Get fields info
        fields = db_handler.get_fields_info()
        total_fields = len(fields)

        generation_tracker.update_generation_status(
            generation_id,
            progress=10,
            current_field=f"Starting generation of {total_fields} fields",
        )

        results: dict = {}
        completed_fields = 0
        all_missing_fields: list[str] = []

        async def generate_field_recursively(
            target_field_id: str,
            target_field_name: str,
            results: dict,
            results_lock: asyncio.Lock,
            depth: int = 0,
            max_depth: int = 5,
        ):
            """
            Recursively generate field fill-outs with progress tracking
            """
            nonlocal completed_fields

            if depth > max_depth:
                logger.warning(
                    "Generation %s: Maximum recursion depth reached for field %s",
                    generation_id,
                    target_field_id,
                )
                return

            async with results_lock:
                if target_field_id in results:
                    logger.info(
                        "Generation %s: Field %s already generated, skipping",
                        generation_id,
                        target_field_id,
                    )
                    return

            logger.info(
                "Generation %s: Generating field %s at depth %d",
                generation_id,
                target_field_id,
                depth,
            )

            # Update current field being processed
            generation_tracker.update_generation_status(
                generation_id,
                current_field=f"{target_field_name} ({target_field_id})",
            )

            # Track missing fields for this specific generation
            current_missing_fields: list[str] = []

            fill_out = await generate_field_fill(
                target_field_id,
                target_field_name,
                request,
                scrapedChunks,
                results,
                os_client,
                logger,
                user_id,
                current_missing_fields,
            )

            # If there are missing fields, generate them first
            if current_missing_fields:
                unique_missing = list(set(current_missing_fields))
                logger.info(
                    "Generation %s: Field %s has dependencies: %s",
                    generation_id,
                    target_field_id,
                    unique_missing,
                )

                # Find field names for missing field IDs
                field_lookup = {field[0]: field[1] for field in fields}

                for missing_field_id in unique_missing:
                    async with results_lock:
                        should_generate = missing_field_id not in results and missing_field_id in field_lookup
                    if should_generate:
                        missing_field_name = field_lookup[missing_field_id]
                        logger.info(
                            "Generation %s: Recursively generating missing field: %s",
                            generation_id,
                            missing_field_id,
                        )
                        await generate_field_recursively(
                            missing_field_id,
                            missing_field_name,
                            results,
                            results_lock,
                            depth + 1,
                            max_depth,
                        )

                # Regenerate the current field now that dependencies are available
                logger.info(
                    "Generation %s: Regenerating field %s after resolving dependencies",
                    generation_id,
                    target_field_id,
                )
                current_missing_fields.clear()  # Reset for regeneration
                fill_out = await generate_field_fill(
                    target_field_id,
                    target_field_name,
                    request,
                    scrapedChunks,
                    results,
                    os_client,
                    logger,
                    user_id,
                    current_missing_fields,
                )

            async with results_lock:
                results[target_field_id] = fill_out.model_dump()
            all_missing_fields.extend(current_missing_fields)

            # Only increment completed_fields for top-level fields (depth 0)
            if depth == 0:
                async with results_lock:
                    completed_fields += 1
                    progress = min(90, 10 + int((completed_fields / total_fields) * 80))  # 10-90% range
                    generation_tracker.update_generation_status(
                        generation_id,
                        progress=progress,
                        completed_fields=completed_fields,
                    )

            unanswered_questions_len = len(fill_out.unanswered_questions or [])
            logger.info(
                "Generation %s: Generated fill-out for field_id %s with %d unanswered questions at depth %d",
                generation_id,
                target_field_id,
                unanswered_questions_len,
                depth,
            )

        # Generate all fields
        # Parallel dependency-aware processing
        metrics = {}
        try:
            # Determine token classification for dependency analysis
            token_cls = request.tokenClassification or "OTH"
            token_cls = "OTH" if token_cls in ["OTH_UTILITY", "OTH_NON_UTILITY"] else token_cls.split("_")[0]

            analyzer = DependencyAnalyzer(token_classification=token_cls)
            analysis = analyzer.analyze_dependencies()
            levels = analyzer.get_execution_levels()
            metrics = {"analysis": analysis, "field_durations": {}}

            field_lookup = {field[0]: field[1] for field in fields}

            semaphore = asyncio.Semaphore(32)
            results_lock = asyncio.Lock()

            async def process_single_field(field_id: str):
                nonlocal completed_fields
                field_name = field_lookup.get(field_id, field_id)
                current_missing_fields: list[str] = []

                # Update current field
                generation_tracker.update_generation_status(
                    generation_id,
                    current_field=f"{field_name} ({field_id})",
                )

                start_time = time.perf_counter()
                async with semaphore:
                    fill_out = await generate_field_fill(
                        field_id,
                        field_name,
                        request,
                        scrapedChunks,
                        results,
                        os_client,
                        logger,
                        user_id,
                        current_missing_fields,
                    )

                # If there are missing dependencies not yet generated, signal for retry
                async with results_lock:
                    unresolved = [m for m in set(current_missing_fields) if m not in results]
                if unresolved:
                    logger.info(
                        "Generation %s: Field %s needs unresolved deps %s; will retry after dependencies",
                        generation_id,
                        field_id,
                        unresolved,
                    )
                    return ("retry", field_id, field_name)

                # Save result
                async with results_lock:
                    results[field_id] = fill_out.model_dump()
                    completed_fields += 1
                    progress = min(90, 10 + int((completed_fields / total_fields) * 80))
                    generation_tracker.update_generation_status(
                        generation_id,
                        progress=progress,
                        completed_fields=completed_fields,
                    )
                    try:
                        duration = time.perf_counter() - start_time
                        # Record field duration in metrics
                        if isinstance(metrics, dict) and "field_durations" in metrics:
                            metrics["field_durations"][field_id] = duration
                        logger.info(
                            "Generation %s: Field %s completed in %.2fs",
                            generation_id,
                            field_id,
                            duration,
                        )
                    except Exception:
                        pass
                return ("done", field_id, field_name)

            # Execute level by level
            for level_index, level_fields in enumerate(levels):
                logger.info(
                    "Generation %s: Processing level %d with %d fields",
                    generation_id,
                    level_index,
                    len(level_fields),
                )
                # Filter to fields that exist in current DB and not already generated
                async with results_lock:
                    runnable = [fid for fid in level_fields if fid in field_lookup and fid not in results]

                # First pass
                tasks = [process_single_field(fid) for fid in runnable]
                results_1 = await asyncio.gather(*tasks, return_exceptions=True)

                # Collect retries due to dynamic dependencies
                retry_ids = []
                for r in results_1:
                    if isinstance(r, tuple) and r[0] == "retry":
                        retry_ids.append((r[1], r[2]))
                    elif isinstance(r, Exception):
                        logger.error(
                            "Generation %s: Error processing field: %s",
                            generation_id,
                            str(r),
                        )

                if retry_ids:
                    logger.info(
                        "Generation %s: Retrying %d fields after resolving dependencies",
                        generation_id,
                        len(retry_ids),
                    )
                    # Retry once
                    async with results_lock:
                        retry_tasks = [process_single_field(fid) for fid, _ in retry_ids if fid not in results]
                    await asyncio.gather(*retry_tasks, return_exceptions=True)

        except Exception as e:
            logger.error(
                "Generation %s: Dependency-based execution failed, falling back to sequential: %s",
                generation_id,
                str(e),
            )
            # Fallback to original sequential logic
            for field in fields:
                field_id = field[0]
                field_name = field[1]
                async with results_lock:
                    if field_id in results:
                        continue
                await generate_field_recursively(field_id, field_name, results, results_lock)

        # ---- Wrap-up, logging, and DataContext payload ----
        async with results_lock:
            results_count = len(results)
        logger.info(
            "Completed /generate process for %s. Fields generated: %d",
            generation_id,
            results_count,
        )

        if all_missing_fields:
            unique_missing_fields = list(set(all_missing_fields))
            logger.warning(
                "Generation %s: Missing fields encountered: %s",
                generation_id,
                unique_missing_fields,
            )
        else:
            logger.info("Generation %s: No missing fields encountered", generation_id)

        # Normalize OTH token classifications to just "OTH"
        normalized_context_type = request.tokenClassification
        if request.tokenClassification in ["OTH_UTILITY", "OTH_NON_UTILITY"]:
            normalized_context_type = "OTH"

        async with results_lock:
            results_copy = results.copy()
            final_completed_fields = completed_fields

        context_data = {
            "contextType": normalized_context_type,
            "scrapedData": results_copy,  # Store the generated field data in scrapedData
            "acceptedFields": [],
            "improvedFields": [],
            "fieldData": {
                "questionnaireData": request.dict(),
                "tokenClassification": normalized_context_type,
                **results_copy,  # Merge the generated results into fieldData as well
            },
        }

        # Final update - completed (store DataContext-style payload with wrapper)
        final_results = {
            "context_data": context_data,
            "metrics": metrics if isinstance(metrics, dict) else {},
        }

        generation_tracker.update_generation_status(
            generation_id,
            status="completed",
            progress=100,
            completed_fields=final_completed_fields,
            current_field="Generation completed",
            results=final_results,
        )

        logger.info(
            "Generation %s: Completed successfully with %d fields",
            generation_id,
            len(results),
        )
        logger.info(f"Generation {generation_id}: {results}")

    except Exception as e:
        logger.error(
            "Generation %s: Fatal error during background generation: %s",
            generation_id,
            str(e),
        )
        generation_tracker.update_generation_status(
            generation_id,
            status="failed",
            error_message=str(e),
        )


@app.post("/generate")
async def generate_endpoint(
    request: GenerateRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
):
    """
    Endpoint to start background generation of field fill-outs for whitepapers.

    Returns a generation_id immediately and processes the generation in the background.
    Use the generation status endpoints to track progress.

    try:
        # Check if user already has an active generation
        active_generation = generation_tracker.get_user_active_generation(current_user["sub"])
        if active_generation:
            # If there is an active generation, update its form with the latest payload
            try:
                updated_form = request.dict(exclude_unset=False, exclude_none=False)
            except Exception:
                updated_form = request.dict()

            generation_tracker.update_generation_status(
                active_generation.generation_id,
                form=updated_form,
            )

            logger.info(
                f"User {current_user['sub']} already has active generation "
                f"{active_generation.generation_id}; updated form and returning "
                "existing generation."
            )
            return JSONResponse(
                content={
                    "generation_id": active_generation.generation_id,
                    "total_fields": active_generation.total_fields,
                    "status": active_generation.status,
                    "message": "A generation is already in progress; updated its form and returned the existing generation_id.",
                }
            )

        JSONResponse: A response with generation_id and total_fields count for progress tracking.
    """
    logger.info("Received /generate request with whitepaperType: %s", request.whitepaperType)

    # Debug: Log the raw request details
    logger.info(f"Raw request object: {request}")
    logger.info(f"Request attributes: {dir(request)}")
    logger.info(f"Request dict: {request.dict()}")
    logger.info(f"Request dict (exclude none/unset false): {request.dict(exclude_unset=False, exclude_none=False)}")

    try:
        # Check if user already has an active generation
        active_generation = generation_tracker.get_user_active_generation(current_user["sub"])
        if active_generation:
            logger.info(f"User {current_user['sub']} already has active generation {active_generation.generation_id}")
            return JSONResponse(
                content={
                    "generation_id": active_generation.generation_id,
                    "total_fields": active_generation.total_fields,
                    "status": active_generation.status,
                    "message": "Generation already in progress. Use the status endpoint to track progress.",
                }
            )

        # Get appropriate database handler based on request to count fields
        db_handler = get_db_handler_for_request(request.dict())
        fields = db_handler.get_fields_info()
        total_fields = len(fields)

        # Debug: Log the form data being saved
        form_data = request.dict(exclude_unset=False, exclude_none=False)
        logger.info(f"Form data to be saved: {json.dumps(form_data, indent=2)[:500]}...")

        # Create new generation tracking entry
        generation_id = generation_tracker.create_generation(
            user_id=current_user["sub"],
            total_fields=total_fields,
            whitepaper_type=request.whitepaperType,
            form=form_data,  # Save the entire form data
        )

        # Start background generation task
        background_tasks.add_task(
            background_generation_task,
            generation_id=generation_id,
            request=request,
            user_id=current_user["sub"],
        )

        logger.info(f"Started background generation {generation_id} for user {current_user['sub']} with {total_fields} fields")

        return JSONResponse(
            content={
                "generation_id": generation_id,
                "total_fields": total_fields,
                "status": "pending",
                "message": "Generation started in background. Use the status endpoint to track progress.",
            }
        )

    except Exception as e:
        logger.error(f"Error starting generation for user {current_user['sub']}: {e!s}")
        raise HTTPException(status_code=500, detail=f"Error starting generation: {e!s}") from e


@app.post("/regenerate")
async def regenerate_endpoint(
    request: RegenerateRequest,
    current_user: dict = Depends(get_current_user),  # noqa: ARG001
):
    """
    Endpoint to regenerate field fill-outs based on provided information.

    This function takes the field ID, field name, field text, unanswered questions, and answers from the request body,
    and generates a new fill-out for the specified field.

    Args:
        request (FieldFillResponse): The request body containing information for regenerating the field fill-out.

    Returns:
        JSONResponse: A response with the regenerated field fill-out.
    """
    logger.info("Received /regenerate request for field_id: %s", request.field_id)

    result: FieldFillResponse = await regenerate_field_fill(request)
    # Regenerate logic here
    # For now, we will just return the same data
    return JSONResponse(content=result.model_dump())


@app.post("/follow-up-questions")
async def get_follow_up_questions(
    request: FollowUpQuestionRequest,
    current_user: dict = Depends(get_current_user),  # noqa: ARG001
):
    """
    Endpoint to generate follow-up questions based on field information.

    Args:
        request (FollowUpQuestionRequest): Field information.

    Returns:
        JSONResponse: A list of follow-up questions.
    """
    logger.info(f"Received follow-up question request for field: {request.fieldKey}")

    questions = [
        f"Could you provide more details about {request.fieldTitle}?",
        f"Is there any specific information about {request.fieldTitle} mentioned in the document?",
    ]

    if request.fieldKey == "A.06":
        questions.append("Is the Legal Entity Identifier (LEI) in the correct format per ISO 17442?")
    elif request.fieldKey == "A.15":
        questions.append("Is there information about when the entity was established?")
    elif request.fieldKey == "I.08":
        questions.append("What specific characteristics of the crypto-asset are mentioned in the document?")
    questions.append("Is there any additional information in the document that could be relevant for this field?")
    return JSONResponse(content={"questions": questions})


@app.post("/dummy")
def dummy_endpoint(
    request: GenerateRequest,
    current_user: dict = Depends(get_current_user),  # noqa: ARG001
):
    """
    Dummy endpoint for testing purposes.
    Accepts form data but returns static dummy data.
    """
    logger.info(f"Received form data at /dummy endpoint: {request.dict()}")
    return JSONResponse(content=DUMMY_DATA)


@app.post("/public/dummy")
def public_dummy_endpoint(request: GenerateRequest):
    """
    Public dummy endpoint for testing purposes without authentication.
    Accepts form data but returns static dummy data.
    """
    logger.info(f"Received form data at /public/dummy endpoint: {request.dict()}")
    return JSONResponse(content=DUMMY_DATA)


@app.get("/api/sections/{section_number}/fields")
async def get_section_fields(
    section_number: int,
    token_type: str = "OTH",
    current_user: dict = Depends(get_current_user),  # noqa: ARG001
):
    """Get all fields for a specific section"""
    # Ensure token_type is uppercase for consistency
    token_type = token_type.upper() if token_type else "OTH"

    logger.info(f"API request: get_section_fields with section_number={section_number}, token_type={token_type}")

    try:
        fields = get_whitepaper_fields_by_section(token_type, section_number)
        return {"fields": fields}
    except HTTPException as e:
        logger.error(f"Unexpected error in get_section_fields: {e!s}")
        # Preserve original HTTP status (e.g., 404/403)
        raise e
    except Exception as e:
        logger.error(f"Database error in get_section_fields: {e!s}")
        raise HTTPException(status_code=500, detail=f"Database error for {token_type}: {e!s}") from e


async def get_section_field(
    section_number: int,
    field_id: str,
    token_type: str = "OTH",
    current_user: dict = Depends(get_current_user),  # noqa: ARG001
):
    """Get a specific field by ID within a section"""
    db_handler = DatabaseHandler(token_classification=token_type)
    field = db_handler.get_section_field_by_id(section_number, field_id)
    if not field:
        raise HTTPException(
            status_code=404,
            detail=f"Field {field_id} not found in section {section_number}",
        )
    return {"field": field}


@app.get("/api/all-section-fields")
async def get_all_section_fields(
    token_type: str = "OTH",
    current_user: dict = Depends(get_current_user),  # noqa: ARG001
):
    """Get all fields from all sections"""
    db_handler = DatabaseHandler(token_classification=token_type)
    all_fields = db_handler.get_all_section_fields()
    return {"sections": all_fields}


@app.post("/api/lei-lookup")
async def lei_lookup(
    request: LEIRequest,
    current_user: dict = Depends(get_current_user),  # noqa: ARG001
):
    """
    Endpoint to lookup LEI (Legal Entity Identifier) information from GLEIF API.
    Includes parent company information from direct parent relationships.

    Args:
        request (LEIRequest): The request containing the LEI number to lookup.

    Returns:
        JSONResponse: The LEI record information with parent company data if found.
    """
    logger.info(f"Received LEI lookup request for: {request.lei}")

    # Fetch main LEI record
    url = f"https://api.gleif.org/api/v1/lei-records/{request.lei}"
    headers = {"Accept": "application/vnd.api+json"}

    try:
        response = requests.request("GET", url, headers=headers, data={})

        if response.status_code == 200:
            lei_data = response.json()

            # Try to fetch parent company information from relationships
            parent_company_name = None
            try:
                # First try the relationships endpoint
                relationships_url = f"https://api.gleif.org/api/v1/lei-records/{request.lei}/direct-parent-relationships"
                relationships_response = requests.request("GET", relationships_url, headers=headers, data={})

                if relationships_response.status_code == 200:
                    relationships_data = relationships_response.json()
                    if relationships_data.get("data"):
                        for relationship in relationships_data["data"]:
                            if relationship.get("attributes", {}).get("relationshipType") == "DIRECT_ACCOUNTING_CONSOLIDATING_PARENT":
                                parent_lei = relationship.get("attributes", {}).get("startNode", {}).get("id")
                                if parent_lei:
                                    # Fetch parent entity details
                                    parent_url = f"https://api.gleif.org/api/v1/lei-records/{parent_lei}"
                                    parent_response = requests.request("GET", parent_url, headers=headers, data={})
                                    if parent_response.status_code == 200:
                                        parent_data = parent_response.json()
                                        if parent_data.get("data", {}).get("attributes", {}).get("entity", {}).get("legalName"):
                                            parent_company_name = parent_data["data"]["attributes"]["entity"]["legalName"]["name"]
                                            logger.info(f"Found parent company: {parent_company_name}")
                                            break

                # If no direct relationships found, try the include parameter approach
                if not parent_company_name:
                    include_url = f"https://api.gleif.org/api/v1/lei-records/{request.lei}?include=relationships"
                    include_response = requests.request("GET", include_url, headers=headers, data={})

                    if include_response.status_code == 200:
                        include_data = include_response.json()
                        if include_data.get("included"):
                            for included_item in include_data["included"]:
                                if included_item.get("type") == "direct-parent-relationships":
                                    relationship = included_item.get("attributes", {})
                                    if relationship.get("relationshipType") == "DIRECT_ACCOUNTING_CONSOLIDATING_PARENT":
                                        parent_lei = relationship.get("startNode", {}).get("id")
                                        if parent_lei:
                                            # Fetch parent entity details
                                            parent_url = f"https://api.gleif.org/api/v1/lei-records/{parent_lei}"
                                            parent_response = requests.request(
                                                "GET",
                                                parent_url,
                                                headers=headers,
                                                data={},
                                            )
                                            if parent_response.status_code == 200:
                                                parent_data = parent_response.json()
                                                if parent_data.get("data", {}).get("attributes", {}).get("entity", {}).get("legalName"):
                                                    parent_company_name = parent_data["data"]["attributes"]["entity"]["legalName"]["name"]
                                                    logger.info(f"Found parent company via include: {parent_company_name}")
                                                    break
            except Exception as e:
                logger.warning(f"Could not fetch parent company information: {e!s}")

            # Add parent company information to the response
            if parent_company_name:
                if "data" not in lei_data:
                    lei_data["data"] = {}
                if "attributes" not in lei_data["data"]:
                    lei_data["data"]["attributes"] = {}
                lei_data["data"]["attributes"]["parentCompanyName"] = parent_company_name

            return JSONResponse(content=lei_data)
        elif response.status_code == 404:
            logger.warning(f"LEI {request.lei} not found in GLEIF database")
            return JSONResponse(
                content={
                    "error": "LEI not found",
                    "message": (
                        f"The LEI number '{request.lei}' could not be found in the GLEIF database. Please verify the LEI number and try again."
                    ),
                }
            )
        else:
            logger.error(f"LEI lookup failed with status code: {response.status_code}")
            return JSONResponse(
                status_code=response.status_code,
                content={
                    "error": "LEI lookup failed",
                    "message": f"The GLEIF API returned an error with status code: {response.status_code}",
                    "detail": response.text,
                },
            )
    except Exception as e:
        logger.error(f"Error during LEI lookup: {e!s}")
        return JSONResponse(
            status_code=500,
            content={
                "error": "Internal server error",
                "message": f"An error occurred while looking up the LEI: {e!s}",
            },
        )


@app.post("/api/elf-lookup")
async def elf_lookup(
    request: ELFRequest,
    current_user: dict = Depends(get_current_user),  # noqa: ARG001
):
    """
    Endpoint to lookup ELF (Entity Legal Form) information from the local CSV database.

    Args:
        request (ELFRequest): The request containing the ELF code to lookup.

    Returns:
        JSONResponse: The ELF record information if found.
    """
    logger.info(f"Received ELF lookup request for: {request.elf_code}")

    try:
        import csv
        from pathlib import Path

        # Get the path to the ELF CSV file
        elf_csv_path = Path(__file__).parent / "data" / "databases" / "elf_list.csv"

        if not elf_csv_path.exists():
            logger.error(f"ELF CSV file not found at: {elf_csv_path}")
            return JSONResponse(
                status_code=500,
                content={
                    "error": "ELF database not found",
                    "message": "The ELF database file could not be found on the server.",
                },
            )

        # Search for the ELF code in the CSV
        with open(elf_csv_path, encoding="utf-8") as file:
            csv_reader = csv.DictReader(file)

            # Debug: Print the actual fieldnames
            logger.info(f"CSV fieldnames: {csv_reader.fieldnames}")

            for row in csv_reader:
                # Debug: Print the row keys for the first row
                if csv_reader.line_num == 2:  # First data row (after header)
                    logger.info(f"Row keys: {list(row.keys())}")

                # Try to find the ELF Code column with different possible names
                elf_code_value = None
                for key in row:
                    if "ELF Code" in key or "elf code" in key.lower():
                        elf_code_value = row[key]
                        logger.debug(f"Found ELF Code column: '{key}' with value: '{elf_code_value}'")
                        break

                if elf_code_value and elf_code_value.strip().upper() == request.elf_code.strip().upper():
                    logger.info(f"Found ELF record for code: {request.elf_code}")

                    # Helper function to safely get values from row
                    def get_value(possible_keys, row_data):
                        for key in possible_keys:
                            for row_key in row_data:
                                if key in row_key:
                                    return row_data[row_key]
                        return ""

                    return JSONResponse(
                        content={
                            "success": True,
                            "data": {
                                "elf_code": elf_code_value,
                                "country_of_formation": get_value(["Country of formation"], row),
                                "country_code": get_value(["Country Code", "ISO 3166-1"], row),
                                "jurisdiction": get_value(["Jurisdiction of formation"], row),
                                "entity_legal_form_name": get_value(["Entity Legal Form name Local name"], row),
                                "language": get_value(["Language"], row),
                                "language_code": get_value(["Language Code", "ISO 639-1"], row),
                                "transliterated_name": get_value(["Entity Legal Form name Transliterated name"], row),
                                "abbreviations_local": get_value(["Abbreviations Local language"], row),
                                "abbreviations_transliterated": get_value(["Abbreviations transliterated"], row),
                                "date_created": get_value(["Date created YYYY-MM-DD"], row),
                                "status": get_value(["ELF Status ACTV/INAC"], row),
                                "modification": get_value(["Modification"], row),
                                "modification_date": get_value(["Modification date YYYY-MM-DD"], row),
                                "reason": get_value(["Reason"], row),
                            },
                        }
                    )

        # If we get here, the ELF code was not found
        logger.warning(f"ELF code {request.elf_code} not found in database")
        return JSONResponse(
            status_code=404,
            content={
                "error": "ELF not found",
                "message": f"The ELF code '{request.elf_code}' could not be found in the database. Please verify the ELF code and try again.",
            },
        )

    except Exception as e:
        logger.error(f"Error during ELF lookup: {e!s}")
        return JSONResponse(
            status_code=500,
            content={
                "error": "Internal server error",
                "message": f"An error occurred while looking up the ELF code: {e!s}",
            },
        )


@app.post("/api/user-context", response_model=UserContextResponse)
async def save_user_context(
    request: UserContextRequest,
    current_user: dict = Depends(get_current_user),  # noqa: ARG001
):
    """Save or update user context data"""
    try:
        # Connect to database (SQLite or Postgres)
        with connect(settings.DATA_CONTEXT_DB) as conn:
            cursor = conn.cursor()

            # Create table if it doesn't exist (use Postgres-compatible DDL when enabled)
            if is_postgres_enabled():
                cursor.execute(
                    """
                CREATE TABLE IF NOT EXISTS user_context (
                    id SERIAL PRIMARY KEY,
                    auth0_user_id TEXT NOT NULL,
                    context_data TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
                )
            else:
                cursor.execute(
                    """
                CREATE TABLE IF NOT EXISTS user_context (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    auth0_user_id TEXT NOT NULL,
                    context_data TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
                )

            # Wrap the context data with the "context_data" key to match expected format
            wrapped_data = {"context_data": request.context_data}

            # Convert data to JSON string
            context_data_json = json.dumps(wrapped_data)
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Check if user already has context data
            cur = execute(
                conn,
                "SELECT id FROM user_context WHERE auth0_user_id = ?",
                (request.auth0_user_id,),
            )
            existing_user = cur.fetchone()

            if existing_user:
                # Update existing record
                execute(
                    conn,
                    "UPDATE user_context SET context_data = ?, updated_at = ? WHERE auth0_user_id = ?",
                    (context_data_json, current_time, request.auth0_user_id),
                )
            else:
                # Insert new record
                execute(
                    conn,
                    "INSERT INTO user_context (auth0_user_id, context_data, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (
                        request.auth0_user_id,
                        context_data_json,
                        current_time,
                        current_time,
                    ),
                )
                logger.info(f"Created new context data for user: {request.auth0_user_id}")

                # Commit/close handled by context manager

        return UserContextResponse(
            auth0_user_id=request.auth0_user_id,
            message="Context data saved successfully",
        )
    except Exception as e:
        logger.error(f"Error saving user context: {e!s}")
        raise HTTPException(status_code=500, detail=f"Failed to save context data: {e!s}") from e


@app.get("/api/user-context/{auth0_user_id}", response_model=UserContextResponse)
async def get_user_context(
    auth0_user_id: str,
    current_user: dict = Depends(get_current_user),  # noqa: ARG001
):
    """Get context data for a specific user"""
    logger.info(f"Getting context data for user: {auth0_user_id}")

    try:
        # Connect to database (SQLite or Postgres)
        with connect(settings.DATA_CONTEXT_DB) as conn:
            cursor = conn.cursor()

            # Create table if it doesn't exist (use Postgres-compatible DDL when enabled)
            if is_postgres_enabled():
                cursor.execute(
                    """
                CREATE TABLE IF NOT EXISTS user_context (
                    id SERIAL PRIMARY KEY,
                    auth0_user_id TEXT NOT NULL,
                    context_data TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
                )
            else:
                cursor.execute(
                    """
                CREATE TABLE IF NOT EXISTS user_context (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    auth0_user_id TEXT NOT NULL,
                    context_data TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
                )

            # Query for user context data
            cur = execute(
                conn,
                "SELECT context_data FROM user_context WHERE auth0_user_id = ?",
                (auth0_user_id,),
            )
            result = cur.fetchone()

        if result:
            # Support both tuple/row and dict_row
            try:
                context_json = result[0]
            except Exception:
                context_json = result.get("context_data") if isinstance(result, dict) else dict(result).get("context_data")
            context_data = json.loads(context_json) if context_json else {}
            logger.info(f"Retrieved context data for user: {auth0_user_id}")
            return UserContextResponse(
                auth0_user_id=auth0_user_id,
                context_data=context_data,
                message="Context data retrieved successfully",
            )
        else:
            logger.info(f"No context data found for user: {auth0_user_id}")
            return UserContextResponse(
                auth0_user_id=auth0_user_id,
                message="No context data found for this user",
            )
    except Exception as e:
        logger.error(f"Error retrieving user context: {e!s}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve context data: {e!s}") from e


@app.get("/api/user/whitepapers")
async def get_user_whitepapers(current_user: dict = Depends(get_current_user)):
    """Get all completed whitepapers for the current user"""
    logger.info(f"Getting whitepapers for user {current_user['sub']}")

    try:
        # Connect to database
        with connect(settings.DATA_CONTEXT_DB) as conn:
            cur = execute(
                conn,
                """
                SELECT generation_id, results, started_at, updated_at,
                       whitepaper_type, status, error_message, progress,
                       completed_fields, total_fields, form
                FROM generation_status
                WHERE user_id = ? AND status IN ('completed', 'failed', 'pending', 'in_progress')
                ORDER BY updated_at DESC
            """,
                (current_user["sub"],),
            )
            rows = cur.fetchall()

        whitepapers = []
        for row in rows:
            # Support both tuple/row and dict_row
            if isinstance(row, dict):
                generation_id = row.get("generation_id")
                results_json = row.get("results")
                started_at = row.get("started_at")
                updated_at = row.get("updated_at")
                whitepaper_type = row.get("whitepaper_type")
                status = row.get("status")
                error_message = row.get("error_message")
                progress = row.get("progress")
                completed_fields = row.get("completed_fields")
                total_fields = row.get("total_fields")
                form_json = row.get("form")
            else:
                (
                    generation_id,
                    results_json,
                    started_at,
                    updated_at,
                    whitepaper_type,
                    status,
                    error_message,
                    progress,
                    completed_fields,
                    total_fields,
                    form_json,
                ) = row
            try:
                results = json.loads(results_json) if results_json else {}
                form = json.loads(form_json) if form_json else None
                # Extract some basic info about the whitepaper
                whitepaper_info = {
                    "generation_id": generation_id,
                    "results": results,
                    "form": form,
                    "created_at": started_at,
                    "updated_at": updated_at,
                    "whitepaper_type": whitepaper_type,
                    "status": status,
                    "title": (f"Whitepaper {generation_id[:8]}..." if generation_id else "Untitled"),
                }

                # Add progress info for active generations
                if status in ("pending", "in_progress"):
                    whitepaper_info["progress"] = progress or 0
                    whitepaper_info["completed_fields"] = completed_fields or 0
                    whitepaper_info["total_fields"] = total_fields or 0

                # Add error message for failed generations
                if status == "failed":
                    whitepaper_info["error_message"] = error_message

                whitepapers.append(whitepaper_info)
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse results for generation {generation_id}")
                continue

        # Use jsonable_encoder to handle datetime and other non-JSON-serializable types
        return JSONResponse(content=jsonable_encoder({"whitepapers": whitepapers}))

    except Exception as e:
        logger.error(f"Error fetching user whitepapers: {e!s}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch whitepapers: {e!s}") from e


@app.delete("/api/whitepaper/{generation_id}")
async def delete_whitepaper(generation_id: str, current_user: dict = Depends(get_current_user)):
    """Delete a whitepaper generation for the current user"""
    logger.info(f"Deleting whitepaper {generation_id} for user {current_user['sub']}")

    try:
        # Connect to database
        with connect(settings.DATA_CONTEXT_DB) as conn:
            # Check if generation exists and belongs to user
            cur = execute(
                conn,
                """
                SELECT user_id FROM generation_status
                WHERE generation_id = ?
            """,
                (generation_id,),
            )

            result = cur.fetchone()

            user_id_val = None
            if result:
                try:
                    user_id_val = result[0]
                except Exception:
                    user_id_val = result.get("user_id") if isinstance(result, dict) else dict(result).get("user_id")

            if not result:
                raise HTTPException(status_code=404, detail="Whitepaper not found")

            if user_id_val != current_user["sub"]:
                raise HTTPException(status_code=403, detail="Access denied to this whitepaper")

            # Delete the generation
            execute(
                conn,
                """
                DELETE FROM generation_status
                WHERE generation_id = ?
            """,
                (generation_id,),
            )

        logger.info(f"Successfully deleted whitepaper {generation_id}")
        return JSONResponse(content={"message": "Whitepaper deleted successfully"})

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting whitepaper: {e!s}")
        raise HTTPException(status_code=500, detail=f"Failed to delete whitepaper: {e!s}") from e


@app.post("/api/whitepaper/{generation_id}/save", response_model=UserContextResponse)
async def save_whitepaper_progress(
    generation_id: str,
    request: UserContextRequest,
    current_user: dict = Depends(get_current_user),  # noqa: ARG001
):
    """Save progress to a specific whitepaper"""
    logger.info(f"Saving progress to whitepaper {generation_id} for user: {request.auth0_user_id}")

    try:
        # Connect to the generation database
        with connect(settings.DATA_CONTEXT_DB) as conn:
            # Update the results for the specific generation
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Check if generation exists and belongs to user
            cur = execute(
                conn,
                "SELECT user_id, results FROM generation_status WHERE generation_id = ?",
                (generation_id,),
            )
            result = cur.fetchone()

            if not result:
                raise HTTPException(status_code=404, detail="Generation not found")

            # Extract user_id and existing_results regardless of row type
            try:
                result_user_id = result[0]
                existing_results = result[1]
            except Exception:
                result_user_id = result.get("user_id") if isinstance(result, dict) else dict(result).get("user_id")
                existing_results = result.get("results") if isinstance(result, dict) else dict(result).get("results")

            if result_user_id != request.auth0_user_id:
                raise HTTPException(status_code=403, detail="Access denied to this generation")

            # Get existing results and merge with new context data
            if existing_results:
                try:
                    existing_data = json.loads(existing_results)
                    # If we have existing context_data, preserve the scrapedData (generated fields)
                    if "context_data" in existing_data:
                        existing_context = existing_data["context_data"]
                        new_context = request.context_data

                        # Preserve existing scrapedData (generated fields) but update other data
                        merged_context = {
                            "contextType": new_context.get("contextType", existing_context.get("contextType")),
                            "scrapedData": existing_context.get("scrapedData", {}),  # Keep generated fields
                            "acceptedFields": new_context.get("acceptedFields", []),
                            "improvedFields": new_context.get("improvedFields", []),
                            "fieldData": new_context.get("fieldData", {}),
                        }
                        final_results = {"context_data": merged_context}
                    else:
                        # No existing context_data, use new data
                        final_results = {"context_data": request.context_data}
                except json.JSONDecodeError:
                    # Invalid existing JSON, use new data
                    final_results = {"context_data": request.context_data}
            else:
                # No existing results, use new data
                final_results = {"context_data": request.context_data}

            # Update the results with merged context data (always update)
            execute(
                conn,
                "UPDATE generation_status SET results = ?, updated_at = ? WHERE generation_id = ?",
                (json.dumps(final_results), current_time, generation_id),
            )

        logger.info(f"Successfully saved progress to whitepaper {generation_id}")
        return UserContextResponse(
            auth0_user_id=request.auth0_user_id,
            message=f"Progress saved to whitepaper {generation_id} successfully",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error saving whitepaper progress: {e!s}")
        raise HTTPException(status_code=500, detail=f"Failed to save whitepaper progress: {e!s}") from e


@app.get("/api/whitepaper/{generation_id}/form")
async def get_whitepaper_form(generation_id: str, current_user: dict = Depends(get_current_user)):
    """Get the form data for a specific whitepaper"""
    logger.info(f"Getting form data for whitepaper {generation_id} for user: {current_user['sub']}")

    try:
        # Connect to the database
        with connect(settings.DATA_CONTEXT_DB) as conn:
            cur = execute(
                conn,
                "SELECT user_id, form FROM generation_status WHERE generation_id = ?",
                (generation_id,),
            )
            result = cur.fetchone()

        if not result:
            raise HTTPException(status_code=404, detail="Whitepaper not found")

        # Extract user_id and form regardless of row type
        try:
            result_user_id = result[0]
            form_json = result[1]
        except Exception:
            result_user_id = result.get("user_id") if isinstance(result, dict) else dict(result).get("user_id")
            form_json = result.get("form") if isinstance(result, dict) else dict(result).get("form")

        if result_user_id != current_user["sub"]:
            raise HTTPException(status_code=403, detail="Access denied to this whitepaper")

        form_data = json.loads(form_json) if form_json else None
        return JSONResponse(content={"form": form_data})

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting whitepaper form: {e!s}")
        raise HTTPException(status_code=500, detail=f"Failed to get whitepaper form: {e!s}") from e


@app.post("/api/generation/{generation_id}/form")
async def save_generation_form(generation_id: str, request: dict, current_user: dict = Depends(get_current_user)):
    """Save the form data for a specific generation"""
    logger.info(f"Saving form data for generation {generation_id} for user: {current_user['sub']}")

    try:
        # Connect to the database
        with connect(settings.DATA_CONTEXT_DB) as conn:
            # Check if generation exists and belongs to user
            cur = execute(
                conn,
                "SELECT user_id FROM generation_status WHERE generation_id = ?",
                (generation_id,),
            )
            result = cur.fetchone()

            if not result:
                raise HTTPException(status_code=404, detail="Generation not found")

            try:
                result_user_id = result[0]
            except Exception:
                result_user_id = result.get("user_id") if isinstance(result, dict) else dict(result).get("user_id")

            if result_user_id != current_user["sub"]:
                raise HTTPException(status_code=403, detail="Access denied to this generation")

            # Update the form data
            form_json = json.dumps(request.get("form", {}))
            execute(
                conn,
                "UPDATE generation_status SET form = ?, updated_at = ? WHERE generation_id = ?",
                (form_json, datetime.now().isoformat(), generation_id),
            )

        logger.info(f"Form data saved successfully for generation {generation_id}")
        return JSONResponse(content={"message": "Form data saved successfully"})

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error saving generation form: {e!s}")
        raise HTTPException(status_code=500, detail=f"Failed to save generation form: {e!s}") from e


@app.post("/api/whitepaper/{generation_id}/reset", response_model=UserContextResponse)
async def reset_whitepaper_progress(generation_id: str, current_user: dict = Depends(get_current_user)):
    """Reset progress for a specific whitepaper by clearing user_context"""
    logger.info(f"Resetting progress for whitepaper {generation_id} for user: {current_user['sub']}")

    try:
        # Connect to the database
        with connect(settings.DATA_CONTEXT_DB) as conn:
            cursor = conn.cursor()

            # Check if generation exists and belongs to user
            cur = execute(
                conn,
                "SELECT user_id FROM generation_status WHERE generation_id = ?",
                (generation_id,),
            )
            result = cur.fetchone()

            if not result:
                raise HTTPException(status_code=404, detail="Generation not found")

            try:
                result_user_id = result[0]
            except Exception:
                result_user_id = result.get("user_id") if isinstance(result, dict) else dict(result).get("user_id")

            if result_user_id != current_user["sub"]:
                raise HTTPException(status_code=403, detail="Access denied to this generation")

            # Ensure user_context table exists with proper DDL per backend
            if is_postgres_enabled():
                cursor.execute(
                    """
                CREATE TABLE IF NOT EXISTS user_context (
                    id SERIAL PRIMARY KEY,
                    auth0_user_id TEXT NOT NULL,
                    context_data TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
                )
            else:
                cursor.execute(
                    """
                CREATE TABLE IF NOT EXISTS user_context (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    auth0_user_id TEXT NOT NULL,
                    context_data TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
                )

            # Clear the user_context for this user (general context reset)
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            empty_context = {
                "contextType": None,
                "scrapedData": {},
                "acceptedFields": [],
                "improvedFields": [],
                "fieldData": {},
            }

            # Update or insert empty context in user_context table
            context_data_json = json.dumps(empty_context)

            # Check if user already has context data
            cur2 = execute(
                conn,
                "SELECT id FROM user_context WHERE auth0_user_id = ?",
                (current_user["sub"],),
            )
            existing_user = cur2.fetchone()

            if existing_user:
                # Update existing record
                execute(
                    conn,
                    "UPDATE user_context SET context_data = ?, updated_at = ? WHERE auth0_user_id = ?",
                    (context_data_json, current_time, current_user["sub"]),
                )
            else:
                # Insert new record
                execute(
                    conn,
                    "INSERT INTO user_context (auth0_user_id, context_data, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (
                        current_user["sub"],
                        context_data_json,
                        current_time,
                        current_time,
                    ),
                )

        logger.info(f"Successfully reset progress for whitepaper {generation_id}")
        return UserContextResponse(
            auth0_user_id=current_user["sub"],
            message=f"Progress reset for whitepaper {generation_id} successfully",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error resetting whitepaper progress: {e!s}")
        raise HTTPException(status_code=500, detail=f"Failed to reset whitepaper progress: {e!s}") from e


@app.on_event("startup")
async def startup_event():
    logger.info("Initializing database tables...")
    from app.core.db_init import init_all_tables

    try:
        init_all_tables()
        logger.info("Database tables initialized successfully")
    except Exception as e:
        logger.error(f"Error initializing database tables: {e}")

    logger.info("Validating core JSON datasets...")

    preflight_json_validation()

    try:
        logger.info(f"Attempting to load DTI data from: {settings.DTI_DATA_JSON}")

        if os.path.exists(settings.DTI_DATA_JSON):
            logger.info(f"File exists at {settings.DTI_DATA_JSON}, attempting to read")
            with open(settings.DTI_DATA_JSON) as f:
                loaded_data = json.load(f)
                set_dti_data(loaded_data)
                record_count = get_dti_record_count()
                logger.info(f"Successfully loaded DTI data from {settings.DTI_DATA_JSON} with {record_count} records")
        else:
            logger.warning(f"DTI data file does not exist at: {settings.DTI_DATA_JSON}")
            set_dti_data({"records": []})

    except Exception as e:
        logger.error(f"Error during DTI data loading: {e!s}")
        set_dti_data({"records": []})

    # Log the number of DTI records loaded
    record_count = get_dti_record_count()
    logger.info(f"Loaded {record_count} DTI records")


@app.get("/api/dti/search")
async def search_dti(query: str = "", type: str = "012"):
    """
    Search for DTIs by query string
    Type parameter can be:
    - "012" for regular DTIs (types 0, 1, 2)
    - "3" for functionally fungible DTIs (type 3)
    """
    try:
        # Load DTI data from the JSON file
        with open(settings.DTI_DATA_JSON) as f:
            data = json.load(f)

        results = []
        dti_types_to_include = []

        # Parse the type parameter
        if type == "3":
            dti_types_to_include = [3]  # Only include type 3 (functionally fungible)
        elif type == "012":
            dti_types_to_include = [0, 1, 2]  # Include regular types 0, 1, 2
        else:
            # If invalid type parameter, default to all types
            dti_types_to_include = [0, 1, 2, 3]

        # Filter DTIs based on query and type
        if query:
            query_lower = query.lower()
            for record in data.get("records", []):
                if not record.get("Header") or record["Header"].get("DTIType") is None:
                    continue
                # Check if this DTI is of a type we want to include
                dti_type = record["Header"]["DTIType"]
                if dti_type not in dti_types_to_include:
                    continue

                # Check for matches in DTI code
                dti_code = record["Header"].get("DTI", "")
                if query_lower in dti_code.lower():
                    results.append(record)
                    continue

                # Check for matches in long name
                if record.get("Informative") and record["Informative"].get("LongName"):
                    long_name = record["Informative"]["LongName"]
                    if query_lower in long_name.lower():
                        results.append(record)
                        continue

                # Check for matches in short names
                if record.get("Informative") and record["Informative"].get("ShortNames"):
                    for short_name_obj in record["Informative"]["ShortNames"]:
                        short_name = short_name_obj.get("ShortName", "")
                        if query_lower in short_name.lower():
                            results.append(record)
                            break

        return {"results": results}
    except Exception as e:
        return {"error": str(e), "results": []}


@app.get("/api/dti/{dti}")
async def get_dti(dti: str):
    """
    Get a specific DTI record by DTI code
    """
    logger.info(f"DTI lookup request received for DTI: {dti}")

    # Check if DTI data is available using thread-safe function
    records = get_dti_records()
    if not records:
        logger.error("DTI data is not loaded or is empty")
        raise HTTPException(status_code=500, detail="DTI data not available")

    try:
        for record in records:
            if record.get("Header", {}).get("DTI") == dti:
                logger.info(f"DTI found: {dti}")
                return record

        logger.warning(f"DTI not found: {dti}")
        raise HTTPException(status_code=404, detail="DTI not found")
    except Exception as e:
        logger.error(f"Error during DTI lookup: {e!s}")
        raise HTTPException(status_code=500, detail=f"Error during DTI lookup: {e!s}") from e


@app.get("/files")
async def get_user_files(current_user: dict = Depends(get_current_user)):
    """Get list of user's files"""
    try:
        logger.info(f"Files endpoint called - current_user: {current_user}")

        user_id = current_user.get("sub")
        if not user_id:
            logger.error("User ID not found in token")
            raise HTTPException(status_code=400, detail="User ID not found in token")

        logger.info(f"Retrieving files for user: {user_id}")

        # Only return files that have at least one document in the index
        query = {
            "size": 0,
            "query": {"term": {"user_id": user_id}},
            "aggs": {"unique_files": {"terms": {"field": "name.keyword", "size": 1000}}},
        }

        try:
            response = os_client.search(index="openai-embeddings", body=query)
            logger.info(f"Files aggregation: {response.get('aggregations', {})}")
        except Exception as debug_error:
            logger.warning(f"Files query failed: {debug_error}")
            response = {}

        files = []
        # For each file, check if there are still documents for this user and file name
        if "aggregations" in response and "unique_files" in response["aggregations"]:
            for bucket in response["aggregations"]["unique_files"]["buckets"]:
                file_name = bucket["key"]
                # Double-check with a filter query to ensure no stale buckets
                file_query = {
                    "size": 0,
                    "query": {
                        "bool": {
                            "must": [
                                {"term": {"user_id": user_id}},
                                {"term": {"name.keyword": file_name}},
                            ]
                        }
                    },
                }
                file_response = os_client.search(index="openai-embeddings", body=file_query)
                actual_count = file_response.get("hits", {}).get("total", {}).get("value", 0)
                if actual_count > 0:
                    files.append(file_name)
                    logger.info(f"File '{file_name}' has {actual_count} docs, included in list.")
                else:
                    logger.info(f"File '{file_name}' has 0 docs, excluded from list.")

        logger.info(f"Found {len(files)} unique files for user: {user_id}")
        logger.info(f"Files: {files}")
        return {"files": files, "count": len(files)}

    except HTTPException as http_exc:
        logger.error(f"HTTP error in get_user_files: {http_exc.detail}")
        raise http_exc
    except Exception as e:
        logger.error(f"Error retrieving files: {e!s}")
        raise HTTPException(status_code=500, detail=f"Error retrieving files: {e!s}") from e


@app.delete("/files/{file_id}")
async def delete_user_file(file_id: str, current_user: dict = Depends(get_current_user)):
    """Delete a specific file"""
    user_id = current_user.get("sub")
    logger.info(f"Deleting file {file_id} for user: {user_id}")

    try:
        # First, find all documents for this user and file
        search_query = {
            "query": {
                "bool": {
                    "must": [
                        {"term": {"user_id": user_id}},
                        {"term": {"name.keyword": file_id}},
                    ]
                }
            },
            "size": 10000,  # Increase size to handle large files
            "_source": ["s3_key"],  # Get s3_key to know which file to delete from S3
        }

        try:
            search_response = await run_in_thread(os_client.search, index="openai-embeddings", body=search_query)
            documents_to_delete = search_response["hits"]["hits"]
            logger.info(f"Found {len(documents_to_delete)} documents to delete for file {file_id}")

            # Extract S3 key from first document (all chunks have the same s3_key)
            s3_key_to_delete = None
            if len(documents_to_delete) > 0:
                s3_key_to_delete = documents_to_delete[0].get("_source", {}).get("s3_key")
                logger.info(f"S3 key to delete: {s3_key_to_delete}")

            if len(documents_to_delete) == 0:
                logger.info(f"No documents found for file {file_id}, treating as already deleted")
                deleted_count = 0
            else:
                # Use bulk delete for efficiency
                actions = []
                for doc in documents_to_delete:
                    actions.append(
                        {
                            "_op_type": "delete",
                            "_index": "openai-embeddings",
                            "_id": doc["_id"],
                        }
                    )

                # Perform bulk delete
                try:
                    bulk_response = helpers.bulk(os_client, actions)
                    deleted_count = len(actions)
                    logger.info(f"Bulk delete completed. Response: {bulk_response}")
                    logger.info(f"Successfully deleted {deleted_count} documents")
                except Exception as bulk_error:
                    logger.error(f"Bulk delete failed: {bulk_error}")
                    # Fallback to individual deletes if bulk fails
                    deleted_count = 0
                    for doc in documents_to_delete:
                        doc_id = doc["_id"]
                        try:
                            os_client.delete(index="openai-embeddings", id=doc_id)
                            logger.info(f"Successfully deleted document {doc_id}")
                            deleted_count += 1
                        except Exception as individual_delete_error:
                            logger.error(f"Failed to delete document {doc_id}: {individual_delete_error}")

        except Exception as search_error:
            logger.error(f"Failed to search for documents to delete: {search_error}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to find documents to delete: {search_error}",
            ) from search_error

        # No need for manual refresh - OpenSearch Serverless handles this automatically within ~1 second

        # Delete from S3 using the s3_key from OpenSearch (if available)
        # Fallback to old format for backwards compatibility with existing documents
        if s3_key_to_delete:
            try:
                logger.info(f"Deleting S3 file with key: {s3_key_to_delete}")
                delete_s3_file(s3_client, S3_BUCKET, s3_key_to_delete, logger)
            except Exception as s3_error:
                logger.warning(f"Could not delete from S3 using s3_key: {s3_error}")
        else:
            # Fallback for old documents without s3_key field
            try:
                logger.info(f"No s3_key found, trying legacy format: uploads/{file_id}")
                delete_s3_file(s3_client, S3_BUCKET, f"uploads/{file_id}", logger)
            except Exception as s3_error:
                logger.warning(f"Could not delete from S3 using legacy format: {s3_error}")

        logger.info(f"Deleted file {file_id} for user: {user_id} - {deleted_count} documents removed")

        return {
            "message": f"File {file_id} deleted successfully",
            "deleted_documents": deleted_count,
        }
    except Exception as e:
        logger.error(f"Error retrieving or deleting user files: {e!s}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve or delete files: {e!s}") from e


@app.post("/files/delete/{file_id}")
async def delete_user_file_s3(file_id: str, user: dict = Depends(get_current_user)):
    """
    DEPRECATED: Use DELETE /files/{file_id} instead.
    This endpoint is kept for backwards compatibility.

    Endpoint to delete a user's file from S3.
    Args:
        file_id (str): The file identifier (filename).
        user (dict): The current user information from authentication.
    Returns:
        JSONResponse: Success or error message.
    """
    logger.warning(f"User {user['sub']} used deprecated endpoint POST /files/delete/{file_id}")
    logger.info(f"Redirecting to DELETE /files/{file_id}")

    # Call the main delete endpoint
    return await delete_user_file(file_id, user)


@app.get("/debug/opensearch")
async def debug_opensearch(current_user: dict = Depends(get_current_user)):
    """Debug endpoint to inspect OpenSearch index content"""
    try:
        user_id = current_user.get("sub")

        # Get all documents for this user (without keyword suffix to see raw storage)
        query = {
            "query": {"term": {"user_id": user_id}},
            "size": 100,
            "_source": ["document_id", "user_id", "chunk_id", "chunk_index"],
        }

        response = os_client.search(index="openai-embeddings", body=query)

        documents = []
        for hit in response["hits"]["hits"]:
            documents.append({"id": hit["_id"], "source": hit["_source"]})

        # Also try with keyword suffix
        keyword_query = {
            "query": {"term": {"user_id.keyword": user_id}},
            "size": 100,
            "_source": ["document_id", "user_id", "chunk_id", "chunk_index"],
        }

        keyword_response = os_client.search(index="openai-embeddings", body=keyword_query)

        keyword_documents = []
        for hit in keyword_response["hits"]["hits"]:
            keyword_documents.append({"id": hit["_id"], "source": hit["_source"]})

        return {
            "user_id": user_id,
            "total_hits_without_keyword": response["hits"]["total"]["value"],
            "total_hits_with_keyword": keyword_response["hits"]["total"]["value"],
            "documents_without_keyword": documents,
            "documents_with_keyword": keyword_documents,
        }

    except Exception as e:
        logger.error(f"Error in debug endpoint: {e!s}")
        raise HTTPException(status_code=500, detail=f"Debug error: {e!s}") from e


# -------------------- User Profile/Onboarding Endpoints --------------------


@app.post("/api/onboarding")
async def save_user_profile(profile: "UserProfileCreate", current_user: dict = Depends(get_current_user)):
    """Save or update user onboarding profile"""

    user_id = current_user.get("sub")
    profile_id = str(uuid.uuid4())

    try:
        with connect(settings.DATA_CONTEXT_DB) as conn:
            cursor = conn.cursor()

            # Check if profile already exists
            if is_postgres_enabled():
                cursor.execute("SELECT id FROM user_profiles WHERE auth0_user_id = %s", (user_id,))
            else:
                cursor.execute("SELECT id FROM user_profiles WHERE auth0_user_id = ?", (user_id,))

            existing = cursor.fetchone()

            use_cases_json = json.dumps(profile.use_cases)
            goals_json = json.dumps(profile.goals)

            if existing:
                # Update existing profile
                if is_postgres_enabled():
                    cursor.execute(
                        """
                        UPDATE user_profiles
                        SET role = %s, use_cases = %s, goals = %s, onboarding_completed = TRUE, updated_at = CURRENT_TIMESTAMP
                        WHERE auth0_user_id = %s
                        RETURNING id
                        """,
                        (profile.role, use_cases_json, goals_json, user_id),
                    )
                    profile_id = cursor.fetchone()["id"]
                else:
                    cursor.execute(
                        """
                        UPDATE user_profiles
                        SET role = ?, use_cases = ?, goals = ?, onboarding_completed = 1, updated_at = CURRENT_TIMESTAMP
                        WHERE auth0_user_id = ?
                        """,
                        (profile.role, use_cases_json, goals_json, user_id),
                    )
                    profile_id = existing["id"] if isinstance(existing, dict) else existing[0]
            else:
                # Insert new profile
                if is_postgres_enabled():
                    cursor.execute(
                        """
                        INSERT INTO user_profiles (id, auth0_user_id, role, use_cases, goals, onboarding_completed)
                        VALUES (%s, %s, %s, %s, %s, TRUE)
                        """,
                        (profile_id, user_id, profile.role, use_cases_json, goals_json),
                    )
                else:
                    cursor.execute(
                        """
                        INSERT INTO user_profiles (id, auth0_user_id, role, use_cases, goals, onboarding_completed)
                        VALUES (?, ?, ?, ?, ?, 1)
                        """,
                        (profile_id, user_id, profile.role, use_cases_json, goals_json),
                    )

            conn.commit()

            return UserProfileResponse(
                id=profile_id,
                auth0_user_id=user_id,
                role=profile.role,
                use_cases=profile.use_cases,
                goals=profile.goals,
                onboarding_completed=True,
            )

    except Exception as e:
        logger.error(f"Error saving user profile: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save profile: {e!s}") from e


@app.get("/api/onboarding")
async def get_user_profile(current_user: dict = Depends(get_current_user)):
    """Get user onboarding profile"""

    user_id = current_user.get("sub")

    try:
        with connect(settings.DATA_CONTEXT_DB) as conn:
            cursor = conn.cursor()

            if is_postgres_enabled():
                cursor.execute("SELECT * FROM user_profiles WHERE auth0_user_id = %s", (user_id,))
            else:
                cursor.execute("SELECT * FROM user_profiles WHERE auth0_user_id = ?", (user_id,))

            row = cursor.fetchone()

            if not row:
                return {"onboarding_completed": False}

            row_dict = (
                dict(row)
                if hasattr(row, "keys")
                else {
                    "id": row[0],
                    "auth0_user_id": row[1],
                    "role": row[2],
                    "use_cases": row[3],
                    "goals": row[4],
                    "onboarding_completed": row[5],
                }
            )

            use_cases = json.loads(row_dict["use_cases"]) if isinstance(row_dict["use_cases"], str) else row_dict["use_cases"]
            goals = json.loads(row_dict["goals"]) if isinstance(row_dict["goals"], str) else row_dict["goals"]

            return UserProfileResponse(
                id=row_dict["id"],
                auth0_user_id=row_dict["auth0_user_id"],
                role=row_dict["role"],
                use_cases=use_cases,
                goals=goals,
                onboarding_completed=bool(row_dict["onboarding_completed"]),
            )

    except Exception as e:
        logger.error(f"Error getting user profile: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get profile: {e!s}") from e


# -------------------- Vendor Endpoints --------------------


@app.get("/api/vendors")
async def list_vendors(current_user: dict = Depends(get_current_user)):
    """List all vendors for the current user"""

    user_id = current_user.get("sub")

    try:
        with connect(settings.DATA_CONTEXT_DB) as conn:
            cursor = conn.cursor()

            if is_postgres_enabled():
                cursor.execute("SELECT * FROM vendors WHERE user_id = %s ORDER BY created_at DESC", (user_id,))
            else:
                cursor.execute("SELECT * FROM vendors WHERE user_id = ? ORDER BY created_at DESC", (user_id,))

            rows = cursor.fetchall()

            vendors = []
            for row in rows:
                row_dict = (
                    dict(row)
                    if hasattr(row, "keys")
                    else {
                        "id": row[0],
                        "user_id": row[1],
                        "name": row[2],
                        "status": row[3],
                        "last_verification_date": row[4],
                        "next_verification_date": row[5],
                        "created_at": row[6],
                    }
                )
                vendors.append(Vendor(**row_dict))

            return {"vendors": vendors, "total": len(vendors)}

    except Exception as e:
        logger.error(f"Error listing vendors: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list vendors: {e!s}") from e


@app.post("/api/vendors")
async def create_vendor(vendor: "VendorCreate", current_user: dict = Depends(get_current_user)):
    """Create a new vendor"""

    user_id = current_user.get("sub")
    vendor_id = str(uuid.uuid4())

    try:
        with connect(settings.DATA_CONTEXT_DB) as conn:
            cursor = conn.cursor()

            if is_postgres_enabled():
                cursor.execute(
                    """
                    INSERT INTO vendors (id, user_id, name, status, last_verification_date, next_verification_date)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (vendor_id, user_id, vendor.name, vendor.status, vendor.last_verification_date, vendor.next_verification_date),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO vendors (id, user_id, name, status, last_verification_date, next_verification_date)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (vendor_id, user_id, vendor.name, vendor.status, vendor.last_verification_date, vendor.next_verification_date),
                )

            conn.commit()

            return Vendor(
                id=vendor_id,
                user_id=user_id,
                name=vendor.name,
                status=vendor.status,
                last_verification_date=vendor.last_verification_date,
                next_verification_date=vendor.next_verification_date,
            )

    except Exception as e:
        logger.error(f"Error creating vendor: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create vendor: {e!s}") from e


@app.get("/api/vendors/{vendor_id}")
async def get_vendor(vendor_id: str, current_user: dict = Depends(get_current_user)):
    """Get vendor details"""
    from app.models import VendorContract

    user_id = current_user.get("sub")

    try:
        with connect(settings.DATA_CONTEXT_DB) as conn:
            cursor = conn.cursor()

            if is_postgres_enabled():
                cursor.execute("SELECT * FROM vendors WHERE id = %s AND user_id = %s", (vendor_id, user_id))
            else:
                cursor.execute("SELECT * FROM vendors WHERE id = ? AND user_id = ?", (vendor_id, user_id))

            row = cursor.fetchone()

            if not row:
                raise HTTPException(status_code=404, detail="Vendor not found")

            row_dict = (
                dict(row)
                if hasattr(row, "keys")
                else {
                    "id": row[0],
                    "user_id": row[1],
                    "name": row[2],
                    "status": row[3],
                    "last_verification_date": row[4],
                    "next_verification_date": row[5],
                    "created_at": row[6],
                }
            )

            # Get contracts for this vendor
            if is_postgres_enabled():
                cursor.execute("SELECT * FROM vendor_contracts WHERE vendor_id = %s", (vendor_id,))
            else:
                cursor.execute("SELECT * FROM vendor_contracts WHERE vendor_id = ?", (vendor_id,))

            contract_rows = cursor.fetchall()
            contracts = []
            for c_row in contract_rows:
                c_dict = (
                    dict(c_row)
                    if hasattr(c_row, "keys")
                    else {
                        "id": c_row[0],
                        "vendor_id": c_row[1],
                        "user_id": c_row[2],
                        "filename": c_row[3],
                        "s3_key": c_row[4],
                        "audit_status": c_row[5],
                        "compliance_status": c_row[6],
                        "created_at": c_row[7],
                    }
                )
                contracts.append(VendorContract(**c_dict))

            return {"vendor": Vendor(**row_dict), "contracts": contracts}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting vendor: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get vendor: {e!s}") from e


@app.put("/api/vendors/{vendor_id}")
async def update_vendor(vendor_id: str, vendor_update: "VendorUpdate", current_user: dict = Depends(get_current_user)):
    """Update a vendor"""

    user_id = current_user.get("sub")

    try:
        with connect(settings.DATA_CONTEXT_DB) as conn:
            cursor = conn.cursor()

            # Check vendor exists and belongs to user
            if is_postgres_enabled():
                cursor.execute("SELECT * FROM vendors WHERE id = %s AND user_id = %s", (vendor_id, user_id))
            else:
                cursor.execute("SELECT * FROM vendors WHERE id = ? AND user_id = ?", (vendor_id, user_id))

            row = cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Vendor not found")

            # Build update query dynamically
            updates = []
            params = []
            if vendor_update.name is not None:
                updates.append("name = %s" if is_postgres_enabled() else "name = ?")
                params.append(vendor_update.name)
            if vendor_update.status is not None:
                updates.append("status = %s" if is_postgres_enabled() else "status = ?")
                params.append(vendor_update.status)
            if vendor_update.last_verification_date is not None:
                updates.append("last_verification_date = %s" if is_postgres_enabled() else "last_verification_date = ?")
                params.append(vendor_update.last_verification_date)
            if vendor_update.next_verification_date is not None:
                updates.append("next_verification_date = %s" if is_postgres_enabled() else "next_verification_date = ?")
                params.append(vendor_update.next_verification_date)

            if updates:
                params.append(vendor_id)
                query = f"UPDATE vendors SET {', '.join(updates)} WHERE id = {'%s' if is_postgres_enabled() else '?'}"
                cursor.execute(query, params)
                conn.commit()

            # Fetch updated vendor
            if is_postgres_enabled():
                cursor.execute("SELECT * FROM vendors WHERE id = %s", (vendor_id,))
            else:
                cursor.execute("SELECT * FROM vendors WHERE id = ?", (vendor_id,))

            updated_row = cursor.fetchone()
            row_dict = (
                dict(updated_row)
                if hasattr(updated_row, "keys")
                else {
                    "id": updated_row[0],
                    "user_id": updated_row[1],
                    "name": updated_row[2],
                    "status": updated_row[3],
                    "last_verification_date": updated_row[4],
                    "next_verification_date": updated_row[5],
                    "created_at": updated_row[6],
                }
            )

            return Vendor(**row_dict)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating vendor: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update vendor: {e!s}") from e


@app.delete("/api/vendors/{vendor_id}")
async def delete_vendor(vendor_id: str, current_user: dict = Depends(get_current_user)):
    """Delete a vendor"""
    user_id = current_user.get("sub")

    try:
        with connect(settings.DATA_CONTEXT_DB) as conn:
            cursor = conn.cursor()

            if is_postgres_enabled():
                cursor.execute("DELETE FROM vendors WHERE id = %s AND user_id = %s", (vendor_id, user_id))
            else:
                cursor.execute("DELETE FROM vendors WHERE id = ? AND user_id = ?", (vendor_id, user_id))

            conn.commit()

            return {"message": "Vendor deleted successfully"}

    except Exception as e:
        logger.error(f"Error deleting vendor: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete vendor: {e!s}") from e


# -------------------- Vendor Contracts Endpoints --------------------


@app.post("/api/vendors/{vendor_id}/contracts")
async def add_vendor_contract(vendor_id: str, contract: "VendorContractCreate", current_user: dict = Depends(get_current_user)):
    """Add a contract to a vendor"""

    user_id = current_user.get("sub")
    contract_id = str(uuid.uuid4())

    try:
        with connect(settings.DATA_CONTEXT_DB) as conn:
            cursor = conn.cursor()

            # Verify vendor belongs to user
            if is_postgres_enabled():
                cursor.execute("SELECT id FROM vendors WHERE id = %s AND user_id = %s", (vendor_id, user_id))
            else:
                cursor.execute("SELECT id FROM vendors WHERE id = ? AND user_id = ?", (vendor_id, user_id))

            if not cursor.fetchone():
                raise HTTPException(status_code=404, detail="Vendor not found")

            if is_postgres_enabled():
                cursor.execute(
                    """
                    INSERT INTO vendor_contracts (id, vendor_id, user_id, filename, s3_key)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (contract_id, vendor_id, user_id, contract.filename, contract.s3_key),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO vendor_contracts (id, vendor_id, user_id, filename, s3_key)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (contract_id, vendor_id, user_id, contract.filename, contract.s3_key),
                )

            conn.commit()

            return VendorContract(
                id=contract_id,
                vendor_id=vendor_id,
                user_id=user_id,
                filename=contract.filename,
                s3_key=contract.s3_key,
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding contract: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to add contract: {e!s}") from e


# -------------------- Dashboard Stats Endpoint --------------------


@app.get("/api/dashboard/stats")
async def get_dashboard_stats(current_user: dict = Depends(get_current_user)):
    """Get dashboard statistics for the current user"""
    user_id = current_user.get("sub")

    try:
        with connect(settings.DATA_CONTEXT_DB) as conn:
            cursor = conn.cursor()

            # Get vendor counts by status
            if is_postgres_enabled():
                cursor.execute(
                    """
                    SELECT status, COUNT(*) as count FROM vendors WHERE user_id = %s GROUP BY status
                    """,
                    (user_id,),
                )
            else:
                cursor.execute(
                    """
                    SELECT status, COUNT(*) as count FROM vendors WHERE user_id = ? GROUP BY status
                    """,
                    (user_id,),
                )
            vendor_status_rows = cursor.fetchall()
            vendor_by_status = {}
            total_vendors = 0
            for row in vendor_status_rows:
                status = row["status"] if hasattr(row, "keys") else row[0]
                count = row["count"] if hasattr(row, "keys") else row[1]
                vendor_by_status[status] = count
                total_vendors += count

            # Get contract counts by audit status
            if is_postgres_enabled():
                cursor.execute(
                    """
                    SELECT audit_status, COUNT(*) as count FROM vendor_contracts WHERE user_id = %s GROUP BY audit_status
                    """,
                    (user_id,),
                )
            else:
                cursor.execute(
                    """
                    SELECT audit_status, COUNT(*) as count FROM vendor_contracts WHERE user_id = ? GROUP BY audit_status
                    """,
                    (user_id,),
                )
            contract_audit_rows = cursor.fetchall()
            contracts_by_audit_status = {}
            total_contracts = 0
            for row in contract_audit_rows:
                status = row["audit_status"] if hasattr(row, "keys") else row[0]
                count = row["count"] if hasattr(row, "keys") else row[1]
                contracts_by_audit_status[status or "waiting"] = count
                total_contracts += count

            # Get contract counts by compliance status
            if is_postgres_enabled():
                cursor.execute(
                    """
                    SELECT compliance_status, COUNT(*) as count FROM vendor_contracts WHERE user_id = %s GROUP BY compliance_status
                    """,
                    (user_id,),
                )
            else:
                cursor.execute(
                    """
                    SELECT compliance_status, COUNT(*) as count FROM vendor_contracts WHERE user_id = ? GROUP BY compliance_status
                    """,
                    (user_id,),
                )
            compliance_rows = cursor.fetchall()
            contracts_by_compliance = {}
            for row in compliance_rows:
                status = row["compliance_status"] if hasattr(row, "keys") else row[0]
                count = row["count"] if hasattr(row, "keys") else row[1]
                contracts_by_compliance[status or "not_verified"] = count

            # Get vendors with monitoring info
            if is_postgres_enabled():
                cursor.execute(
                    """
                    SELECT id, name, status, last_verification_date, next_verification_date
                    FROM vendors WHERE user_id = %s ORDER BY next_verification_date ASC NULLS LAST LIMIT 10
                    """,
                    (user_id,),
                )
            else:
                cursor.execute(
                    """
                    SELECT id, name, status, last_verification_date, next_verification_date
                    FROM vendors WHERE user_id = ? ORDER BY next_verification_date ASC LIMIT 10
                    """,
                    (user_id,),
                )
            monitoring_rows = cursor.fetchall()
            vendor_monitoring = []
            for row in monitoring_rows:
                if hasattr(row, "keys"):
                    vendor_monitoring.append(dict(row))
                else:
                    vendor_monitoring.append(
                        {
                            "id": row[0],
                            "name": row[1],
                            "status": row[2],
                            "last_verification_date": row[3],
                            "next_verification_date": row[4],
                        }
                    )

            return {
                "total_vendors": total_vendors,
                "vendor_by_status": vendor_by_status,
                "total_contracts": total_contracts,
                "contracts_by_audit_status": contracts_by_audit_status,
                "contracts_by_compliance": contracts_by_compliance,
                "vendor_monitoring": vendor_monitoring,
            }

    except Exception as e:
        logger.error(f"Error getting dashboard stats: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get dashboard stats: {e!s}") from e


# -------------------- Contract Audit Endpoints --------------------


@app.get("/api/contract-audit/checklists")
async def list_checklists(_current_user: dict = Depends(get_current_user)):
    """List available audit checklists"""

    checklists_dir = Path(__file__).parent / "data" / "checklists"
    checklists = []

    try:
        if checklists_dir.exists():
            for file_path in checklists_dir.glob("*.json"):
                with open(file_path) as f:
                    checklist_data = json.load(f)
                    checklists.append(ChecklistDefinition(**checklist_data))

        return {"checklists": checklists}

    except Exception as e:
        logger.error(f"Error listing checklists: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list checklists: {e!s}") from e


@app.post("/api/contract-audit/start")
async def start_contract_audit(
    audit_request: "ContractAuditCreate",
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
):
    """Start a contract audit"""

    user_id = current_user.get("sub")
    audit_id = str(uuid.uuid4())

    try:
        # Load checklist
        checklists_dir = Path(__file__).parent / "data" / "checklists"
        checklist_path = checklists_dir / f"{audit_request.checklist_type}.json"

        if not checklist_path.exists():
            raise HTTPException(status_code=404, detail=f"Checklist {audit_request.checklist_type} not found")

        with open(checklist_path) as f:
            checklist_data = json.load(f)

        checklist_items = [{"question_id": q["id"], "question": q["question"]} for q in checklist_data.get("questions", [])]

        with connect(settings.DATA_CONTEXT_DB) as conn:
            cursor = conn.cursor()

            checklist_items_json = json.dumps(checklist_items)

            if is_postgres_enabled():
                cursor.execute(
                    """
                    INSERT INTO contract_audits (id, contract_id, user_id, checklist_type, checklist_name, checklist_items, status)
                    VALUES (%s, %s, %s, %s, %s, %s, 'pending')
                    """,
                    (audit_id, audit_request.contract_id, user_id, audit_request.checklist_type, audit_request.checklist_name, checklist_items_json),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO contract_audits (id, contract_id, user_id, checklist_type, checklist_name, checklist_items, status)
                    VALUES (?, ?, ?, ?, ?, ?, 'pending')
                    """,
                    (audit_id, audit_request.contract_id, user_id, audit_request.checklist_type, audit_request.checklist_name, checklist_items_json),
                )

            conn.commit()

        # Start background task for audit processing
        background_tasks.add_task(process_contract_audit, audit_id, user_id, audit_request.documents, checklist_data)

        return {"audit_id": audit_id, "status": "pending", "message": "Contract audit started"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting contract audit: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to start audit: {e!s}") from e


async def process_contract_audit(audit_id: str, user_id: str, documents: list[str], checklist_data: dict):
    """Background task to process contract audit"""
    from app.utils.search import hybrid_search

    try:
        questions = checklist_data.get("questions", [])
        total_questions = len(questions)
        results = []

        with connect(settings.DATA_CONTEXT_DB) as conn:
            cursor = conn.cursor()

            # Update status to in_progress
            if is_postgres_enabled():
                cursor.execute("UPDATE contract_audits SET status = 'in_progress' WHERE id = %s", (audit_id,))
            else:
                cursor.execute("UPDATE contract_audits SET status = 'in_progress' WHERE id = ?", (audit_id,))
            conn.commit()

        for i, question in enumerate(questions):
            try:
                # Search for relevant content using RAG
                search_result = await hybrid_search(
                    question["question"],
                    os_client,
                    logger,
                    k=5,
                    user_id=user_id,
                    filenames=documents if documents else None,
                )

                context = search_result.get("context", "")

                # Generate answer using OpenAI
                if context:
                    prompt = f"""Based on the following contract content, answer this audit question:

Question: {question["question"]}

Contract content:
{context[:4000]}

Provide:
1. A clear YES/NO/PARTIAL answer indicating if the contract addresses this requirement
2. A brief explanation
3. The relevant quote from the contract (if found)

Format your response as:
ANSWER: [YES/NO/PARTIAL]
EXPLANATION: [Your explanation]
QUOTE: [Relevant quote from the contract or "No direct quote found"]"""

                    response = await run_in_thread(
                        openai.chat.completions.create,
                        model="gpt-5-mini",
                        messages=[
                            {
                                "role": "system",
                                "content": "You are a contract audit assistant. Analyze contracts and answer audit questions accurately.",
                            },
                            {"role": "user", "content": prompt},
                        ],
                    )

                    answer_text = response.choices[0].message.content.strip()

                    # Parse the response
                    answer_lines = answer_text.split("\n")
                    compliant = None
                    explanation = ""
                    quote = ""

                    for line in answer_lines:
                        if line.startswith("ANSWER:"):
                            answer_val = line.replace("ANSWER:", "").strip().upper()
                            if "YES" in answer_val:
                                compliant = True
                            elif "NO" in answer_val:
                                compliant = False
                            else:
                                compliant = None
                        elif line.startswith("EXPLANATION:"):
                            explanation = line.replace("EXPLANATION:", "").strip()
                        elif line.startswith("QUOTE:"):
                            quote = line.replace("QUOTE:", "").strip()

                    result = {
                        "question_id": question["id"],
                        "question": question["question"],
                        "answer": explanation or answer_text,
                        "source_quote": quote if quote and "No direct quote" not in quote else None,
                        "source_document": documents[0] if documents else None,
                        "compliant": compliant,
                    }
                else:
                    result = {
                        "question_id": question["id"],
                        "question": question["question"],
                        "answer": "No relevant content found in the uploaded documents.",
                        "source_quote": None,
                        "source_document": None,
                        "compliant": None,
                    }

                results.append(result)

                # Update progress
                progress = int(((i + 1) / total_questions) * 100)
                with connect(settings.DATA_CONTEXT_DB) as conn:
                    cursor = conn.cursor()
                    results_json = json.dumps(results)
                    if is_postgres_enabled():
                        cursor.execute(
                            "UPDATE contract_audits SET checklist_items = %s, progress = %s WHERE id = %s",
                            (results_json, progress, audit_id),
                        )
                    else:
                        cursor.execute(
                            "UPDATE contract_audits SET checklist_items = ?, progress = ? WHERE id = ?",
                            (results_json, progress, audit_id),
                        )
                    conn.commit()

            except Exception as q_error:
                logger.error(f"Error processing question {question['id']}: {q_error}")
                results.append(
                    {
                        "question_id": question["id"],
                        "question": question["question"],
                        "answer": f"Error processing question: {q_error!s}",
                        "source_quote": None,
                        "source_document": None,
                        "compliant": None,
                    }
                )

        # Mark as completed
        with connect(settings.DATA_CONTEXT_DB) as conn:
            cursor = conn.cursor()
            results_json = json.dumps(results)
            if is_postgres_enabled():
                cursor.execute(
                    """UPDATE contract_audits
                    SET checklist_items = %s, status = 'completed',
                    progress = 100, completed_at = CURRENT_TIMESTAMP WHERE id = %s""",
                    (results_json, audit_id),
                )
            else:
                cursor.execute(
                    """UPDATE contract_audits
                    SET checklist_items = ?, status = 'completed',
                    progress = 100, completed_at = CURRENT_TIMESTAMP WHERE id = ?""",
                    (results_json, audit_id),
                )
            conn.commit()

        logger.info(f"Contract audit {audit_id} completed successfully")

    except Exception as e:
        logger.error(f"Error in contract audit processing: {e}")
        with connect(settings.DATA_CONTEXT_DB) as conn:
            cursor = conn.cursor()
            if is_postgres_enabled():
                cursor.execute("UPDATE contract_audits SET status = 'failed' WHERE id = %s", (audit_id,))
            else:
                cursor.execute("UPDATE contract_audits SET status = 'failed' WHERE id = ?", (audit_id,))
            conn.commit()


@app.get("/api/contract-audit/{audit_id}/status")
async def get_contract_audit_status(audit_id: str, current_user: dict = Depends(get_current_user)):
    """Get contract audit status"""

    user_id = current_user.get("sub")

    try:
        with connect(settings.DATA_CONTEXT_DB) as conn:
            cursor = conn.cursor()

            if is_postgres_enabled():
                cursor.execute("SELECT * FROM contract_audits WHERE id = %s AND user_id = %s", (audit_id, user_id))
            else:
                cursor.execute("SELECT * FROM contract_audits WHERE id = ? AND user_id = ?", (audit_id, user_id))

            row = cursor.fetchone()

            if not row:
                raise HTTPException(status_code=404, detail="Audit not found")

            row_dict = (
                dict(row)
                if hasattr(row, "keys")
                else {
                    "id": row[0],
                    "contract_id": row[1],
                    "user_id": row[2],
                    "checklist_type": row[3],
                    "checklist_name": row[4],
                    "checklist_items": row[5],
                    "status": row[6],
                    "progress": row[7],
                    "report_s3_key": row[8],
                    "created_at": row[9],
                    "completed_at": row[10],
                }
            )

            checklist_items = json.loads(row_dict["checklist_items"]) if isinstance(row_dict["checklist_items"], str) else row_dict["checklist_items"]
            items = [ChecklistItem(**item) for item in checklist_items]

            return ContractAuditStatus(
                id=row_dict["id"],
                status=row_dict["status"],
                progress=row_dict["progress"],
                checklist_items=items,
                completed_at=row_dict.get("completed_at"),
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting audit status: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get audit status: {e!s}") from e


@app.get("/api/contract-audit/list")
async def list_contract_audits(current_user: dict = Depends(get_current_user)):
    """List all contract audits for the current user"""
    user_id = current_user.get("sub")

    try:
        with connect(settings.DATA_CONTEXT_DB) as conn:
            cursor = conn.cursor()

            if is_postgres_enabled():
                cursor.execute(
                    """SELECT id, checklist_name, status, progress, created_at, completed_at
                    FROM contract_audits WHERE user_id = %s ORDER BY created_at DESC""",
                    (user_id,),
                )
            else:
                cursor.execute(
                    """SELECT id, checklist_name, status, progress, created_at, completed_at
                    FROM contract_audits WHERE user_id = ? ORDER BY created_at DESC""",
                    (user_id,),
                )

            rows = cursor.fetchall()

            audits = []
            for row in rows:
                if hasattr(row, "keys"):
                    audits.append(dict(row))
                else:
                    audits.append(
                        {
                            "id": row[0],
                            "checklist_name": row[1],
                            "status": row[2],
                            "progress": row[3],
                            "created_at": row[4],
                            "completed_at": row[5],
                        }
                    )

            return {"audits": audits, "total": len(audits)}

    except Exception as e:
        logger.error(f"Error listing audits: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list audits: {e!s}") from e


# -------------------- DORA Audit Endpoints --------------------


@app.post("/api/dora/generate")
async def start_dora_audit(
    audit_request: "DoraAuditCreate",
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
):
    """Start a DORA audit"""

    user_id = current_user.get("sub")
    audit_id = str(uuid.uuid4())

    try:
        with connect(settings.DATA_CONTEXT_DB) as conn:
            cursor = conn.cursor()

            questionnaire_json = json.dumps(audit_request.questionnaire_data)
            documents_json = json.dumps(audit_request.documents)

            if is_postgres_enabled():
                cursor.execute(
                    """
                    INSERT INTO dora_audits (id, user_id, company_name, questionnaire_data, documents, status)
                    VALUES (%s, %s, %s, %s, %s, 'pending')
                    """,
                    (audit_id, user_id, audit_request.company_name, questionnaire_json, documents_json),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO dora_audits (id, user_id, company_name, questionnaire_data, documents, status)
                    VALUES (?, ?, ?, ?, ?, 'pending')
                    """,
                    (audit_id, user_id, audit_request.company_name, questionnaire_json, documents_json),
                )

            conn.commit()

        # Start background task for DORA audit processing
        background_tasks.add_task(process_dora_audit, audit_id, user_id, audit_request)

        return {"audit_id": audit_id, "status": "pending", "message": "DORA audit started"}

    except Exception as e:
        logger.error(f"Error starting DORA audit: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to start DORA audit: {e!s}") from e


async def process_dora_audit(audit_id: str, user_id: str, audit_request):
    """Background task to process DORA audit"""
    from app.utils.search import hybrid_search

    # DORA audit questions
    dora_questions = [
        {"id": "dora_1", "question": "Does the organization have an ICT risk management framework in place?"},
        {"id": "dora_2", "question": "Are there documented policies for ICT security and risk management?"},
        {"id": "dora_3", "question": "Does the organization have incident reporting procedures for ICT-related incidents?"},
        {"id": "dora_4", "question": "Are there business continuity and disaster recovery plans for ICT systems?"},
        {"id": "dora_5", "question": "Does the organization conduct regular ICT risk assessments?"},
        {"id": "dora_6", "question": "Are ICT third-party providers subject to due diligence and ongoing monitoring?"},
        {"id": "dora_7", "question": "Does the organization have a register of all ICT third-party service providers?"},
        {"id": "dora_8", "question": "Are there contractual arrangements addressing ICT service continuity with third parties?"},
        {"id": "dora_9", "question": "Does the organization conduct digital operational resilience testing?"},
        {"id": "dora_10", "question": "Are there information sharing arrangements for cyber threat intelligence?"},
        {"id": "dora_11", "question": "Does the organization have audit rights over ICT third-party providers?"},
    ]

    try:
        total_questions = len(dora_questions)
        results = []

        with connect(settings.DATA_CONTEXT_DB) as conn:
            cursor = conn.cursor()
            if is_postgres_enabled():
                cursor.execute("UPDATE dora_audits SET status = 'in_progress' WHERE id = %s", (audit_id,))
            else:
                cursor.execute("UPDATE dora_audits SET status = 'in_progress' WHERE id = ?", (audit_id,))
            conn.commit()

        for i, question in enumerate(dora_questions):
            try:
                # Search for relevant content
                search_result = await hybrid_search(
                    question["question"],
                    os_client,
                    logger,
                    k=5,
                    user_id=user_id,
                    filenames=audit_request.documents if audit_request.documents else None,
                )

                context = search_result.get("context", "")

                if context:
                    prompt = f"""Based on the following documentation, assess compliance with this DORA requirement:

Question: {question["question"]}

Documentation content:
{context[:4000]}

Provide:
1. A compliance assessment: COMPLIANT, NON-COMPLIANT, or PARTIAL
2. A detailed explanation
3. The relevant quote from the documentation (if found)

Format your response as:
COMPLIANCE: [COMPLIANT/NON-COMPLIANT/PARTIAL]
EXPLANATION: [Your detailed explanation]
QUOTE: [Relevant quote from the documentation or "No direct quote found"]"""

                    response = await run_in_thread(
                        openai.chat.completions.create,
                        model="gpt-5-mini",
                        messages=[
                            {
                                "role": "system",
                                "content": "You are a DORA compliance expert. Assess organizational compliance with DORA requirements accurately.",
                            },
                            {"role": "user", "content": prompt},
                        ],
                    )

                    answer_text = response.choices[0].message.content.strip()

                    # Parse response
                    compliance = "partial"
                    explanation = ""
                    quote = ""

                    for line in answer_text.split("\n"):
                        if line.startswith("COMPLIANCE:"):
                            comp_val = line.replace("COMPLIANCE:", "").strip().upper()
                            if "NON-COMPLIANT" in comp_val or "NON COMPLIANT" in comp_val:
                                compliance = "false"
                            elif "COMPLIANT" in comp_val:
                                compliance = "true"
                            else:
                                compliance = "partial"
                        elif line.startswith("EXPLANATION:"):
                            explanation = line.replace("EXPLANATION:", "").strip()
                        elif line.startswith("QUOTE:"):
                            quote = line.replace("QUOTE:", "").strip()

                    result = {
                        "question_id": question["id"],
                        "question": question["question"],
                        "answer": explanation or answer_text,
                        "source_document": audit_request.documents[0] if audit_request.documents else None,
                        "source_quote": quote if quote and "No direct quote" not in quote else None,
                        "compliant": compliance,
                    }
                else:
                    result = {
                        "question_id": question["id"],
                        "question": question["question"],
                        "answer": "No relevant documentation found for this requirement.",
                        "source_document": None,
                        "source_quote": None,
                        "compliant": None,
                    }

                results.append(result)

                # Update progress
                progress = int(((i + 1) / total_questions) * 100)
                with connect(settings.DATA_CONTEXT_DB) as conn:
                    cursor = conn.cursor()
                    results_json = json.dumps(results)
                    if is_postgres_enabled():
                        cursor.execute(
                            "UPDATE dora_audits SET results = %s, progress = %s WHERE id = %s",
                            (results_json, progress, audit_id),
                        )
                    else:
                        cursor.execute(
                            "UPDATE dora_audits SET results = ?, progress = ? WHERE id = ?",
                            (results_json, progress, audit_id),
                        )
                    conn.commit()

            except Exception as q_error:
                logger.error(f"Error processing DORA question {question['id']}: {q_error}")
                results.append(
                    {
                        "question_id": question["id"],
                        "question": question["question"],
                        "answer": f"Error processing question: {q_error!s}",
                        "source_document": None,
                        "source_quote": None,
                        "compliant": None,
                    }
                )

        # Mark as completed
        with connect(settings.DATA_CONTEXT_DB) as conn:
            cursor = conn.cursor()
            results_json = json.dumps(results)
            if is_postgres_enabled():
                cursor.execute(
                    "UPDATE dora_audits SET results = %s, status = 'completed', progress = 100, completed_at = CURRENT_TIMESTAMP WHERE id = %s",
                    (results_json, audit_id),
                )
            else:
                cursor.execute(
                    "UPDATE dora_audits SET results = ?, status = 'completed', progress = 100, completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (results_json, audit_id),
                )
            conn.commit()

        logger.info(f"DORA audit {audit_id} completed successfully")

    except Exception as e:
        logger.error(f"Error in DORA audit processing: {e}")
        with connect(settings.DATA_CONTEXT_DB) as conn:
            cursor = conn.cursor()
            if is_postgres_enabled():
                cursor.execute("UPDATE dora_audits SET status = 'failed' WHERE id = %s", (audit_id,))
            else:
                cursor.execute("UPDATE dora_audits SET status = 'failed' WHERE id = ?", (audit_id,))
            conn.commit()


@app.get("/api/dora/{audit_id}/status")
async def get_dora_audit_status(audit_id: str, current_user: dict = Depends(get_current_user)):
    """Get DORA audit status"""

    user_id = current_user.get("sub")

    try:
        with connect(settings.DATA_CONTEXT_DB) as conn:
            cursor = conn.cursor()

            if is_postgres_enabled():
                cursor.execute("SELECT * FROM dora_audits WHERE id = %s AND user_id = %s", (audit_id, user_id))
            else:
                cursor.execute("SELECT * FROM dora_audits WHERE id = ? AND user_id = ?", (audit_id, user_id))

            row = cursor.fetchone()

            if not row:
                raise HTTPException(status_code=404, detail="DORA audit not found")

            row_dict = (
                dict(row)
                if hasattr(row, "keys")
                else {
                    "id": row[0],
                    "user_id": row[1],
                    "company_name": row[2],
                    "questionnaire_data": row[3],
                    "documents": row[4],
                    "status": row[5],
                    "progress": row[6],
                    "results": row[7],
                    "created_at": row[8],
                    "completed_at": row[9],
                }
            )

            results_data = json.loads(row_dict["results"]) if isinstance(row_dict["results"], str) else (row_dict["results"] or [])
            results = [DoraAuditResult(**r) for r in results_data]

            return DoraAuditStatus(
                id=row_dict["id"],
                status=row_dict["status"],
                progress=row_dict["progress"],
                results=results,
                company_name=row_dict.get("company_name"),
                completed_at=row_dict.get("completed_at"),
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting DORA audit status: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get DORA audit status: {e!s}") from e


@app.get("/api/dora/list")
async def list_dora_audits(current_user: dict = Depends(get_current_user)):
    """List all DORA audits for the current user"""

    user_id = current_user.get("sub")

    try:
        with connect(settings.DATA_CONTEXT_DB) as conn:
            cursor = conn.cursor()

            if is_postgres_enabled():
                cursor.execute(
                    """SELECT id, company_name, status, progress, created_at, completed_at
                    FROM dora_audits WHERE user_id = %s ORDER BY created_at DESC""",
                    (user_id,),
                )
            else:
                cursor.execute(
                    "SELECT id, company_name, status, progress, created_at, completed_at FROM dora_audits WHERE user_id = ? ORDER BY created_at DESC",
                    (user_id,),
                )

            rows = cursor.fetchall()

            audits = []
            for row in rows:
                if hasattr(row, "keys"):
                    audits.append(DoraAuditListItem(**dict(row)))
                else:
                    audits.append(
                        DoraAuditListItem(
                            id=row[0],
                            company_name=row[1],
                            status=row[2],
                            progress=row[3],
                            created_at=row[4],
                            completed_at=row[5],
                        )
                    )

            return {"audits": audits, "total": len(audits)}

    except Exception as e:
        logger.error(f"Error listing DORA audits: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list DORA audits: {e!s}") from e


@app.delete("/api/dora/{audit_id}")
async def delete_dora_audit(audit_id: str, current_user: dict = Depends(get_current_user)):
    """Delete a DORA audit"""
    user_id = current_user.get("sub")

    try:
        with connect(settings.DATA_CONTEXT_DB) as conn:
            cursor = conn.cursor()

            if is_postgres_enabled():
                cursor.execute("DELETE FROM dora_audits WHERE id = %s AND user_id = %s", (audit_id, user_id))
            else:
                cursor.execute("DELETE FROM dora_audits WHERE id = ? AND user_id = ?", (audit_id, user_id))

            conn.commit()

            return {"message": "DORA audit deleted successfully"}

    except Exception as e:
        logger.error(f"Error deleting DORA audit: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete DORA audit: {e!s}") from e


# -------------------- Chat Endpoints --------------------


@app.post("/api/chat")
async def chat(chat_request: "ChatRequest", current_user: dict = Depends(get_current_user)):
    """Send a chat message and get a response"""
    from app.utils.search import hybrid_search

    user_id = current_user.get("sub")
    session_id = chat_request.session_id or str(uuid.uuid4())

    try:
        # Search for relevant context from documents
        context = ""
        sources = []

        if chat_request.context_documents:
            search_result = await hybrid_search(
                chat_request.message,
                os_client,
                logger,
                k=5,
                user_id=user_id,
                filenames=chat_request.context_documents,
            )
            context = search_result.get("context", "")
            if search_result.get("ids"):
                sources = [{"id": doc_id, "type": "document"} for doc_id in search_result["ids"][:3]]

        # Build prompt
        if context:
            system_prompt = """You are an AI assistant specialized in compliance, DORA regulations, and contract analysis.
Answer questions based on the provided document context when available.
If the context doesn't contain relevant information, use your general knowledge but indicate this clearly.
Always cite sources when using information from the documents."""

            user_prompt = f"""Context from documents:
{context[:4000]}

User question: {chat_request.message}

Please provide a helpful and accurate response."""
        else:
            system_prompt = """You are an AI assistant specialized in compliance, DORA regulations, and contract analysis.
Provide helpful and accurate information about regulatory compliance, vendor management, and contract auditing.
Be clear when you're providing general guidance versus specific regulatory requirements."""

            user_prompt = chat_request.message

        # Generate response
        response = await run_in_thread(
            openai.chat.completions.create,
            model="gpt-5-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )

        assistant_message = response.choices[0].message.content.strip()

        # Save to session
        with connect(settings.DATA_CONTEXT_DB) as conn:
            cursor = conn.cursor()

            # Check if session exists
            if is_postgres_enabled():
                cursor.execute("SELECT messages FROM chat_sessions WHERE id = %s AND user_id = %s", (session_id, user_id))
            else:
                cursor.execute("SELECT messages FROM chat_sessions WHERE id = ? AND user_id = ?", (session_id, user_id))

            existing = cursor.fetchone()

            new_messages = [
                {"role": "user", "content": chat_request.message, "timestamp": datetime.now().isoformat()},
                {"role": "assistant", "content": assistant_message, "timestamp": datetime.now().isoformat(), "sources": sources},
            ]

            if existing:
                messages_data = existing["messages"] if hasattr(existing, "keys") else existing[0]
                messages = json.loads(messages_data) if isinstance(messages_data, str) else messages_data
                messages.extend(new_messages)
                messages_json = json.dumps(messages)
                context_docs_json = json.dumps(chat_request.context_documents or [])

                if is_postgres_enabled():
                    cursor.execute(
                        "UPDATE chat_sessions SET messages = %s, context_documents = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                        (messages_json, context_docs_json, session_id),
                    )
                else:
                    cursor.execute(
                        "UPDATE chat_sessions SET messages = ?, context_documents = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (messages_json, context_docs_json, session_id),
                    )
            else:
                messages_json = json.dumps(new_messages)
                context_docs_json = json.dumps(chat_request.context_documents or [])

                if is_postgres_enabled():
                    cursor.execute(
                        "INSERT INTO chat_sessions (id, user_id, messages, context_documents) VALUES (%s, %s, %s, %s)",
                        (session_id, user_id, messages_json, context_docs_json),
                    )
                else:
                    cursor.execute(
                        "INSERT INTO chat_sessions (id, user_id, messages, context_documents) VALUES (?, ?, ?, ?)",
                        (session_id, user_id, messages_json, context_docs_json),
                    )

            conn.commit()

        return ChatResponse(
            message=assistant_message,
            sources=sources if sources else None,
            session_id=session_id,
        )

    except Exception as e:
        logger.error(f"Error in chat: {e}")
        raise HTTPException(status_code=500, detail=f"Chat error: {e!s}") from e


@app.get("/api/chat/sessions")
async def list_chat_sessions(current_user: dict = Depends(get_current_user)):
    """List all chat sessions for the current user"""

    user_id = current_user.get("sub")

    try:
        with connect(settings.DATA_CONTEXT_DB) as conn:
            cursor = conn.cursor()

            if is_postgres_enabled():
                cursor.execute(
                    "SELECT id, messages, created_at, updated_at FROM chat_sessions WHERE user_id = %s ORDER BY updated_at DESC",
                    (user_id,),
                )
            else:
                cursor.execute(
                    "SELECT id, messages, created_at, updated_at FROM chat_sessions WHERE user_id = ? ORDER BY updated_at DESC",
                    (user_id,),
                )

            rows = cursor.fetchall()

            sessions = []
            for row in rows:
                row_dict = dict(row) if hasattr(row, "keys") else {"id": row[0], "messages": row[1], "created_at": row[2], "updated_at": row[3]}

                messages = json.loads(row_dict["messages"]) if isinstance(row_dict["messages"], str) else row_dict["messages"]
                last_msg = messages[-1]["content"][:100] if messages else None

                sessions.append(
                    ChatSessionListItem(
                        id=row_dict["id"],
                        created_at=row_dict.get("created_at"),
                        updated_at=row_dict.get("updated_at"),
                        message_count=len(messages),
                        last_message=last_msg,
                    )
                )

            return {"sessions": sessions, "total": len(sessions)}

    except Exception as e:
        logger.error(f"Error listing chat sessions: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list sessions: {e!s}") from e


@app.get("/api/chat/sessions/{session_id}")
async def get_chat_session(session_id: str, current_user: dict = Depends(get_current_user)):
    """Get a specific chat session"""

    user_id = current_user.get("sub")

    try:
        with connect(settings.DATA_CONTEXT_DB) as conn:
            cursor = conn.cursor()

            if is_postgres_enabled():
                cursor.execute("SELECT * FROM chat_sessions WHERE id = %s AND user_id = %s", (session_id, user_id))
            else:
                cursor.execute("SELECT * FROM chat_sessions WHERE id = ? AND user_id = ?", (session_id, user_id))

            row = cursor.fetchone()

            if not row:
                raise HTTPException(status_code=404, detail="Chat session not found")

            row_dict = (
                dict(row)
                if hasattr(row, "keys")
                else {
                    "id": row[0],
                    "user_id": row[1],
                    "messages": row[2],
                    "context_documents": row[3],
                    "created_at": row[4],
                    "updated_at": row[5],
                }
            )

            messages = json.loads(row_dict["messages"]) if isinstance(row_dict["messages"], str) else row_dict["messages"]
            context_docs = (
                json.loads(row_dict["context_documents"]) if isinstance(row_dict["context_documents"], str) else row_dict["context_documents"]
            )

            return ChatSession(
                id=row_dict["id"],
                user_id=row_dict["user_id"],
                messages=[ChatMessage(**m) for m in messages],
                context_documents=context_docs or [],
                created_at=row_dict.get("created_at"),
                updated_at=row_dict.get("updated_at"),
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting chat session: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get session: {e!s}") from e


@app.delete("/api/chat/sessions/{session_id}")
async def delete_chat_session(session_id: str, current_user: dict = Depends(get_current_user)):
    """Delete a chat session"""
    user_id = current_user.get("sub")

    try:
        with connect(settings.DATA_CONTEXT_DB) as conn:
            cursor = conn.cursor()

            if is_postgres_enabled():
                cursor.execute("DELETE FROM chat_sessions WHERE id = %s AND user_id = %s", (session_id, user_id))
            else:
                cursor.execute("DELETE FROM chat_sessions WHERE id = ? AND user_id = ?", (session_id, user_id))

            conn.commit()

            return {"message": "Chat session deleted successfully"}

    except Exception as e:
        logger.error(f"Error deleting chat session: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete session: {e!s}") from e


# -------------------- Vendor Qualification Endpoints --------------------


@app.get("/api/dora/ict-services")
async def get_dora_ict_services(_current_user: dict = Depends(get_current_user)):
    """Get list of DORA ICT service types (S01-S16)"""
    try:
        import os

        json_path = os.path.join(os.path.dirname(__file__), "data", "dora", "ict_services_types.json")

        with open(json_path) as f:
            data = json.load(f)

        return {
            "services": data.get("services", []),
            "ict_services_definition": data.get("ict_services_definition"),
        }

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="DORA ICT services data not found") from None
    except Exception as e:
        logger.error(f"Error loading DORA ICT services: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to load DORA services: {e!s}") from e


@app.post("/api/vendor-qualification/start")
async def start_vendor_qualification(qualification_data: VendorQualificationCreate, current_user: dict = Depends(get_current_user)):
    """Start a new vendor qualification process"""
    user_id = current_user.get("sub")
    qualification_id = str(uuid.uuid4())

    try:
        with connect(settings.DATA_CONTEXT_DB) as conn:
            cursor = conn.cursor()

            if is_postgres_enabled():
                cursor.execute(
                    """
                    INSERT INTO vendor_qualifications
                    (id, vendor_id, vendor_name, user_id, status, current_step, step_data, services_mapping, created_at)
                    VALUES (%s, %s, %s, %s, 'draft', 1, '{}', '[]', CURRENT_TIMESTAMP)
                    """,
                    (qualification_id, qualification_data.vendor_id, qualification_data.vendor_name, user_id),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO vendor_qualifications
                    (id, vendor_id, vendor_name, user_id, status, current_step, step_data, services_mapping, created_at)
                    VALUES (?, ?, ?, ?, 'draft', 1, '{}', '[]', CURRENT_TIMESTAMP)
                    """,
                    (qualification_id, qualification_data.vendor_id, qualification_data.vendor_name, user_id),
                )

            conn.commit()

            return {
                "qualification_id": qualification_id,
                "vendor_id": qualification_data.vendor_id,
                "vendor_name": qualification_data.vendor_name,
                "status": "draft",
                "current_step": 1,
                "message": "Vendor qualification started",
            }

    except Exception as e:
        logger.error(f"Error starting vendor qualification: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to start qualification: {e!s}") from e


@app.get("/api/vendor-qualification/list")
async def list_vendor_qualifications(current_user: dict = Depends(get_current_user)):
    """List all vendor qualifications for the current user"""
    user_id = current_user.get("sub")

    try:
        with connect(settings.DATA_CONTEXT_DB) as conn:
            cursor = conn.cursor()

            if is_postgres_enabled():
                cursor.execute(
                    """
                    SELECT id, vendor_id, vendor_name, status, current_step, is_ict_provider, created_at
                    FROM vendor_qualifications WHERE user_id = %s ORDER BY created_at DESC
                    """,
                    (user_id,),
                )
            else:
                cursor.execute(
                    """
                    SELECT id, vendor_id, vendor_name, status, current_step, is_ict_provider, created_at
                    FROM vendor_qualifications WHERE user_id = ? ORDER BY created_at DESC
                    """,
                    (user_id,),
                )

            rows = cursor.fetchall()
            qualifications = []

            for row in rows:
                if hasattr(row, "keys"):
                    row_dict = dict(row)
                else:
                    row_dict = {
                        "id": row[0],
                        "vendor_id": row[1],
                        "vendor_name": row[2],
                        "status": row[3],
                        "current_step": row[4],
                        "is_ict_provider": row[5],
                        "created_at": row[6],
                    }

                qualifications.append(
                    VendorQualificationListItem(
                        id=row_dict["id"],
                        vendor_id=row_dict["vendor_id"],
                        vendor_name=row_dict.get("vendor_name"),
                        status=row_dict["status"],
                        current_step=row_dict["current_step"],
                        is_ict_provider=bool(row_dict["is_ict_provider"]) if row_dict.get("is_ict_provider") is not None else None,
                        created_at=row_dict.get("created_at"),
                    )
                )

            return {"qualifications": qualifications, "total": len(qualifications)}

    except Exception as e:
        logger.error(f"Error listing vendor qualifications: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list qualifications: {e!s}") from e


@app.get("/api/vendor-qualification/{qualification_id}")
async def get_vendor_qualification(qualification_id: str, current_user: dict = Depends(get_current_user)):
    """Get a specific vendor qualification"""
    user_id = current_user.get("sub")

    try:
        with connect(settings.DATA_CONTEXT_DB) as conn:
            cursor = conn.cursor()

            if is_postgres_enabled():
                cursor.execute(
                    "SELECT * FROM vendor_qualifications WHERE id = %s AND user_id = %s",
                    (qualification_id, user_id),
                )
            else:
                cursor.execute(
                    "SELECT * FROM vendor_qualifications WHERE id = ? AND user_id = ?",
                    (qualification_id, user_id),
                )

            row = cursor.fetchone()

            if not row:
                raise HTTPException(status_code=404, detail="Vendor qualification not found")

            if hasattr(row, "keys"):
                row_dict = dict(row)
            else:
                row_dict = {
                    "id": row[0],
                    "vendor_id": row[1],
                    "vendor_name": row[2],
                    "user_id": row[3],
                    "status": row[4],
                    "current_step": row[5],
                    "step_data": row[6],
                    "is_ict_provider": row[7],
                    "services_mapping": row[8],
                    "created_at": row[9],
                    "completed_at": row[10],
                }

            step_data = json.loads(row_dict["step_data"]) if isinstance(row_dict["step_data"], str) else row_dict["step_data"]
            services_mapping = (
                json.loads(row_dict["services_mapping"]) if isinstance(row_dict["services_mapping"], str) else row_dict["services_mapping"]
            )

            return VendorQualification(
                id=row_dict["id"],
                vendor_id=row_dict["vendor_id"],
                vendor_name=row_dict.get("vendor_name"),
                user_id=row_dict["user_id"],
                status=row_dict["status"],
                current_step=row_dict["current_step"],
                step_data=step_data or {},
                is_ict_provider=bool(row_dict["is_ict_provider"]) if row_dict.get("is_ict_provider") is not None else None,
                services_mapping=services_mapping or [],
                created_at=row_dict.get("created_at"),
                completed_at=row_dict.get("completed_at"),
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting vendor qualification: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get qualification: {e!s}") from e


@app.put("/api/vendor-qualification/{qualification_id}/step/{step_num}")
async def update_qualification_step(
    qualification_id: str,
    step_num: int,
    step_update: VendorQualificationStepUpdate,
    current_user: dict = Depends(get_current_user),
):
    """Update a specific step of the vendor qualification"""
    user_id = current_user.get("sub")

    try:
        with connect(settings.DATA_CONTEXT_DB) as conn:
            cursor = conn.cursor()

            # Get current qualification
            if is_postgres_enabled():
                cursor.execute(
                    "SELECT step_data, current_step FROM vendor_qualifications WHERE id = %s AND user_id = %s",
                    (qualification_id, user_id),
                )
            else:
                cursor.execute(
                    "SELECT step_data, current_step FROM vendor_qualifications WHERE id = ? AND user_id = ?",
                    (qualification_id, user_id),
                )

            row = cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Vendor qualification not found")

            step_data = json.loads(row[0]) if isinstance(row[0], str) else (row[0] or {})

            # Update step data
            step_data[str(step_num)] = {
                "question_responses": step_update.question_responses,
                "approved": step_update.approved,
                "approved_at": datetime.now().isoformat() if step_update.approved else None,
            }

            step_data_json = json.dumps(step_data)
            new_step = step_num + 1 if step_update.approved else step_num
            new_status = "completed" if step_update.approved and step_num >= 4 else "in_progress"

            if is_postgres_enabled():
                cursor.execute(
                    """
                    UPDATE vendor_qualifications
                    SET step_data = %s, current_step = %s, status = %s
                    WHERE id = %s AND user_id = %s
                    """,
                    (step_data_json, new_step, new_status, qualification_id, user_id),
                )
            else:
                cursor.execute(
                    """
                    UPDATE vendor_qualifications
                    SET step_data = ?, current_step = ?, status = ?
                    WHERE id = ? AND user_id = ?
                    """,
                    (step_data_json, new_step, new_status, qualification_id, user_id),
                )

            conn.commit()

            return {
                "qualification_id": qualification_id,
                "step": step_num,
                "approved": step_update.approved,
                "current_step": new_step,
                "status": new_status,
                "message": f"Step {step_num} updated successfully",
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating qualification step: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update step: {e!s}") from e


@app.put("/api/vendor-qualification/{qualification_id}/ict-provider")
async def update_ict_provider_status(
    qualification_id: str,
    is_ict_provider: bool,
    current_user: dict = Depends(get_current_user),
):
    """Update the ICT provider status for a qualification"""
    user_id = current_user.get("sub")

    try:
        with connect(settings.DATA_CONTEXT_DB) as conn:
            cursor = conn.cursor()

            if is_postgres_enabled():
                cursor.execute(
                    """
                    UPDATE vendor_qualifications
                    SET is_ict_provider = %s
                    WHERE id = %s AND user_id = %s
                    """,
                    (is_ict_provider, qualification_id, user_id),
                )
            else:
                cursor.execute(
                    """
                    UPDATE vendor_qualifications
                    SET is_ict_provider = ?
                    WHERE id = ? AND user_id = ?
                    """,
                    (1 if is_ict_provider else 0, qualification_id, user_id),
                )

            conn.commit()

            return {
                "qualification_id": qualification_id,
                "is_ict_provider": is_ict_provider,
                "message": "ICT provider status updated",
            }

    except Exception as e:
        logger.error(f"Error updating ICT provider status: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update ICT provider status: {e!s}") from e


@app.put("/api/vendor-qualification/{qualification_id}/services-mapping")
async def update_services_mapping(
    qualification_id: str,
    services_mapping: list[ServiceMapping],
    current_user: dict = Depends(get_current_user),
):
    """Update the services mapping for a qualification"""
    user_id = current_user.get("sub")

    try:
        with connect(settings.DATA_CONTEXT_DB) as conn:
            cursor = conn.cursor()

            services_json = json.dumps([s.model_dump() for s in services_mapping])

            if is_postgres_enabled():
                cursor.execute(
                    """
                    UPDATE vendor_qualifications
                    SET services_mapping = %s
                    WHERE id = %s AND user_id = %s
                    """,
                    (services_json, qualification_id, user_id),
                )
            else:
                cursor.execute(
                    """
                    UPDATE vendor_qualifications
                    SET services_mapping = ?
                    WHERE id = ? AND user_id = ?
                    """,
                    (services_json, qualification_id, user_id),
                )

            conn.commit()

            return {
                "qualification_id": qualification_id,
                "services_count": len(services_mapping),
                "message": "Services mapping updated",
            }

    except Exception as e:
        logger.error(f"Error updating services mapping: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update services mapping: {e!s}") from e


@app.post("/api/vendor-qualification/{qualification_id}/generate-answer")
async def generate_qualification_answer(
    qualification_id: str,
    request: GenerateAnswerRequest,
    current_user: dict = Depends(get_current_user),
):
    """Generate an AI answer for a qualification question using RAG"""
    user_id = current_user.get("sub")

    try:
        from app.utils.search import hybrid_search

        # Verify qualification belongs to user
        with connect(settings.DATA_CONTEXT_DB) as conn:
            cursor = conn.cursor()
            if is_postgres_enabled():
                cursor.execute(
                    "SELECT id FROM vendor_qualifications WHERE id = %s AND user_id = %s",
                    (qualification_id, user_id),
                )
            else:
                cursor.execute(
                    "SELECT id FROM vendor_qualifications WHERE id = ? AND user_id = ?",
                    (qualification_id, user_id),
                )
            if not cursor.fetchone():
                raise HTTPException(status_code=404, detail="Vendor qualification not found")

        # Build the prompt for RAG
        prompt = f"""Based on the provided documents, answer the following question about vendor services:

Question: {request.question_text}

Please provide a clear, detailed answer based on the information found in the documents.
If the information is not found, state that clearly.
Include relevant quotes from the source documents to support your answer."""

        # Use hybrid search to find relevant context
        search_results = await hybrid_search(request.question_text, user_id, top_k=5)

        context_chunks = []
        source_document = None
        source_quote = None

        if search_results and search_results.get("results"):
            for result in search_results["results"][:3]:
                chunk_text = result.get("chunk_text", "")
                doc_name = result.get("document_name", "Unknown")
                context_chunks.append(f"[From {doc_name}]: {chunk_text}")
                if not source_document:
                    source_document = doc_name
                    source_quote = chunk_text[:500] if chunk_text else None

        context = "\n\n".join(context_chunks) if context_chunks else "No relevant documents found."

        system_content = "You are an expert in vendor qualification and DORA compliance. " "Answer questions based on the provided document context."
        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": f"Context from documents:\n{context}\n\n{prompt}"},
        ]

        response = await asyncio.to_thread(
            openai.chat.completions.create,
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.3,
            max_tokens=1000,
        )

        answer = response.choices[0].message.content.strip()

        return GenerateAnswerResponse(
            answer=answer,
            source_document=source_document,
            source_quote=source_quote,
            confidence=0.8 if context_chunks else 0.3,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating qualification answer: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to generate answer: {e!s}") from e


@app.post("/api/vendor-qualification/{qualification_id}/generate-report")
async def generate_qualification_report(
    qualification_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Generate a qualification report for a vendor"""
    user_id = current_user.get("sub")

    try:
        # Get qualification data
        with connect(settings.DATA_CONTEXT_DB) as conn:
            cursor = conn.cursor()

            if is_postgres_enabled():
                cursor.execute(
                    "SELECT * FROM vendor_qualifications WHERE id = %s AND user_id = %s",
                    (qualification_id, user_id),
                )
            else:
                cursor.execute(
                    "SELECT * FROM vendor_qualifications WHERE id = ? AND user_id = ?",
                    (qualification_id, user_id),
                )

            row = cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Vendor qualification not found")

            if hasattr(row, "keys"):
                row_dict = dict(row)
            else:
                row_dict = {
                    "id": row[0],
                    "vendor_id": row[1],
                    "vendor_name": row[2],
                    "user_id": row[3],
                    "status": row[4],
                    "current_step": row[5],
                    "step_data": row[6],
                    "is_ict_provider": row[7],
                    "services_mapping": row[8],
                    "created_at": row[9],
                    "completed_at": row[10],
                }

        step_data = json.loads(row_dict["step_data"]) if isinstance(row_dict["step_data"], str) else (row_dict["step_data"] or {})
        services_mapping = (
            json.loads(row_dict["services_mapping"]) if isinstance(row_dict["services_mapping"], str) else (row_dict["services_mapping"] or [])
        )

        # Generate summary using GPT
        prompt = f"""Generate a qualification report summary for the following vendor:

Vendor Name: {row_dict.get('vendor_name', 'Unknown')}
Is ICT Service Provider: {'Yes' if row_dict.get('is_ict_provider') else 'No' if row_dict.get('is_ict_provider') is False else 'Not determined'}
Services Mapped: {len(services_mapping)} services

Step Data: {json.dumps(step_data, indent=2)}

Please provide a professional summary of the vendor qualification status, including:
1. Overview of the vendor classification
2. Services provided and their DORA categorization
3. Risk considerations
4. Recommendations for next steps"""

        response = await asyncio.to_thread(
            openai.chat.completions.create,
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=1500,
        )

        summary = response.choices[0].message.content.strip()

        return {
            "qualification_id": qualification_id,
            "vendor_name": row_dict.get("vendor_name"),
            "status": row_dict["status"],
            "is_ict_provider": row_dict.get("is_ict_provider"),
            "services_count": len(services_mapping),
            "summary": summary,
            "generated_at": datetime.now().isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating qualification report: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to generate report: {e!s}") from e


@app.delete("/api/vendor-qualification/{qualification_id}")
async def delete_vendor_qualification(qualification_id: str, current_user: dict = Depends(get_current_user)):
    """Delete a vendor qualification"""
    user_id = current_user.get("sub")

    try:
        with connect(settings.DATA_CONTEXT_DB) as conn:
            cursor = conn.cursor()

            if is_postgres_enabled():
                cursor.execute(
                    "DELETE FROM vendor_qualifications WHERE id = %s AND user_id = %s",
                    (qualification_id, user_id),
                )
            else:
                cursor.execute(
                    "DELETE FROM vendor_qualifications WHERE id = ? AND user_id = ?",
                    (qualification_id, user_id),
                )

            conn.commit()

            return {"message": "Vendor qualification deleted successfully"}

    except Exception as e:
        logger.error(f"Error deleting vendor qualification: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete qualification: {e!s}") from e


# -------------------- Uvicorn Run --------------------
if __name__ == "__main__":
    import uvicorn

    logger.info("Starting FastAPI app on port 8000...")
    uvicorn.run(app, host="0.0.0.0", port=8000)

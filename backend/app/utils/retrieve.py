import asyncio
import logging

import aiohttp
import openai
import tiktoken
from aiohttp import ClientTimeout

from app.config import get_firecrawl_app
from app.models import FieldQuestionsFormat
from app.utils.async_utils import run_in_thread
from app.utils.helper_functions import (
    clean_scraped_text,
    extract_main_content,
    index_chunk,
    process_chunk,
)
from app.utils.json_loader import (
    get_guideline_by_no,
    get_relevant_variable,
    get_subquestions_by_field_id,
    load_guidelines,
)
from app.utils.prompt_loader import prompt_loader


class TikTokenTokenizer:
    """Wrapper for tiktoken to work with docling's BaseTokenizer interface"""

    def __init__(self, model_name: str = "text-embedding-ada-002", **_kwargs):
        self._model_name = model_name
        self._encoding = tiktoken.encoding_for_model(model_name)

    def encode(self, text: str) -> list[int]:
        return self._encoding.encode(text)

    def decode(self, token_ids: list[int]) -> str:
        return self._encoding.decode(token_ids)

    def count_tokens(self, text: str) -> int:
        return len(self._encoding.encode(text))

    def get_max_tokens(self) -> int:
        # text-embedding-ada-002 has a context window of 8191 tokens
        return 8191

    def get_tokenizer(self):
        return self._encoding


def chunk_text_tiktoken(text: str, chunk_size: int = 2_000, overlap_tokens: int = 200) -> list[str]:
    tokenizer = TikTokenTokenizer()
    tokens = tokenizer.encode(text)

    if len(tokens) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    while start < len(tokens):
        end = min(start + chunk_size, len(tokens))
        chunk_tokens = tokens[start:end]
        chunked_text = tokenizer.decode(chunk_tokens)
        chunks.append(chunked_text.strip())
        if end >= len(tokens):
            break
        start = end - overlap_tokens

    return chunks


def is_field_a_simple_forward(field_id: str, logger: logging.Logger, token_classification: str) -> str:
    """
    Checks if a field is a simple forward based on JSON subquestions.
    Returns the relevant variable if present, otherwise empty string.
    """
    try:
        rv = get_relevant_variable(token_classification, field_id)
        if rv:
            logger.info(f"Field {field_id} is a simple forward. Forwarding value from {rv}.")
            return rv
        logger.warning(f"Field {field_id} is not a simple forward.")
        return ""
    except Exception as e:
        logger.error("Error checking if field is a simple forward: %s", e)
        return ""


async def fetch_url_content(url: str, logger: logging.Logger) -> str:
    """
    Fetches the content of a URL asynchronously and extracts the main content.

    Args:
        url (str): The URL from which to fetch content.
        logger (logging): Logger for debugging information.

    Returns:
        str: The main content text extracted from the URL if successful; otherwise, an empty string.
    """
    logger.info("Fetching content from URL: %s", url)
    try:
        async with aiohttp.ClientSession() as session:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            }
            async with session.get(url, headers=headers, timeout=ClientTimeout(total=30)) as response:
                if response.status == 200:
                    page_html = await response.text()

                    main_content = extract_main_content(page_html)
                    if len(main_content) < 200 and len(page_html) > 1000:
                        logger.info(
                            "Main content extraction yielded limited text, falling back to cleaned full page for %s",
                            url,
                        )
                        main_content = clean_scraped_text(page_html)

                    logger.info(
                        "Successfully fetched and extracted content from %s (%d chars)",
                        url,
                        len(main_content),
                    )
                    return main_content
                else:
                    logger.warning(
                        "Failed to fetch content from %s, status code: %d",
                        url,
                        response.status,
                    )
    except Exception as e:
        logger.error("Error fetching content from %s: %s", url, e)
        return ""


def get_fields_info(logger: logging.Logger) -> list[tuple]:
    """
    Get fields info from guidelines JSON (OTH by default).
    """
    try:
        items = load_guidelines("OTH")
        fields = [(str(g.no), g.field, g.section_name, g.content_to_be_reported) for g in items]
        logger.info("Retrieved %d fields from guidelines JSON.", len(fields))
        return fields
    except Exception as e:
        logger.error("Error accessing guidelines JSON: %s", e)
        return []


def get_field_questions(field_id: str, logger: logging.Logger, token_classification: str) -> list[FieldQuestionsFormat]:
    """
    Fetches questions related to a specific field from JSON subquestions.
    """
    try:
        items = get_subquestions_by_field_id(token_classification, field_id)
        if not items:
            logger.warning(
                "No questions found for field_id: %s in token_classification: %s",
                field_id,
                token_classification,
            )
            return []
        questions: list[FieldQuestionsFormat] = []
        for sq in items:
            questions.append(
                FieldQuestionsFormat(
                    question=sq.question,
                    type=sq.type,
                    relevant_field=sq.relevant_field,
                    relevant_variable=sq.relevant_variable,
                )
            )
        logger.info(f"Found {len(questions)} questions for field_id: {field_id} in tc: {token_classification}")
        return questions
    except Exception as e:
        logger.error(
            "Error fetching questions for field_id %s (tc %s): %s",
            field_id,
            token_classification,
            e,
        )
        return []


def get_field_standards(field_id: str, logger: logging.Logger, token_classification: str) -> dict[str, str]:
    """
    Fetch field standards and formatting information from JSON guidelines.
    """
    try:
        g = get_guideline_by_no(token_classification, field_id)
        if g:
            return {
                "field_content": g.content_to_be_reported,
                "form_and_standards": g.form_and_standards,
            }
        logger.warning("No standards found for field_id: %s", field_id)
        return {}
    except Exception as e:
        logger.error("Error fetching field standards for %s: %s", field_id, e)
        return {}


def get_hardcoded_field_content(field_id: str, logger: logging.Logger, token_classification: str) -> str:
    """
    Fetch hardcoded field content from JSON guidelines if the field has
    'Predefined alphanumerical text' as its form and standards value.
    """
    try:
        g = get_guideline_by_no(token_classification, field_id)
        if g and g.form_and_standards == "Predefined alphanumerical text":
            logger.info("Found hardcoded content for field_id: %s", field_id)
            return g.content_to_be_reported
        return ""
    except Exception as e:
        logger.error("Error fetching hardcoded content for field_id %s: %s", field_id, e)
        return ""


async def scrape_links(links: list[str], logger: logging.Logger, os_client) -> list[dict]:
    """
    Scrapes multiple URLs concurrently and processes the text content from each page.

    This asynchronous function takes a list of URLs, fetches their content concurrently, and processes
    the text into chunks. Each chunk is then processed by calling the `process_chunk` function.
    If no content is retrieved from a page, a warning is logged.

    Args:
        links (List[str]): A list of URLs to be scraped.
        logger (logging): The logging object used for logging information and warnings.
        os_client: OpenSearch client for checking existing chunks.

    Returns:
        results (List[Dict]): A list of dictionaries containing the processed url, chunks and their embeddings.
    """
    logger.info("Starting to scrape %d links concurrently.", len(links))
    links_to_scrape = []
    # Fetch all link contents concurrently
    tasks = []
    for url in links:
        index_name = url.replace("http://", "").replace("https://", "").replace("/", "_").replace(".", "_").replace(":", "_") + "_chunks"

        try:
            found_chunks = await run_in_thread(os_client.search, index=index_name, body={"query": {"match_all": {}}})
            if found_chunks["hits"]["total"]["value"] > 0:
                logger.info("Found existing chunks for %s, skipping scraping.", url)
                tasks.append(process_chunk(found_chunks["hits"]["hits"], logger))
            else:
                logger.info("No existing chunks found for %s, will scrape.", url)
                links_to_scrape.append(url)
        except Exception as e:
            logger.info(
                "Index not found for %s (this is normal for new URLs), will scrape. Error: %s",
                url,
                str(e),
            )
            links_to_scrape.append(url)
    if links_to_scrape:
        logger.info("Scraping %d new URLs.", len(links_to_scrape))
        try:
            firecrawl_app = get_firecrawl_app()
            logger.info("Firecrawl app initialized successfully")

            batch_result = await run_in_thread(firecrawl_app.batch_scrape_urls, links_to_scrape, formats=["markdown"])
            logger.info("Batch scraping completed, processing results")

            pages_content = batch_result.data if hasattr(batch_result, "data") else batch_result
            logger.info("Found %d pages of content", len(pages_content) if pages_content else 0)

        except Exception as e:
            logger.error("Error during batch scraping: %s", e)
            logger.info("Falling back to manual URL fetching")
            pages_content = await asyncio.gather(*[fetch_url_content(url, logger) for url in links_to_scrape])
            logger.info(
                "Fallback scraping completed, found %d pages of content",
                len(pages_content),
            )

        for url, page in zip(links_to_scrape, pages_content, strict=False):
            page_text = clean_scraped_text(page) if isinstance(page, str) else page.markdown
            if page_text:
                logger.info("Processing content from %s", url)
                cleaned_text = clean_scraped_text(page_text)
                logger.info(
                    "Cleaned text from %s (reduced from %d to %d chars)",
                    url,
                    len(page_text),
                    len(cleaned_text),
                )
                page_chunks = chunk_text_tiktoken(cleaned_text, chunk_size=1000, overlap_tokens=50)
                logger.info("Chunked content from %s into %d chunks.", url, len(page_chunks))
                page_chunks = [chunk for chunk in page_chunks if len(chunk.strip()) > 50]

                for chunk in page_chunks:
                    tasks.append(process_chunk(chunk, logger))
                    try:
                        context = await generate_chunk_context(
                            document_text=page_text,
                            chunk=chunk,
                            source_type="webpage",
                            logger=logger,
                        )
                        contextual_chunk = f"{context}\n\n{chunk}"

                        embedding_response = await run_in_thread(
                            openai.embeddings.create,
                            input=contextual_chunk,
                            model="text-embedding-ada-002",
                        )
                        embedding = embedding_response.data[0].embedding
                        index_name = (
                            url.replace("http://", "").replace("https://", "").replace("/", "_").replace(".", "_").replace(":", "_") + "_chunks"
                        )
                        await run_in_thread(
                            index_chunk,
                            index_name,
                            contextual_chunk,
                            embedding,
                            os_client,
                            user_id=None,
                        )
                        logger.info("Indexed %d chunks for %s", len(page_chunks), url)
                    except Exception as e:
                        logger.error(
                            "Error creating embeddings or indexing chunks for %s: %s",
                            url,
                            e,
                        )
            else:
                logger.warning("No text retrieved from %s", url)
    logger.info("Processing chunks from all links.")
    results = await asyncio.gather(*tasks)
    logger.info("Completed scraping and processing chunks from links.")
    return results


async def generate_chunk_context(document_text: str, chunk: str, source_type: str, logger: logging.Logger) -> str:
    """
    Generate contextual information for a chunk using the full document.

    Args:
        document_text (str): The full document text
        chunk (str): The specific chunk to add context to
        source_type (str): The type of source (e.g., "webpage")
        logger: Logger instance

    Returns:
        str: Contextual header for the chunk
    """
    prompt = prompt_loader.generate_rag_context(source_type=source_type, document_text=document_text, chunk=chunk)

    try:
        response = await run_in_thread(
            openai.chat.completions.create,
            model="gpt-5",
            messages=[
                {
                    "role": "system",
                    "content": prompt_loader.get_system_message("document_summary"),
                },
                {"role": "user", "content": prompt},
            ],
        )
        context = response.choices[0].message.content.strip()
        logger.info(f"Generated context for chunk (length: {len(chunk)}): {context[:100]}...")
        return context
    except Exception as e:
        logger.error(f"Error generating chunk context: {e}")
        return chunk

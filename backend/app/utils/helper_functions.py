import html
import json
import logging
import re
from asyncio import Semaphore
from logging import Logger
from typing import Any

import numpy as np
import openai
from opensearchpy import OpenSearch

from .async_utils import run_in_thread

semaphore = Semaphore(10)


def index_chunk(document_id: str, chunk: str, embedding: list[float], os_client: OpenSearch, user_id) -> Any:
    """
    Indexes a chunk of text along with its embedding into OpenSearch.

    Args:
        document_id (str): The unique identifier for the document being indexed.
        chunk (str): The chunk of text to be indexed.
        embedding (List[float]): A list of floating-point numbers representing the embedding of the chunk.
        os_client (OpenSearch): The OpenSearch client used to interact with the OpenSearch cluster.

    Returns:
        any: The response from the OpenSearch client after attempting to index the chunk.
    """
    body = {
        "document_id": document_id,
        "text": chunk,
        "embedding": embedding,
        "user_id": user_id,
    }

    # For AWS OpenSearch Serverless VECTORSEARCH, let it auto-generate document IDs
    # This is because VECTORSEARCH type has restrictions on external document IDs
    response = os_client.index(index="openai-embeddings", body=body)

    return response


def unindex_chunk(document_id: str, os_client: OpenSearch) -> Any:
    """
    Unindexes a chunk of text from OpenSearch using its document ID.

    Args:
        document_id (str): The unique identifier for the document to be unindexed.
        os_client (OpenSearch): The OpenSearch client used to interact with the OpenSearch cluster.

    Returns:
        any: The response from the OpenSearch client after attempting to unindex the chunk.
    """
    logger = logging.getLogger(__name__)
    try:
        response = os_client.delete(index="openai-embeddings", id=document_id)
        logger.info(f"Successfully deleted document {document_id} from index")
        return response
    except Exception as e:
        # Handle the case where the document doesn't exist or other errors
        if "404" in str(e) or "not_found" in str(e).lower() or "NotFoundError" in str(type(e)):
            logger.warning(f"Document {document_id} not found in index, it may have already been deleted")
            return {"result": "not_found", "document_id": document_id}
        else:
            # Log other exceptions and re-raise them
            logger.error(f"Error deleting document {document_id}: {e!s}")
            raise e


def delete_s3_file(s3_client: Any, bucket: str, key: str, logger: Logger) -> bool:
    """
    Deletes a file from an S3 bucket.

    Args:
        s3_client: The S3 client used to interact with the S3 service.
        bucket (str): The name of the S3 bucket.
        key (str): The key of the file to be deleted.
        logger (logging): A logging object used to log any errors during the deletion process.

    Returns:
        bool: True if the file was successfully deleted, False otherwise.
    """
    try:
        s3_client.delete_object(Bucket=bucket, Key=key)
        return True
    except Exception as e:
        logger.error(f"Error deleting file {key} from bucket {bucket}: {e!s}")
        return False


def clean_scraped_text(text: str) -> str:
    """
    Cleans and normalizes scraped text to make it more suitable for AI processing.

    This function:
    1. Unescapes HTML entities
    2. Removes or normalizes escape sequences
    3. Attempts to extract clean text from JSON-like structures
    4. Normalizes whitespace
    5. Removes common HTML/CSS artifacts

    Args:
        text (str): The raw scraped text to clean

    Returns:
        str: The cleaned and normalized text
    """
    logger = logging.getLogger(__name__)

    if not text:
        return ""

    text = html.unescape(text)
    try:
        json_matches = re.findall(r'(\{["\'].*?["\']:["\'].*?["\'].*?\})', text)
        for json_str in json_matches:
            try:
                parsed = json.loads(json_str)
                for _key, value in parsed.items():
                    if isinstance(value, str) and len(value) > 15:
                        text = text.replace(json_str, value)
            except Exception as e:
                logger.error(f"Error parsing JSON string '{json_str}': {e!s}")
    except Exception as e:
        logger.error(f"Error processing JSON matches: {e!s}")

    text = text.replace("\\n", " ").replace("\\r", " ").replace("\\t", " ")
    text = text.replace('\\"', '"').replace("\\'", "'")

    text = re.sub(r"https?://\S+", "[URL]", text)

    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\{[^}]+\}", " ", text)
    text = re.sub(r'__typename["\']:\s*["\'][^"\']+["\']', " ", text)

    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"[^\w\s,.?!;:()\[\]]{3,}", " ", text)

    return text


def chunk_text(text: str, chunk_size: int = 500) -> list[str]:
    """
    Splits a text into smaller chunks based on a specified chunk size.
    Attempts to create more coherent chunks by breaking at sentence boundaries when possible.

    Args:
        text (str): The input text to be split into chunks.
        chunk_size (int, optional): The maximum size of each chunk in characters. Defaults to 500.

    Returns:
        List[str]: A list of text chunks, each having a length close to or less than the specified chunk size.
    """
    text = clean_scraped_text(text)
    sentences = re.split(r"(?<=[.!?])\s+", text)

    chunks: list[str] = []
    current_chunk: list[str] = []
    current_length = 0

    for sentence in sentences:
        sentence_len = len(sentence)

        if sentence_len > chunk_size:
            if current_chunk:
                chunks.append(" ".join(current_chunk))
                current_chunk = []
                current_length = 0

            words = sentence.split()
            word_chunk: list[str] = []
            word_chunk_len = 0

            for word in words:
                word_len = len(word) + 1
                if word_chunk_len + word_len <= chunk_size:
                    word_chunk.append(word)
                    word_chunk_len += word_len
                else:
                    if word_chunk:
                        chunks.append(" ".join(word_chunk))
                    word_chunk = [word]
                    word_chunk_len = word_len

            if word_chunk:
                chunks.append(" ".join(word_chunk))

        elif current_length + sentence_len + 1 <= chunk_size:
            current_chunk.append(sentence)
            current_length += sentence_len + 1
        else:
            if current_chunk:
                chunks.append(" ".join(current_chunk))
            current_chunk = [sentence]
            current_length = sentence_len

    if current_chunk:
        chunks.append(" ".join(current_chunk))
    return chunks


async def process_chunk(chunk: str, logger: Logger) -> dict[str, Any] | None:
    """
    Processes a text chunk by creating an embedding using OpenAI's API,
    then stores the chunk and its embedding in a database.

    This function asynchronously calls OpenAI's API to generate the embedding of the given chunk of text.
    It then appends the chunk, its embedding, and the associated URL to a list,
    and logs any errors encountered during the process.

    Args:
        chunk (str): The text chunk to be processed.
        url (str): The URL from which the chunk was scraped.
        logger (logging): A logging object used to log any errors during the process.

    Returns:
        dict or None: The processed record containing the URL, chunk, and embedding,
                       or `None` if an error occurs during the processing.
    """
    async with semaphore:
        try:
            embedding_response = await run_in_thread(openai.embeddings.create, input=chunk, model="text-embedding-ada-002")
            embedding = embedding_response.data[0].embedding
            record = {"chunk": chunk, "embedding": embedding}
            return record
        except Exception as e:
            logger.error("Error processing chunk: %s", e)
            return None


def cosine_similarity_numpy(query_embedding: list[float], chunk_embedding: list[float]) -> float:
    """
    Computes the cosine similarity between two embeddings using NumPy.

    The cosine similarity is a measure of similarity between two non-zero vectors in an inner product space,
    defined as the cosine of the angle between them. This is used frequently in various NLP tasks to measure
    how similar two pieces of text are, based on their embeddings.

    Args:
        query_embedding (List[float]): The embedding of the query text as a list of floating-point numbers.
        chunk_embedding (List[float]): The embedding of the chunk of text as a list of floating-point numbers.

    Returns:
        float: The cosine similarity between the two embeddings, a value between -1 and 1.
              A value closer to 1 indicates high similarity, while a value closer to -1 indicates high dissimilarity.
    """
    q = np.asarray(query_embedding, dtype=float)
    c = np.asarray(chunk_embedding, dtype=float)
    denom = float(np.linalg.norm(q) * np.linalg.norm(c))
    if denom == 0.0:
        return 0.0
    return float(np.dot(q, c) / denom)


def extract_main_content(html_content: str) -> str:
    """
    Attempts to extract the main content from an HTML page by focusing on
    specific content areas and removing boilerplate elements.

    Args:
        html_content (str): The raw HTML content

    Returns:
        str: The extracted main content text
    """
    import re

    # Remove script and style elements
    html_content = re.sub(r"<script[^>]*>.*?</script>", " ", html_content, flags=re.DOTALL)
    html_content = re.sub(r"<style[^>]*>.*?</style>", " ", html_content, flags=re.DOTALL)

    # Try to identify and extract main content areas
    main_content = ""

    # Look for common content containers
    content_patterns = [
        r"<main[^>]*>(.*?)</main>",
        r"<article[^>]*>(.*?)</article>",
        r'<div[^>]*class="[^"]*content[^"]*"[^>]*>(.*?)</div>',
        r'<div[^>]*class="[^"]*article[^"]*"[^>]*>(.*?)</div>',
        r'<div[^>]*id="[^"]*content[^"]*"[^>]*>(.*?)</div>',
        r'<div[^>]*id="[^"]*article[^"]*"[^>]*>(.*?)</div>',
        r'<div[^>]*class="[^"]*post[^"]*"[^>]*>(.*?)</div>',
        r'<div[^>]*class="[^"]*entry[^"]*"[^>]*>(.*?)</div>',
    ]

    for pattern in content_patterns:
        matches = re.findall(pattern, html_content, re.DOTALL | re.IGNORECASE)
        if matches:
            for match in matches:
                main_content += " " + match

    # If we couldn't find specific content containers, use fallback to extract paragraph text
    if not main_content:
        paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", html_content, re.DOTALL)
        main_content = " ".join(paragraphs)

    # Extract text from HTML tags
    main_content = re.sub(r"<[^>]+>", " ", main_content)

    # Clean up the text
    main_content = clean_scraped_text(main_content)

    return main_content

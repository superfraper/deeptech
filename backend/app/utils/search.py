import logging
from typing import Any

import openai
from opensearchpy import OpenSearch

from app.utils.async_utils import run_in_thread
from app.utils.helper_functions import cosine_similarity_numpy


async def hybrid_search(
    query: str,
    os_client: OpenSearch,
    logger: logging.Logger,
    k=5,
    user_id=None,
    filenames=None,
):
    """
    Performs a hybrid search combining both vector-based similarity search and a threshold-based filter
    on the retrieved results to find the most relevant chunks.

    This function first generates an embedding for the given query using OpenAI's API,
    then it searches for similar chunks in the OpenSearch index using k-nearest neighbors (k-NN) search.
    It filters out results based on cosine similarity and returns the most relevant chunks.

    Args:
        query (str): The query string for which to perform the hybrid search.
        os_client (OpenSearch): The OpenSearch client used to perform the search.
        logger (logging): The logging object used to log information during the search process.
        k (int, optional): The number of nearest neighbors to retrieve from OpenSearch. Defaults to 5.
        user_id (str, optional): The user ID to filter results by. Defaults to None.
        filenames (List[str], optional): List of filenames to filter results by. Defaults to None.

    Returns:run_in_thread
        str: A concatenated string containing the most relevant chunks of text that match the query.
    """
    # openai.embeddings.create is synchronous; run it in a thread within async context
    query_embedding_response = await run_in_thread(
        openai.embeddings.create,
        input=query,
        model="text-embedding-ada-002",
    )
    query_embedding = query_embedding_response.data[0].embedding

    # Use the correct KNN query structure for AWS OpenSearch Serverless
    # This is the only format that works with VECTORSEARCH index type
    search_body = {
        "size": k,
        "query": {"knn": {"embedding": {"vector": query_embedding, "k": k}}},
    }

    # If user_id filtering is needed, use a bool query with must clauses
    if user_id or filenames:
        filters = []

        # Add user_id filter if provided
        if user_id:
            filters.append({"term": {"user_id": user_id}})

        # Add filename filter if provided
        if filenames:
            filters.append({"terms": {"original_filename.keyword": filenames}})

        search_body = {
            "size": k,
            "query": {
                "bool": {
                    "must": [{"knn": {"embedding": {"vector": query_embedding, "k": k}}}],
                    "filter": filters,
                }
            },
        }

    # Execute the search
    try:
        search_response = await run_in_thread(os_client.search, index="openai-embeddings", body=search_body)
    except Exception as e:
        logger.error("Search failed: %s", e)
        return {"context": "", "ids": []}
    logger.info("Hybrid search retrieved %d chunks.", len(search_response["hits"]["hits"]))
    relevant_chunks = []
    ids = [hit["_id"] for hit in search_response["hits"]["hits"]]
    for hit in search_response["hits"]["hits"]:
        chunk_embedding = hit["_source"]["embedding"]
        similarity = cosine_similarity_numpy(query_embedding, chunk_embedding)
        if similarity > 0.7:
            relevant_chunks.append(hit["_source"]["text"])
    logger.info(
        "Hybrid search retrieved %d relevant chunks(after threshold).",
        len(relevant_chunks),
    )

    context = "\n".join(relevant_chunks)
    return {"context": context, "ids": ids}


async def search_chunks(
    query: str,
    scraped_chunks: list[dict],
    logger: logging.Logger,
    threshold: float = 0.7,
) -> list[dict[str, Any]]:
    """
    Searches through a list of scraped text chunks and returns those that are most similar to the query.

    This function generates an embedding for the given query using OpenAI's API,
    then compares the query embedding to the embeddings of all scraped chunks using cosine similarity.
    Chunks with similarity above the given threshold are included in the results, sorted by similarity.

    Args:
        query (str): The query string to search for in the scraped chunks.
        scraped_chunks_db (List[Dict]): A list of dictionaries where each dictionary contains 'chunk', and 'embedding' for a chunk.
        threshold (float, optional): The cosine similarity threshold above which a chunk is considered relevant. Defaults to 0.7.

    Returns:
        List[Dict[str, Any]]: A list of dictionaries containing the 'chunk', and
        'similarity' of relevant chunks, sorted by similarity in descending order.
    """
    query_embedding_response = await run_in_thread(openai.embeddings.create, input=query, model="text-embedding-ada-002")
    query_embedding = query_embedding_response.data[0].embedding
    results = []
    for record in scraped_chunks:
        similarity = cosine_similarity_numpy(query_embedding, record["embedding"])
        if similarity > threshold:
            results.append({"chunk": record["chunk"], "similarity": similarity})
    logger.info(
        "Found %d relevant chunks with similarity above %.2f in scraped links",
        len(results),
        threshold,
    )
    results.sort(key=lambda x: x["similarity"], reverse=True)
    return results

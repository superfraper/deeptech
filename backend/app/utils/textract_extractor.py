import logging
import os
import time
import uuid
from typing import Any

import boto3
from botocore.exceptions import ClientError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TextractExtractor:
    """
    A class to handle PDF text extraction using AWS Textract.
    """

    def __init__(self):
        """
        Initialize the Textract client with AWS credentials from environment variables.
        """
        self.aws_access_key = os.getenv("AWS_ACCESS_KEY")
        self.aws_secret_key = os.getenv("AWS_SECRET_KEY")
        self.aws_region = os.getenv("AWS_REGION", "eu-west-1")
        self.s3_bucket = os.getenv("S3_BUCKET")

        # Initialize AWS clients
        self.textract_client = boto3.client(
            "textract",
            aws_access_key_id=self.aws_access_key,
            aws_secret_access_key=self.aws_secret_key,
            region_name=self.aws_region,
        )

        self.s3_client = boto3.client(
            "s3",
            aws_access_key_id=self.aws_access_key,
            aws_secret_access_key=self.aws_secret_key,
            region_name=self.aws_region,
        )

        logger.info(f"TextractExtractor initialized for region: {self.aws_region}")

    def extract_text_from_s3_pdf(self, s3_key: str, bucket_name: str | None = None) -> dict[str, Any]:
        """
        Perform document analysis with layout and table analysis on a PDF file stored in S3 using AWS Textract.

        Args:
            s3_key (str): The S3 key (path) of the PDF file
            bucket_name (str, optional): S3 bucket name. Uses default bucket if not provided.

        Returns:
            Dict[str, Any]: A dictionary containing:
                - 'text': Extracted plain text
                - 'layout_elements': Layout elements like paragraphs, headers, etc.

        Raises:
            ClientError: If AWS operation fails
        """
        bucket = bucket_name or self.s3_bucket
        if not bucket:
            raise ValueError("S3 bucket name must be provided either as parameter or environment variable")

        logger.info(f"Performing document analysis on S3 PDF: s3://{bucket}/{s3_key}")

        try:
            response = self.textract_client.start_document_analysis(
                DocumentLocation={"S3Object": {"Bucket": bucket, "Name": s3_key}},
                FeatureTypes=["LAYOUT"],
            )

            job_id = response["JobId"]
            logger.info(f"Started Textract document analysis job: {job_id}")

            # Poll for job completion and get structured analysis
            analysis_result = self._wait_for_analysis_completion(job_id)

            logger.info("Successfully completed document analysis for S3 PDF")
            return analysis_result

        except ClientError as e:
            logger.error(f"AWS error during S3 PDF document analysis: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error during S3 PDF document analysis: {e}")
            raise

    def extract_text_from_pdf_bytes(self, pdf_bytes: bytes, filename: str = "document.pdf") -> dict[str, Any]:
        """
        Perform document analysis on PDF bytes by uploading to S3 temporarily.

        Args:
            pdf_bytes (bytes): The PDF file content as bytes
            filename (str): The filename for the PDF (used for S3 key generation)

        Returns:
            Dict[str, Any]: A dictionary containing:
                - 'text': Extracted plain text
                - 'layout_elements': Layout elements like paragraphs, headers, etc.

        Raises:
            ClientError: If AWS operation fails
            ValueError: If S3 bucket is not configured
        """
        # …rest of implementation…
        if not self.s3_bucket:
            raise ValueError("S3 bucket name must be configured for PDF bytes processing. Please set the S3_BUCKET environment variable.")

        logger.info(f"Performing document analysis on PDF bytes: {filename}")
        logger.info(f"Using S3 bucket: {self.s3_bucket} for temporary file storage")

        # Generate a unique S3 key
        s3_key = f"temp/textract/{uuid.uuid4()}_{filename}"

        try:
            # Upload bytes to S3
            logger.info(f"Uploading PDF bytes to S3: {s3_key}")
            self.s3_client.put_object(
                Bucket=self.s3_bucket,
                Key=s3_key,
                Body=pdf_bytes,
                ContentType="application/pdf",
            )

            # Perform document analysis via S3
            analysis_result = self.extract_text_from_s3_pdf(s3_key)

            # Clean up temporary S3 file
            self.s3_client.delete_object(Bucket=self.s3_bucket, Key=s3_key)
            logger.info("Cleaned up temporary S3 file")

            return analysis_result

        except Exception as e:
            # Try to clean up S3 file even if extraction failed
            try:
                self.s3_client.delete_object(Bucket=self.s3_bucket, Key=s3_key)
            except Exception:
                logger.error(f"Failed to delete temporary S3 file {s3_key} after error: {e}")
            raise

    def _extract_via_s3(self, pdf_path: str) -> dict[str, Any]:
        """
        Upload a large local PDF to S3 and then perform document analysis via S3 method.

        Args:
            pdf_path (str): Path to the local PDF file

        Returns:
            Dict[str, Any]: Document analysis results including text, tables, and layout
        """
        if not self.s3_bucket:
            raise ValueError("S3 bucket name must be configured for large file processing")

        # Generate a unique S3 key
        file_name = os.path.basename(pdf_path)
        s3_key = f"temp/textract/{uuid.uuid4()}_{file_name}"

        try:
            # Upload to S3
            logger.info(f"Uploading large PDF to S3: {s3_key}")
            self.s3_client.upload_file(pdf_path, self.s3_bucket, s3_key)

            # Perform document analysis via S3
            analysis_result = self.extract_text_from_s3_pdf(s3_key)

            # Clean up temporary S3 file
            self.s3_client.delete_object(Bucket=self.s3_bucket, Key=s3_key)
            logger.info("Cleaned up temporary S3 file")

            return analysis_result

        except Exception as e:
            # Try to clean up S3 file even if extraction failed
            try:
                self.s3_client.delete_object(Bucket=self.s3_bucket, Key=s3_key)
            except Exception:
                logger.error(f"Failed to delete temporary S3 file {s3_key} after error: {e}")
                pass
            raise

    def _wait_for_job_completion(self, job_id: str, max_wait_time: int = 300) -> str:
        """
        Wait for a Textract job to complete and return the extracted text.

        Args:
            job_id (str): The Textract job ID
            max_wait_time (int): Maximum time to wait in seconds (default: 5 minutes)

        Returns:
            str: Extracted text from the completed job
        """
        start_time = time.time()

        while time.time() - start_time < max_wait_time:
            try:
                response = self.textract_client.get_document_text_detection(JobId=job_id)
                status = response["JobStatus"]

                if status == "SUCCEEDED":
                    logger.info("Textract job completed successfully")
                    return self._parse_textract_job_response(response, job_id)
                elif status == "FAILED":
                    raise Exception(f"Textract job failed: {response.get('StatusMessage', 'Unknown error')}")
                elif status == "IN_PROGRESS":
                    logger.info("Textract job still in progress, waiting...")
                    time.sleep(5)
                else:
                    logger.warning(f"Unknown job status: {status}")
                    time.sleep(5)

            except ClientError as e:
                logger.error(f"Error checking job status: {e}")
                raise

        raise TimeoutError(f"Textract job did not complete within {max_wait_time} seconds")

    def _wait_for_analysis_completion(self, job_id: str, max_wait_time: int = 300) -> dict[str, Any]:
        """
        Wait for a Textract document analysis job to complete and return the structured results.

        Args:
            job_id (str): The Textract job ID
            max_wait_time (int): Maximum time to wait in seconds (default: 5 minutes)

        Returns:
            Dict[str, Any]: Structured analysis results including text, tables, and layout
        """
        start_time = time.time()

        while time.time() - start_time < max_wait_time:
            try:
                response = self.textract_client.get_document_analysis(JobId=job_id)
                status = response["JobStatus"]

                if status == "SUCCEEDED":
                    logger.info("Textract document analysis job completed successfully")
                    return self._parse_analysis_response(response, job_id)
                elif status == "FAILED":
                    raise Exception(f"Textract analysis job failed: {response.get('StatusMessage', 'Unknown error')}")
                elif status == "IN_PROGRESS":
                    logger.info("Textract analysis job still in progress, waiting...")
                    time.sleep(5)
                else:
                    logger.warning(f"Unknown job status: {status}")
                    time.sleep(5)

            except ClientError as e:
                logger.error(f"Error checking analysis job status: {e}")
                raise

        raise TimeoutError(f"Textract analysis job did not complete within {max_wait_time} seconds")

    def _parse_textract_response(self, response: dict[str, Any]) -> str:
        """
        Parse the response from Textract detect_document_text API.

        Args:
            response (dict): The response from Textract API

        Returns:
            str: Extracted text
        """
        text_blocks = []

        for block in response.get("Blocks", []):
            if block["BlockType"] == "LINE":
                text_blocks.append(block["Text"])

        return "\n".join(text_blocks)

    def _parse_textract_job_response(self, response: dict[str, Any], job_id: str) -> str:
        """
        Parse the response from Textract get_document_text_detection API.

        Args:
            response (dict): The response from Textract API
            job_id (str): The job ID for handling pagination

        Returns:
            str: Extracted text
        """
        text_blocks = []

        # Get all blocks from the first page
        for block in response.get("Blocks", []):
            if block["BlockType"] == "LINE":
                text_blocks.append(block["Text"])

        # Handle multi-page documents
        next_token = response.get("NextToken")
        while next_token:
            try:
                next_response = self.textract_client.get_document_text_detection(JobId=job_id, NextToken=next_token)

                for block in next_response.get("Blocks", []):
                    if block["BlockType"] == "LINE":
                        text_blocks.append(block["Text"])

                next_token = next_response.get("NextToken")

            except ClientError as e:
                logger.error(f"Error getting next page: {e}")
                break

        return "\n".join(text_blocks)

    def _parse_analysis_response(self, response: dict[str, Any], job_id: str) -> dict[str, Any]:
        """
        Parse the response from Textract get_document_analysis API.

        Args:
            response (dict): The response from Textract API
            job_id (str): The job ID for handling pagination

        Returns:
            Dict[str, Any]: Structured analysis results
        """
        # Collect all blocks from all pages
        all_blocks = []
        all_blocks.extend(response.get("Blocks", []))

        # Handle multi-page documents
        next_token = response.get("NextToken")
        while next_token:
            try:
                next_response = self.textract_client.get_document_analysis(JobId=job_id, NextToken=next_token)

                all_blocks.extend(next_response.get("Blocks", []))
                next_token = next_response.get("NextToken")

            except ClientError as e:
                logger.error(f"Error getting next page: {e}")
                break

        # Parse different types of content
        result = {
            "text": self._extract_text_from_blocks(all_blocks),
            "layout_elements": self._extract_layout_elements_from_blocks(all_blocks),
        }

        logger.info(f"Parsed {len(all_blocks)} blocks into structured content")

        return result

    def _extract_text_from_blocks(self, blocks: list) -> str:
        """Extract plain text from blocks."""
        text_blocks = []
        for block in blocks:
            if block["BlockType"] == "LINE":
                text_blocks.append(block["Text"])
        return "\n".join(text_blocks)

    def _extract_layout_elements_from_blocks(self, blocks: list) -> list:
        """Extract layout elements from blocks."""
        layout_elements = []
        layout_blocks = [block for block in blocks if "LAYOUT" in block.get("BlockType", "")]

        for block in layout_blocks:
            element = {
                "id": block["Id"],
                "type": block.get("BlockType", "UNKNOWN"),
                "text": self._get_text_from_block(block, blocks),
                "confidence": block.get("Confidence", 0),
                "geometry": block.get("Geometry", {}),
            }
            layout_elements.append(element)

        return layout_elements

    def _get_text_from_block(self, block: dict, all_blocks: list) -> str:
        """Get text content from a block by following relationships."""
        text_parts = []

        if "Relationships" in block:
            for relationship in block["Relationships"]:
                if relationship["Type"] == "CHILD":
                    for child_id in relationship["Ids"]:
                        child_block = next((b for b in all_blocks if b["Id"] == child_id), None)
                        if child_block and (child_block["BlockType"] == "WORD" or child_block["BlockType"] == "LINE"):
                            text_parts.append(child_block["Text"])

        return " ".join(text_parts)


def extract_text_from_pdf(pdf_path: str) -> dict[str, Any]:
    """
    Convenience function to perform document analysis on a PDF file.

    Args:
        pdf_path (str): Path to the PDF file

    Returns:
        Dict[str, Any]: Document analysis results including text, layout
    """
    extractor = TextractExtractor()
    with open(pdf_path, "rb") as f:
        file_bytes = f.read()
    filename = os.path.basename(pdf_path)

    # Local file
    return extractor.extract_text_from_pdf_bytes(pdf_bytes=file_bytes, filename=filename)


def extract_text_from_pdf_bytes(pdf_bytes: bytes, filename: str = "document.pdf") -> dict[str, Any]:
    """
    Convenience function to perform document analysis on PDF bytes.

    Args:
        pdf_bytes (bytes): The PDF file content as bytes
        filename (str): The filename for the PDF (used for S3 key generation)

    Returns:
        Dict[str, Any]: Document analysis results including text, layout
    """
    extractor = TextractExtractor()
    return extractor.extract_text_from_pdf_bytes(pdf_bytes, filename)


def create_chunks_from_layout(layout_elements: list[dict[str, Any]], max_chunk_size: int = 4_000) -> list[dict[str, Any]]:
    """Create chunks from layout elements respecting document structure."""
    chunks = []
    current_chunk: list[dict[str, Any]] = []
    current_size = 0

    for element in layout_elements:
        text_length = len(element["text"])
        layout_type = element.get("type", "")

        # NOTE: we could enforce longer chunks sice sometimes there is TITLE -> HEADER etc.
        should_break = (layout_type in ["LAYOUT_TITLE", "LAYOUT_SECTION_HEADER"] and current_chunk) or (
            current_size + text_length > max_chunk_size and current_chunk
        )

        if should_break:
            chunks.append(
                {
                    "elements": current_chunk.copy(),
                    "text": " ".join(elem["text"] for elem in current_chunk),
                    "size": current_size,
                }
            )
            current_chunk = [element]
            current_size = text_length
        else:
            current_chunk.append(element)
            current_size += text_length

    if current_chunk:
        chunks.append(
            {
                "elements": current_chunk.copy(),
                "text": " ".join(elem["text"] for elem in current_chunk),
                "size": current_size,
            }
        )

    return chunks

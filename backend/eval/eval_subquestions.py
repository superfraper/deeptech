import logging
import os
import sqlite3
from dataclasses import dataclass
from typing import Any

from deepeval.metrics import (
    AnswerRelevancyMetric,
    BiasMetric,
    FaithfulnessMetric,
    HallucinationMetric,
    ToxicityMetric,
)
from dotenv import load_dotenv

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)
load_dotenv()


@dataclass
class SubquestionData:
    """Data structure for subquestion evaluation data"""

    field_id: str
    question: str
    question_type: str
    relevant_field: str
    relevant_variable: str
    actual_output: str | None = None
    context: str | None = None
    table_name: str = ""


class SubquestionEvaluator:
    """Evaluator for subquestions database using DeepEval"""

    def __init__(self, db_path: str | None = None, default_table: str = "oth"):
        """Initialize the evaluator with database path and default table name"""
        self.db_path = db_path
        self.default_table = default_table
        self.conn = None
        self.metrics = self._initialize_metrics()
        self._initialize_db_connection()

    def _initialize_metrics(self) -> list:
        """Initialize DeepEval metrics"""
        return [
            AnswerRelevancyMetric(threshold=0.7),
            FaithfulnessMetric(threshold=0.7),
            HallucinationMetric(threshold=0.3),
            BiasMetric(threshold=0.3),
            ToxicityMetric(threshold=0.3),
        ]

    def _initialize_db_connection(self):
        """Initialize database connection"""
        if self.db_path and os.path.exists(self.db_path):
            try:
                self.conn = sqlite3.connect(self.db_path)
                logger.info(f"Database connection established: {self.db_path}")
            except Exception as e:
                logger.error(f"Failed to connect to database: {e}")
                self.conn = None
        else:
            logger.warning(f"Database path not found or not provided: {self.db_path}")
            self.conn = None

    def __del__(self):
        """Clean up database connection"""
        if self.conn:
            self.conn.close()

    def load_subquestions_from_table(self, table_name: str, limit: int | None = None) -> list[SubquestionData]:
        """Load subquestions from a specific table"""
        try:
            if not os.path.exists(self.db_path):
                logger.error(f"Database not found: {self.db_path}")
                return []

            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Check if table exists
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            )
            if not cursor.fetchone():
                logger.error(f"Table '{table_name}' not found in database")
                conn.close()
                return []

            cursor.execute(f"PRAGMA table_info({table_name})")
            columns = [column[1] for column in cursor.fetchall()]
            logger.info(f"Table '{table_name}' columns: {columns}")

            base_columns = [
                "field_id",
                "question",
                "type",
                "relevant_field",
                "relevant_variable",
            ]
            available_columns = [col for col in base_columns if col in columns]

            optional_columns = ["actual_output", "context"]
            for col in optional_columns:
                if col in columns:
                    available_columns.append(col)

            query = f"SELECT {', '.join(available_columns)} FROM {table_name}"
            if limit:
                query += f" LIMIT {limit}"

            cursor.execute(query)
            rows = cursor.fetchall()

            subquestions = []
            for row in rows:
                data = dict(zip(available_columns, row, strict=False))
                subquestion = SubquestionData(
                    field_id=data.get("field_id", ""),
                    question=data.get("question", ""),
                    question_type=data.get("type", ""),
                    relevant_field=data.get("relevant_field", ""),
                    relevant_variable=data.get("relevant_variable", ""),
                    actual_output=data.get("actual_output"),
                    context=data.get("context"),
                    table_name=table_name,
                )
                subquestions.append(subquestion)

            conn.close()
            logger.info(f"Loaded {len(subquestions)} subquestions from table '{table_name}'")
            return subquestions

        except Exception as e:
            logger.error(f"Error loading subquestions from table '{table_name}': {e}")
            return []

    async def evaluate_question_simple(self, question: str, question_type: str, field_id: str = "test") -> dict[str, Any]:
        """
        Simplified question evaluation function for DeepEval testing

        Args:
            question: The question text to evaluate
            question_type: Type of question ('rag' or 'hardcoded')
            field_id: Field ID for context (default: 'test')

        Returns:
            Dict with answer, context, and metadata
        """
        try:
            if question_type == "hardcoded":
                # For hardcoded questions, the answer is the question itself
                return {
                    "question": question,
                    "answer": question,
                    "context": "Hardcoded response",
                    "question_type": question_type,
                    "field_id": field_id,
                    "confident": True,
                    "error": None,
                }

            elif question_type == "rag":
                # Import here to avoid circular imports
                from app.config import get_opensearch_client
                from app.utils.generate import answer_rag_question

                os_client = get_opensearch_client()

                # Call the RAG function with minimal parameters
                rag_result = await answer_rag_question(
                    field_id=field_id,
                    question_text=question,
                    scrapedChunks=[],  # Empty list - no scraped content for evaluation
                    os_client=os_client,
                    logger=logger,
                    user_id=None,  # Test user ID
                )

                answer_obj = rag_result["answer"]
                return {
                    "question": question,
                    "answer": answer_obj.answer,
                    "context": f"OpenSearch context (IDs: {rag_result.get('ids', [])})",
                    "question_type": question_type,
                    "field_id": field_id,
                    "confident": answer_obj.confident,
                    "search_ids": rag_result.get("ids", []),
                    "error": None,
                }

            else:
                return {
                    "question": question,
                    "answer": f"Unsupported question type: {question_type}",
                    "context": None,
                    "question_type": question_type,
                    "field_id": field_id,
                    "confident": False,
                    "error": f"Unknown question type: {question_type}",
                }

        except Exception as e:
            logger.error(f"Error evaluating question '{question}': {e}")
            return {
                "question": question,
                "answer": f"Error: {e!s}",
                "context": None,
                "question_type": question_type,
                "field_id": field_id,
                "confident": False,
                "error": str(e),
            }

    async def evaluate_sample_questions(self, field_ids: list[str] | None = None, table_name: str | None = None) -> dict[str, Any]:
        """
        Evaluate sample questions from specific field_ids

        Args:
            field_ids: List of field IDs to evaluate (defaults to ["A.12", "I.09", "I.6"])
            table_name: Table name to query (defaults to self.default_table)
        """
        if field_ids is None:
            field_ids = ["A.12", "I.09", "I.6"]  # Small, medium, large question sets

        if table_name is None:
            table_name = self.default_table

        logger.info(f"Evaluating sample questions from fields: {field_ids} in table: {table_name}")

        all_results = []
        total_questions = 0

        cursor = self.conn.cursor()

        for field_id in field_ids:
            query = f"""
                SELECT field_id, question, type, relevant_field, relevant_variable
                FROM {table_name}
                WHERE field_id = ? AND type IN ('rag', 'hardcoded')
            """
            cursor.execute(query, (field_id,))
            rows = cursor.fetchall()

            field_results = []
            for row in rows:
                subq_data = SubquestionData(
                    field_id=row[0],
                    question=row[1],
                    question_type=row[2],
                    relevant_field=row[3],
                    relevant_variable=row[4],
                    table_name=table_name,
                )

                # Evaluate this question
                result = await self.evaluate_question_simple(
                    question=subq_data.question,
                    question_type=subq_data.question_type,
                    field_id=subq_data.field_id,
                )

                field_results.append(result)
                total_questions += 1

                logger.info(f"Evaluated {field_id} question: {result['confident']} - {result['answer'][:100]}...")

            all_results.extend(field_results)
            logger.info(f"Completed {len(field_results)} questions for field {field_id}")

        # Summary statistics
        successful = len([r for r in all_results if r["error"] is None])
        confident = len([r for r in all_results if r["confident"]])
        rag_questions = len([r for r in all_results if r["question_type"] == "rag"])
        hardcoded_questions = len([r for r in all_results if r["question_type"] == "hardcoded"])

        return {
            "evaluation_summary": {
                "total_questions": total_questions,
                "successful_evaluations": successful,
                "confident_answers": confident,
                "rag_questions": rag_questions,
                "hardcoded_questions": hardcoded_questions,
                "field_ids": field_ids,
            },
            "detailed_results": all_results,
        }


if __name__ == "__main__":
    import asyncio

    async def main():
        evaluator = SubquestionEvaluator("data/databases/subquestions.db")

        # Test loading questions from table
        print("=== Testing question loading ===")
        sample_questions = evaluator.load_subquestions_from_table("oth", limit=5)
        print(f"Loaded {len(sample_questions)} sample questions")
        for sq in sample_questions[:3]:
            print(f"- {sq.field_id}: {sq.question_type} - {sq.question[:60]}...")

        # Test simple evaluation
        print("\n=== Testing simple evaluation ===")
        results = await evaluator.evaluate_sample_questions()  # Start with just one field
        print(f"Evaluation completed: {results['evaluation_summary']}")

        # Show first result details
        if results["detailed_results"]:
            first_result = results["detailed_results"][0]
            print(f"Sample result: {first_result['field_id']} - {first_result['confident']} - {first_result['answer'][:100]}...")

    asyncio.run(main())

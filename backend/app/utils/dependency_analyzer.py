import logging
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, ClassVar

from app.utils.json_loader import load_guidelines, load_subquestions

logger = logging.getLogger("dependency_analyzer")


@dataclass
class FieldDependency:
    """Represents a field and its dependencies"""

    field_id: str
    field_name: str
    depends_on: set[str]
    dependents: set[str]
    level: int = -1  # Execution level, -1 means not calculated yet


class DependencyAnalyzer:
    """Analyzes field dependencies and creates execution levels for parallel processing"""

    ALLOWED_TOKEN_CLASSIFICATIONS: ClassVar[set[str]] = {"OTH", "EMT", "ART"}

    def __init__(self, token_classification: str = "OTH"):
        self.token_classification = token_classification.upper()
        # Validate token_classification against allowed values
        if self.token_classification not in self.ALLOWED_TOKEN_CLASSIFICATIONS:
            raise ValueError(f"Invalid token_classification: {token_classification}. Must be one of {self.ALLOWED_TOKEN_CLASSIFICATIONS}")
        self.fields: dict[str, FieldDependency] = {}
        self.execution_levels: list[list[str]] = []
        self.dependency_graph: dict[str, set[str]] = defaultdict(set)
        self.reverse_graph: dict[str, set[str]] = defaultdict(set)

    def analyze_dependencies(self) -> dict[str, Any]:
        """
        Main method to analyze all field dependencies and create execution levels
        Returns analysis results including execution levels and statistics
        """
        logger.info(f"Starting dependency analysis for {self.token_classification}")

        # Step 1: Load all fields and their dependencies
        self._load_field_dependencies()

        # Step 2: Build dependency graphs
        self._build_dependency_graphs()

        # Step 3: Detect and handle circular dependencies
        cycles = self._detect_cycles()
        if cycles:
            logger.warning(f"Found {len(cycles)} circular dependencies: {cycles}")
            self._handle_cycles(cycles)

        # Step 4: Calculate execution levels using topological sort
        self._calculate_execution_levels()

        # Step 5: Generate analysis report
        return self._generate_analysis_report()

    def _load_field_dependencies(self):
        """Load field information and dependencies from databases"""
        logger.info("Loading field dependencies from databases")

        # Load basic field information
        fields_info = self._get_fields_info()

        # Initialize field dependency objects
        for field_id, field_name, _, _ in fields_info:
            self.fields[field_id] = FieldDependency(
                field_id=field_id,
                field_name=field_name,
                depends_on=set(),
                dependents=set(),
            )

        # Load dependency relationships from subquestions
        self._load_field_question_dependencies()

        logger.info(f"Loaded {len(self.fields)} fields with dependencies")

    def _get_fields_info(self) -> list[tuple]:
        """Get basic field information from guidelines JSON"""
        try:
            tc = self.token_classification
            items = load_guidelines(tc)
            fields = [
                (
                    str(g.no),
                    g.field,
                    g.section_name,
                    g.content_to_be_reported,
                )
                for g in items
            ]
            return fields
        except Exception as e:
            logger.error(f"Error loading fields info from JSON: {e}")
            raise

    def _load_field_question_dependencies(self):
        """Load field dependencies from subquestions JSON"""
        try:
            tc = self.token_classification
            items = load_subquestions(tc)
            for sq in items:
                if sq.type == "whitepaper" and sq.relevant_field:
                    field_id = str(sq.field_id).strip()
                    relevant_fields_str = sq.relevant_field
                    if field_id in self.fields and relevant_fields_str:
                        # Parse comma-separated relevant fields
                        relevant_fields = [f.strip() for f in relevant_fields_str.split(",") if f.strip()]
                        for dep_field in relevant_fields:
                            dep_field_id = dep_field.strip()
                            if dep_field_id in self.fields:
                                # field_id depends on dep_field
                                self.fields[field_id].depends_on.add(dep_field_id)
                                self.fields[dep_field_id].dependents.add(field_id)
                                logger.debug(f"Field {field_id} depends on {dep_field}")

            # Log dependency statistics
            dependent_fields = sum(1 for f in self.fields.values() if f.depends_on)
            total_dependencies = sum(len(f.depends_on) for f in self.fields.values())

            logger.info(f"Loaded dependencies: {dependent_fields} fields have dependencies, {total_dependencies} total dependency relationships")
        except Exception as e:
            logger.error(f"Error loading field dependencies from JSON: {e}")
            raise

    def _build_dependency_graphs(self):
        """Build forward and reverse dependency graphs for cycle detection"""
        for field_id, field_dep in self.fields.items():
            self.dependency_graph[field_id] = field_dep.depends_on.copy()
            for dep in field_dep.depends_on:
                self.reverse_graph[dep].add(field_id)

    def _detect_cycles(self) -> list[list[str]]:
        """Detect circular dependencies using DFS"""
        visited = set()
        rec_stack = set()
        cycles = []

        def dfs(node: str, path: list[str]) -> bool:
            if node in rec_stack:
                # Found a cycle
                cycle_start = path.index(node)
                cycle = [*path[cycle_start:], node]
                cycles.append(cycle)
                return True

            if node in visited:
                return False

            visited.add(node)
            rec_stack.add(node)
            path.append(node)

            for neighbor in self.dependency_graph[node]:
                if dfs(neighbor, path):
                    return True

            rec_stack.remove(node)
            path.pop()
            return False

        for field_id in self.fields:
            if field_id not in visited:
                dfs(field_id, [])

        return cycles

    def _handle_cycles(self, cycles: list[list[str]]):
        """Handle circular dependencies by breaking them strategically"""
        for cycle in cycles:
            logger.warning(f"Breaking circular dependency: {' -> '.join(cycle)}")

            # Strategy: Remove the dependency with the least impact
            # For now, remove the last dependency in the cycle
            if len(cycle) >= 2:
                dependent = cycle[-2]
                dependency = cycle[-1]

                if dependent in self.fields and dependency in self.fields[dependent].depends_on:
                    self.fields[dependent].depends_on.remove(dependency)
                    self.fields[dependency].dependents.discard(dependent)
                    self.dependency_graph[dependent].discard(dependency)
                    self.reverse_graph[dependency].discard(dependent)

                    logger.info(f"Removed dependency: {dependent} no longer depends on {dependency}")

    def _calculate_execution_levels(self):
        """Calculate execution levels using topological sort (Kahn's algorithm)"""
        logger.info("Calculating execution levels using topological sort")

        # Initialize in-degree count for each field
        in_degree = {field_id: len(deps.depends_on) for field_id, deps in self.fields.items()}

        # Start with fields that have no dependencies (level 0)
        queue = deque([field_id for field_id, degree in in_degree.items() if degree == 0])
        current_level = 0

        while queue:
            # Process all fields at current level
            level_fields = []
            level_size = len(queue)

            for _ in range(level_size):
                field_id = queue.popleft()
                level_fields.append(field_id)
                self.fields[field_id].level = current_level

                # Reduce in-degree for all dependents
                for dependent in self.fields[field_id].dependents:
                    in_degree[dependent] -= 1
                    if in_degree[dependent] == 0:
                        queue.append(dependent)

            self.execution_levels.append(level_fields)
            logger.info(f"Level {current_level}: {len(level_fields)} fields can be processed in parallel")
            current_level += 1

        # Check for remaining fields (shouldn't happen if no cycles)
        remaining_fields = [f for f, degree in in_degree.items() if degree > 0]
        if remaining_fields:
            logger.error(f"Found {len(remaining_fields)} fields with unresolved dependencies: {remaining_fields}")
            # Add them to the last level as fallback
            if remaining_fields:
                self.execution_levels.append(remaining_fields)
                for field_id in remaining_fields:
                    self.fields[field_id].level = current_level

    def _generate_analysis_report(self) -> dict[str, Any]:
        """Generate comprehensive analysis report"""
        total_fields = len(self.fields)
        independent_fields = len(self.execution_levels[0]) if self.execution_levels else 0
        dependent_fields = total_fields - independent_fields
        max_dependency_depth = len(self.execution_levels) - 1 if self.execution_levels else 0

        # Calculate parallelization potential
        sequential_time_estimate = total_fields  # Assume 1 time unit per field
        parallel_time_estimate = len(self.execution_levels)  # One time unit per level
        speedup_potential = sequential_time_estimate / parallel_time_estimate if parallel_time_estimate > 0 else 1

        # Field statistics
        dependency_stats = {
            "fields_with_no_dependencies": independent_fields,
            "fields_with_dependencies": dependent_fields,
            "max_dependencies_per_field": (max(len(f.depends_on) for f in self.fields.values()) if self.fields else 0),
            "avg_dependencies_per_field": (sum(len(f.depends_on) for f in self.fields.values()) / total_fields if total_fields > 0 else 0),
        }

        # Level distribution
        level_distribution = {f"level_{i}": len(fields) for i, fields in enumerate(self.execution_levels)}

        report = {
            "token_classification": self.token_classification,
            "total_fields": total_fields,
            "execution_levels": len(self.execution_levels),
            "max_dependency_depth": max_dependency_depth,
            "speedup_potential": round(speedup_potential, 2),
            "dependency_stats": dependency_stats,
            "level_distribution": level_distribution,
            "execution_plan": self.execution_levels,
            "field_details": {
                field_id: {
                    "name": field.field_name,
                    "level": field.level,
                    "depends_on": list(field.depends_on),
                    "dependents": list(field.dependents),
                }
                for field_id, field in self.fields.items()
            },
        }

        logger.info(f"Analysis complete: {total_fields} fields, {len(self.execution_levels)} levels, {speedup_potential:.2f}x potential speedup")
        return report

    def get_execution_levels(self) -> list[list[str]]:
        """Get the calculated execution levels"""
        return self.execution_levels

    def get_field_level(self, field_id: str) -> int:
        """Get the execution level for a specific field"""
        return self.fields.get(field_id, FieldDependency("", "", set(), set())).level

    def get_field_dependencies(self, field_id: str) -> set[str]:
        """Get the dependencies for a specific field"""
        return self.fields.get(field_id, FieldDependency("", "", set(), set())).depends_on

    def is_field_ready(self, field_id: str, completed_fields: set[str]) -> bool:
        """Check if a field is ready to be processed based on completed fields"""
        if field_id not in self.fields:
            return False

        dependencies = self.fields[field_id].depends_on
        return dependencies.issubset(completed_fields)

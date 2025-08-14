"""Code analysis tool for LLM."""

import ast
import os
from pathlib import Path
from typing import Any, Dict, List

from ..config import HashConfig
from .base import Tool


class CodeAnalysisTool(Tool):
    """Tool for analyzing code files and projects."""

    def get_name(self) -> str:
        return "analyze_code"

    def get_description(self) -> str:
        return "Analyze code files for structure, issues, and insights"

    async def execute(self, arguments: Dict[str, Any], config: HashConfig) -> str:
        """Analyze code based on the provided arguments."""

        file_path = arguments.get("file_path", "")
        analysis_type = arguments.get("analysis_type", "structure")

        if not file_path:
            return "No file path provided for code analysis"

        try:
            path = Path(file_path).resolve()

            if not path.exists():
                return f"File does not exist: {path}"

            if not path.is_file():
                return f"Path is not a file: {path}"

            # Determine file type
            file_extension = path.suffix.lower()

            if file_extension == ".py":
                return await self._analyze_python_file(path, analysis_type)
            elif file_extension in [".js", ".ts", ".jsx", ".tsx"]:
                return await self._analyze_javascript_file(path, analysis_type)
            elif file_extension in [".java"]:
                return await self._analyze_java_file(path, analysis_type)
            else:
                return await self._analyze_generic_file(path, analysis_type)

        except Exception as e:
            return f"Error analyzing code: {e}"

    async def _analyze_python_file(self, path: Path, analysis_type: str) -> str:
        """Analyze a Python file using AST."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()

            # Parse Python AST
            tree = ast.parse(content)

            if analysis_type == "structure":
                return self._get_python_structure(tree, path)
            elif analysis_type == "complexity":
                return self._get_python_complexity(tree, path)
            elif analysis_type == "issues":
                return self._get_python_issues(tree, content, path)
            else:
                return self._get_python_overview(tree, content, path)

        except SyntaxError as e:
            return f"Python syntax error in {path}:\\nLine {e.lineno}: {e.msg}"
        except Exception as e:
            return f"Error analyzing Python file: {e}"

    def _get_python_structure(self, tree: ast.AST, path: Path) -> str:
        """Get Python file structure."""
        classes = []
        functions = []
        imports = []

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                methods = [n.name for n in node.body if isinstance(n, ast.FunctionDef)]
                classes.append(f"class {node.name}: {len(methods)} methods")
            elif isinstance(node, ast.FunctionDef) and not any(
                isinstance(parent, ast.ClassDef) for parent in ast.walk(tree)
            ):
                functions.append(f"def {node.name}()")
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.append(f"import {alias.name}")
                else:
                    module = node.module or ""
                    for alias in node.names:
                        imports.append(f"from {module} import {alias.name}")

        result = f"Python file structure for {path.name}:\\n\\n"

        if imports:
            result += f"Imports ({len(imports)}):\\n"
            for imp in imports[:10]:  # Limit output
                result += f"  {imp}\\n"
            if len(imports) > 10:
                result += f"  ... and {len(imports) - 10} more\\n"
            result += "\\n"

        if classes:
            result += f"Classes ({len(classes)}):\\n"
            for cls in classes:
                result += f"  {cls}\\n"
            result += "\\n"

        if functions:
            result += f"Functions ({len(functions)}):\\n"
            for func in functions:
                result += f"  {func}\\n"

        return result.strip()

    def _get_python_complexity(self, tree: ast.AST, path: Path) -> str:
        """Calculate basic complexity metrics."""
        metrics = {
            "lines": 0,
            "classes": 0,
            "functions": 0,
            "loops": 0,
            "conditions": 0,
            "imports": 0,
        }

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                metrics["classes"] += 1
            elif isinstance(node, ast.FunctionDef):
                metrics["functions"] += 1
            elif isinstance(node, (ast.For, ast.While)):
                metrics["loops"] += 1
            elif isinstance(node, (ast.If, ast.IfExp)):
                metrics["conditions"] += 1
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                metrics["imports"] += 1

        # Estimate lines (rough)
        try:
            with open(path, "r") as f:
                metrics["lines"] = len(
                    [
                        line
                        for line in f
                        if line.strip() and not line.strip().startswith("#")
                    ]
                )
        except:
            pass

        result = f"Python complexity analysis for {path.name}:\\n\\n"
        result += f"Lines of code (approx): {metrics['lines']}\\n"
        result += f"Classes: {metrics['classes']}\\n"
        result += f"Functions: {metrics['functions']}\\n"
        result += f"Loops: {metrics['loops']}\\n"
        result += f"Conditionals: {metrics['conditions']}\\n"
        result += f"Imports: {metrics['imports']}\\n"

        # Basic complexity assessment
        complexity_score = (
            metrics["functions"] * 2
            + metrics["classes"] * 3
            + metrics["loops"] * 2
            + metrics["conditions"] * 1.5
        )

        if complexity_score < 20:
            assessment = "Low complexity"
        elif complexity_score < 50:
            assessment = "Medium complexity"
        else:
            assessment = "High complexity"

        result += (
            f"\\nComplexity assessment: {assessment} (score: {complexity_score:.1f})"
        )

        return result

    def _get_python_issues(self, tree: ast.AST, content: str, path: Path) -> str:
        """Identify potential issues in Python code."""
        issues = []

        # Check for common issues
        lines = content.split("\\n")

        for i, line in enumerate(lines, 1):
            # Long lines
            if len(line) > 120:
                issues.append(f"Line {i}: Line too long ({len(line)} chars)")

            # Multiple statements on one line
            if ";" in line and not line.strip().startswith("#"):
                issues.append(f"Line {i}: Multiple statements on one line")

        # AST-based checks
        for node in ast.walk(tree):
            # Empty except blocks
            if isinstance(node, ast.ExceptHandler):
                if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                    issues.append(f"Line {node.lineno}: Empty except block")

            # Unused imports (basic check)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.asname or alias.name
                    if content.count(name) <= 1:  # Only appears in import
                        issues.append(
                            f"Line {node.lineno}: Potentially unused import '{name}'"
                        )

        result = f"Python code issues for {path.name}:\\n\\n"

        if issues:
            for issue in issues[:15]:  # Limit output
                result += f"⚠️  {issue}\\n"
            if len(issues) > 15:
                result += f"... and {len(issues) - 15} more issues\\n"
        else:
            result += "✅ No obvious issues found\\n"

        return result

    def _get_python_overview(self, tree: ast.AST, content: str, path: Path) -> str:
        """Get comprehensive Python file overview."""
        structure = self._get_python_structure(tree, path)
        complexity = self._get_python_complexity(tree, path)
        issues = self._get_python_issues(tree, content, path)

        return f"{structure}\\n\\n{complexity}\\n\\n{issues}"

    async def _analyze_javascript_file(self, path: Path, analysis_type: str) -> str:
        """Basic JavaScript file analysis (without full AST parsing)."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()

            lines = content.split("\\n")

            # Basic metrics
            functions = len(
                [line for line in lines if "function" in line or "=>" in line]
            )
            classes = len([line for line in lines if line.strip().startswith("class ")])
            imports = len(
                [
                    line
                    for line in lines
                    if line.strip().startswith("import ")
                    or line.strip().startswith("const ")
                    and "require(" in line
                ]
            )

            result = f"JavaScript analysis for {path.name}:\\n\\n"
            result += f"Total lines: {len(lines)}\\n"
            result += f"Functions (approx): {functions}\\n"
            result += f"Classes (approx): {classes}\\n"
            result += f"Imports/Requires (approx): {imports}\\n"

            return result

        except Exception as e:
            return f"Error analyzing JavaScript file: {e}"

    async def _analyze_java_file(self, path: Path, analysis_type: str) -> str:
        """Basic Java file analysis."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()

            lines = content.split("\\n")

            # Basic metrics
            classes = len(
                [line for line in lines if "class " in line and "public" in line]
            )
            methods = len(
                [
                    line
                    for line in lines
                    if ("public " in line or "private " in line)
                    and "(" in line
                    and ")" in line
                ]
            )
            imports = len(
                [line for line in lines if line.strip().startswith("import ")]
            )

            result = f"Java analysis for {path.name}:\\n\\n"
            result += f"Total lines: {len(lines)}\\n"
            result += f"Classes (approx): {classes}\\n"
            result += f"Methods (approx): {methods}\\n"
            result += f"Imports: {imports}\\n"

            return result

        except Exception as e:
            return f"Error analyzing Java file: {e}"

    async def _analyze_generic_file(self, path: Path, analysis_type: str) -> str:
        """Generic file analysis for unsupported file types."""
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            lines = content.split("\\n")

            result = f"Generic file analysis for {path.name}:\\n\\n"
            result += f"File type: {path.suffix}\\n"
            result += f"File size: {len(content)} characters\\n"
            result += f"Total lines: {len(lines)}\\n"
            result += (
                f"Non-empty lines: {len([line for line in lines if line.strip()])}\\n"
            )

            # Show first few lines as preview
            preview_lines = lines[:10]
            result += f"\\nPreview (first 10 lines):\\n"
            for i, line in enumerate(preview_lines, 1):
                result += f"{i:2d}: {line[:80]}{'...' if len(line) > 80 else ''}\\n"

            return result

        except Exception as e:
            return f"Error analyzing file: {e}"

    def requires_confirmation(self) -> bool:
        """Code analysis doesn't require confirmation."""
        return False

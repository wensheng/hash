"""Fix command implementation for coding assistance."""

from typing import List

from ..command_proxy import Command
from ..config import HashConfig


class FixCommand(Command):
    """Command for coding-specialized assistance."""

    def execute(self, args: List[str], config: HashConfig) -> str:
        """Execute fix command for coding assistance."""

        if not args:
            return self.get_help()

        # Join all arguments into a description
        description = " ".join(args)

        # Create a specialized prompt for coding assistance
        coding_prompt = f"""I need help with a coding issue. Please provide a practical solution:

Issue: {description}

Please provide:
1. A clear explanation of the problem
2. A concrete solution with code examples if applicable
3. Any relevant best practices or alternatives
4. Commands to run if needed (I can execute them with your guidance)

Focus on being practical and actionable."""

        # This would normally trigger LLM mode with the specialized prompt
        # For now, return a message indicating the prompt would be processed
        return f"Coding assistance request: '{description}'\n\nThis would normally trigger an LLM conversation with specialized coding context. In a full implementation, this would seamlessly switch to LLM mode with the enhanced prompt above."

    def get_help(self) -> str:
        """Get help text for the fix command."""
        return """Get coding assistance for development issues:
  /fix <description>       - Get help with a coding problem
  
Examples:
  /fix my python script has a syntax error
  /fix how do I implement authentication in Express.js
  /fix git merge conflict resolution
  /fix optimize this slow database query
  /fix unit test is failing with TypeError
  
This command provides specialized coding assistance with:
- Problem analysis and solutions
- Code examples and best practices  
- Command suggestions for fixes
- Step-by-step guidance"""

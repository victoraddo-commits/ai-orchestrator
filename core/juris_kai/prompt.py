"""Prompt engineering for Juris Kai Legal Assistant

This module builds legal-specific prompts, following the same secure structure 
as law_tutor's prompt building, with no operational code dependencies.
"""

def build_prompt(task_type, content):
    """Build appropriate legal prompts based on task type."""
    
    if task_type == "legal_teaching":
        return (
            f"Explain the legal concept of '{content}' in detail. "
            "Provide examples and relevant legal principles. "
            "Structure your response to aid understanding for a legal student."
        )
    elif task_type == "legal_case_analysis":
        return (
            f"Analyze the legal case '{content}'. "
            "Discuss the key legal principles involved, the court's reasoning, "
            "and the impact on legal doctrine. Provide a balanced summary."
        )
    elif task_type == "legal_research":
        return (
            f"Research the legal topic: '{content}'. "
            "Provide an overview of the current legal position, relevant cases, "
            "statutory provisions, and any ongoing debates in legal scholarship."
        )
    elif task_type == "legal_argument":
        return (
            f"Construct a legal argument for: '{content}'. "
            "Consider the relevant legal principles, precedents, and possible counterarguments. "
            "Structure your response as a persuasive legal argument."
        )
    elif task_type == "legal_flashcards":
        return (
            f"Generate flashcards for the legal topic: '{content}'. "
            "Each flashcard should contain a key legal principle or concept on the front, "
            "and a detailed explanation on the back. Format as bullet points for easy reading."
        )
    else:
        return f"Provide a legal explanation for: '{content}'"

# Same security pattern as law_tutor - no imports of operational modules
# Only pure text processing and prompt construction
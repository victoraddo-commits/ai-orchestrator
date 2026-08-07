"""Prompt engineering for Juris Kai Legal Assistant

This module builds legal-specific prompts, following the same secure structure 
as law_tutor's prompt building, with no operational code dependencies.
"""

def build_prompt(task_type, content):
    """Build appropriate legal prompts based on task type."""
    
    if task_type == "legal_teaching":
        return (
            f"Explain the legal concept of '{content}'. "
            "Provide key examples and relevant legal principles. "
            "Keep your response concise (300-500 words) and structured for a legal student."
        )
    elif task_type == "legal_case_analysis":
        return (
            f"Analyze the legal case '{content}'. "
            "Discuss the key legal principles, the court's reasoning, "
            "and the impact on legal doctrine. Keep your response concise (300-500 words)."
        )
    elif task_type == "legal_research":
        return (
            f"Research the legal topic: '{content}'. "
            "Provide an overview of the current legal position, key cases, "
            "and statutory provisions. Keep your response concise (300-500 words)."
        )
    elif task_type == "legal_argument":
        return (
            f"Construct a legal argument for: '{content}'. "
            "Consider relevant legal principles, precedents, and counterarguments. "
            "Keep your response concise (300-500 words)."
        )
    elif task_type == "legal_flashcards":
        return (
            f"Generate flashcards for the legal topic: '{content}'. "
            "Each flashcard should have a key legal concept and a brief explanation. "
            "Generate 5 flashcards. Format as bullet points."
        )
    else:
        return f"Provide a concise legal explanation for: '{content}'. Keep under 500 words."

# Same security pattern as law_tutor - no imports of operational modules
# Only pure text processing and prompt construction
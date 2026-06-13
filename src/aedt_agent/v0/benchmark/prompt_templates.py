from __future__ import annotations


def build_prompt(group: str, requirement: str, context: str) -> str:
    if group == "A":
        return f"Write PyAEDT code for this HFSS task:\n\n{requirement}"
    if group == "B":
        return f"Write PyAEDT code for this HFSS task.\nUse the API information below.\n\n{requirement}\n\n{context}"
    return (
        "Write PyAEDT code for this HFSS task.\n"
        "Stay inside the provided node and API constraints.\n\n"
        f"{requirement}\n\n{context}"
    )

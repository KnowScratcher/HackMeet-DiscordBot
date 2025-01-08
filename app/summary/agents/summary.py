# app/summary/agents/summary.py
"""
Summary agent.
"""
import os

from pydantic_ai import Agent

from app.summary.ai_select import get_model

prompt = """
You are a meeting summary expert. Please create a summary of the meeting from the provided transcript.

The summary should include:
People, items, and topics mentioned in the meeting.
Discussion topics and content, highlighting the main points.
Decisions or options discussed and their outcomes.

You do not need to list:
Action items from the meeting.
Participant lists or detailed meeting information.

Format example:
-----
Meeting Summary
Keywords:
Discussion Topics and Summary:
Discussion Outcomes:
-----
"""

async def generate_summary(transcript: str) -> str:
    """Generates a summary of the meeting from the transcript."""
    model = get_model()
    language = os.getenv("AI_OUTPUT_LANGUAGE", "en-US")
    agent_prompt = (
        transcript
        + prompt
        + f"\nPlease present the summary using the format "
        + language
        + " No additional commentary or text is needed."
    )
    agent = Agent(model)
    result = await agent.run(agent_prompt)
    return result.data

# app/summary/agents/todolist.py
"""
Todolist agent.
"""
import os

from pydantic_ai import Agent

from app.summary.ai_select import get_model

prompt = """
You are a task management expert. Please create a detailed To-Do List based on the provided meeting transcript.

The To-Do List should include:
Action items explicitly or implicitly mentioned during the meeting.
Responsible individuals or teams assigned to each task.
Deadlines or timelines if specified or inferable.
A brief description of the task to provide context.

Do not include:
Irrelevant or unrelated meeting details.
General discussions without specific action items.

Format example:
-----
Meeting To-Do List
1. Task Name: [Brief Description]
   - Responsible: [Individual/Team]
   - Deadline: [Specific Date/Timeline]

2. Task Name: [Brief Description]
   - Responsible: [Individual/Team]
   - Deadline: [Specific Date/Timeline]
-----
"""

async def generate_todolist(transcript: str) -> str:
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

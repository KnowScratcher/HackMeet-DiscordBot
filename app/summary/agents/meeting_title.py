# app/summary/agents/meeting_title.py
"""
Meeting title generation agent.
"""
import os
from datetime import datetime
from pydantic_ai import Agent
from app.summary.ai_select import get_model

prompt = """
You are a meeting title expert. Please create a concise and descriptive title for the meeting based on the provided transcript.

The title should:
1. Be brief but informative (maximum 50 characters)
2. Capture the main purpose or key topic of the meeting
3. Be easy to understand and search for later
4. Not include special characters that might cause file system issues

Do not include:
- Participant names
- Detailed descriptions
- Technical jargon unless necessary

Format:
[YYYYMMDD] Brief Title

Example:
[20240120] Product Roadmap Review
[20240121] UI Design Sprint Planning
"""

async def generate_meeting_title(transcript: str, meeting_date: datetime) -> str:
    """
    Generates a title for the meeting from the transcript.
    
    Args:
        transcript (str): Meeting transcript
        meeting_date (datetime): Meeting date
        
    Returns:
        str: Generated meeting title with date prefix
    """
    model = get_model()
    language = os.getenv("AI_OUTPUT_LANGUAGE", "en-US")
    date_str = meeting_date.strftime("[%Y%m%d]")
    
    agent_prompt = (
        transcript
        + prompt
        + f"\nPlease generate a title in {language}. "
        + f"Use this date format: {date_str}"
        + "\nThe title should be a single line with no additional text."
    )
    
    agent = Agent(model)
    result = await agent.run(agent_prompt)
    
    # Ensure the title starts with the correct date format
    title = result.data.strip()
    if not title.startswith(date_str):
        title = f"{date_str} {title}"
        
    # Remove any invalid characters for file systems
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        title = title.replace(char, '')
        
    return title 

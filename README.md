# 🚀 HackMeet-DiscordBot

![badge for project](https://wakapi.xiaobo.app/api/badge/%e5%b0%8f%e6%b3%a2/interval:any/project:HackMeet-DiscordBot)

### 🌐 Description
This is a Discord meeting recording bot that feels nothing short of magical! ✨

Imagine joining a voice channel on Discord and having a dedicated voice channel created just for you. This bot automatically joins the channel and records each participant's speech on separate audio tracks—every individual gets their own distinct recording! Once the meeting concludes, it seamlessly generates precise transcripts and comprehensive meeting summaries. The process is so seamless and sophisticated that it truly seems like magic!

But that's not all—the bot also supports a variety of cutting-edge STT (speech-to-text) and LLM (large language model) services, enhancing its capabilities and making it even more astonishing.

### Quick Preview
https://github.com/user-attachments/assets/934d18af-bde9-4d8d-a70d-c213cf91feff


### 🔥 Features
* **Automatic Voice Channel Creation:** The bot instantly sets up a private voice channel tailored for each user, streamlining the meeting setup process.
* **Forum Post Generation:** Automatically creates forum posts summarizing the meeting, making it easy to review discussions later.
* **Accurate Transcription:** Each participant’s speech is recorded on individual tracks and transcribed precisely.
* **Comprehensive Summaries:** The bot automatically compiles detailed meeting summaries, capturing key points and discussions.
* **TODO List Organization:** Automatically organizes action items into a clear and concise TODO list.
* **Multi-Bot Support:** Run several meetings at the same time without any hassle, as the bot supports multiple instances simultaneously.
* **Extensive STT and LLM Integration:** Integrates with a variety of speech-to-text and large language model services for enhanced functionality, with the flexibility to add more.
* **Multi-Language Support:** The bot can operate in multiple languages, making it accessible to a global audience.
* Experience the enchanting capabi

## 🚀 Deployment

You can deploy the app using Docker or just setup self.
### 🐳 Docker
You can just use docker run, but we recommend to use docker-compose.  
This is an example of docker-compose.yml:
```yaml
Not updated yet
```

### 🛠 Setup
#### 1️⃣ Install requirements
Recommend to use virtual environment for python 3.12.  
All requirements are listed in the `requirements.txt` file. To install them, using `pip install -r requirements.txt` should suffice.
#### 2️⃣ Configure environment variables
Edit `.env` file in the root directory and add the following environment variables:
```
# ---- Settings ----
SPEECH_LANGUAGE="en-US"
AI_OUTPUT_LANGUAGE="en-US"
DISCORD_MEETING_ROOM_NAME="MeetingRoom"
DISCORD_MEETING_NOTE_FORUM_NAME="MeetingNotes"
MAX_WAIT_SECONDS=86400
MEETING_CLOSE_DELAY=60

# ---- Translate ----
# When creating a forum post, the content template:
# {initiator} is the initiator of the meeting,
# {time} is the time of the meeting,
# {channel} is the channel of the meeting.
MEETING_FORUM_CONTENT="**Meeting Minutes**\n\nMeeting Initiator: {initiator}\nMeeting Start Time: {time}\nMeeting Channel: {channel}\n\nParticipant {initiator} joined the meeting"
# Member join/leave message template, {member} is the member who joined the meeting
MEETING_JOIN_MESSAGE="{member} joined the meeting"
MEETING_LEAVE_MESSAGE="{member} left the meeting"
# Meeting end message template
# {duration} is the duration of the meeting, {participants} is the list of participants
MEETING_ENDED_MESSAGE="**Meeting Ended**\nDuration: {duration}\nParticipants: {participants}\n"
# Generating summary message template (will be deleted after the summary is generated)
GENERATING_SUMMARY_MESSAGE="Generating meeting summary, please wait..."
# Transcript message template
TRANSCRIBING_MESSAGE="Here is the meeting transcript:"
# After generating summary message
SUMMARY_MESSAGE="Here is the meeting summary:"
# After generating to-do list message
TODOLIST_MESSAGE="Here is the to-do list:"
# No transcript message
NO_TRANSCRIPT_MESSAGE="No transcript for this meeting"

# ---- Keys ----
STT_SERVICE="google"
# | Azure, azure |
AZURE_SPEECH_KEY="ADD YOUR AZURE SPEECH KEY HERE"
AZURE_SPEECH_REGION="japanwest"
# | Google, google |
GOOGLE_APPLICATION_CREDENTIALS="ADD YOUR GOOGLE APPLICATION CREDENTIALS PATH HERE"
GCS_BUCKET_NAME="hackitbukket"
GCP_PROJECT_ID="arctic-sentry-445704-k5"
GCP_LOCATION="global"

AI_SERVICE="gemini"
MODEL_USE="gemini-2.0-flash-exp"
# | Azure OpenAI, azureopenai |
AZURE_OPENAI_ENDPOINT="https://hsh2024.openai.azure.com/"
AZURE_OPENAI_API_KEY="ADD YOUR AZURE OPENAI API KEY HERE"
AZURE_OPENAI_API_VERSION="2024-05-01-preview"
# | Gemini, gemini |
GEMINI_API_KEY="ADD YOUR GEMINI API KEY HERE"

BOT_TOKENS="ADD YOUR BOT TOKENS HERE"
```

#### 3️⃣ Run the app
Use `python run.py` to run the app in development.  

## 📜 License

This project is licensed under the Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International (CC BY-NC-SA 4.0) license. 

### 📌 Key Points:

1. **NonCommercial**: You may not use the material for commercial purposes.
2. **ShareAlike**: If you remix, transform, or build upon the material, you must distribute your contributions under the same license as the original.
3. **Attribution**: You must give appropriate credit, provide a link to the license, and indicate if changes were made. You may do so in any reasonable manner, but not in any way that suggests the licensor endorses you or your use.

### 📝 Additional Notes:

- **Technical Modifications**: You are allowed to make necessary technical modifications to the material.
- **No Additional Restrictions**: You may not apply legal terms or technological measures that legally restrict others from doing anything the license permits.
  
If you want to see the official description of the CC BY-NC-SA 4.0 license, you can visit https://creativecommons.org/licenses/by-nc-sa/4.0/legalcode.

### 🚨 Important:

If there is any conflict between the contents of this project and the CC BY-NC-SA 4.0 license, the terms of the CC BY-NC-SA 4.0 license shall prevail. Any interpretations that conflict with the CC BY-NC-SA 4.0 license are invalid unless formally agreed upon by the author in a signed contract.

import asyncio
import os
import logging
from datetime import datetime
from google import genai
from arch_ai.config import Config

logger = logging.getLogger("agent_terminal.subagents")


class SubAgentManager:
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.client = genai.Client(api_key=Config.GEMINI_API_KEY)
        self.reports_dir = os.path.join(Config.APP_DIR, "reports")
        os.makedirs(self.reports_dir, exist_ok=True)
        self.active_tasks = []

    async def _research_task(self, task_description: str, task_id: str):
        try:
            # Execute Gemini turn with search grounding
            response = await self.client.aio.models.generate_content(
                model=Config.MODEL_NAME,
                contents=f"You are an autonomous background researcher sub-agent. Perform a deep dive into the following task. Use your search grounding to fetch the latest information. Task: {task_description}",
                config=genai.types.GenerateContentConfig(tools=[{"google_search": {}}]),
            )

            report = response.text
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"research_report_{task_id}_{timestamp}.md"
            filepath = os.path.join(self.reports_dir, filename)

            with open(filepath, "w") as f:
                f.write(f"# Research Report: {task_description}\n\n{report}")

            from arch_ai.memory_manager import MemoryManager

            MemoryManager.get_instance().save_memory(
                f"Background Researcher finished report '{filename}' about: {task_description}"
            )

        except Exception as e:
            logger.error(f"Sub-agent task failed: {e}")

    def spawn_researcher(self, task_description: str) -> str:
        """Spawns a background asynchronous researcher agent."""
        task_id = str(len(self.active_tasks) + 1)
        # We must fire and forget using asyncio.create_task so it runs in the background
        task = asyncio.create_task(self._research_task(task_description, task_id))
        self.active_tasks.append(task)
        return f"Spawned Background Researcher Agent (Task ID: {task_id}). It will save its report to {self.reports_dir} and notify the memory system when done."

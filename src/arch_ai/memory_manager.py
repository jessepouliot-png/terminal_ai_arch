import sqlite3
import os
import logging
from typing import List

from arch_ai.config import Config

logger = logging.getLogger("agent_terminal.memory")

class MemoryManager:
    _instance = None
    
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.db_path = Config.DB_PATH
        self._init_db()
        
    def _init_db(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS long_term_memory (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        fact TEXT UNIQUE NOT NULL,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to initialize memory DB: {e}")

    def save_memory(self, fact: str) -> str:
        """Saves a fact or preference about the user or project into long-term memory."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("INSERT OR IGNORE INTO long_term_memory (fact) VALUES (?)", (fact.strip(),))
                conn.commit()
                if cursor.rowcount > 0:
                    return f"Successfully saved memory: '{fact}'"
                return f"Memory already exists: '{fact}'"
        except Exception as e:
            return f"Error saving memory: {e}"

    def get_all_memories(self) -> List[str]:
        """Retrieves all saved memories."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT fact FROM long_term_memory ORDER BY timestamp ASC")
                rows = cursor.fetchall()
                return [row[0] for row in rows]
        except Exception as e:
            logger.error(f"Error fetching memories: {e}")
            return []
            
    def clear_memory(self, fact: str) -> str:
        """Removes a specific fact from memory."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM long_term_memory WHERE fact = ?", (fact.strip(),))
                conn.commit()
                if cursor.rowcount > 0:
                    return f"Successfully removed memory: '{fact}'"
                return f"Memory not found: '{fact}'"
        except Exception as e:
            return f"Error removing memory: {e}"


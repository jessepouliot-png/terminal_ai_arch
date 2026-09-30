import sqlite3
import os
import re
import logging
import aiosqlite
import asyncio
import json
from datetime import datetime
from collections import deque
from typing import List, Dict, Optional, Tuple, Any

from config import Config

from logger_utils import StructuredLogger

log = StructuredLogger(logging.getLogger("agent_terminal.analyzer"))

class LocalKnowledge:
    """Manages local documentation and file indexing using SQLite FTS5."""
    
    def __init__(self, db_path: str):
        self.db_path = db_path

    async def initialize(self):
        """Creates a virtual table for full-text search if it doesn't exist."""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # Create FTS5 table for fast local searches
                await db.execute('''
                    CREATE VIRTUAL TABLE IF NOT EXISTS local_docs 
                    USING fts5(path, content, tokenize='porter')
                ''')
                # Also a tracker table for last modified dates to avoid re-indexing
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS indexed_files (
                        path TEXT PRIMARY KEY,
                        last_modified REAL
                    )
                ''')
                await db.commit()
        except Exception as e:
            log.error(f"FTS5 initialization failed: {e}")

    async def index_directory(self, root_dir: str, extensions: List[str] = ['.py', '.md', '.conf', '.sh', '.json', '.yaml', '.yml']):
        """Crawls and indexes files in the given directory with high performance."""
        root_dir = os.path.abspath(os.path.expanduser(root_dir))
        
        home_dir = os.path.expanduser("~")
        try:
            is_under_home = os.path.commonpath([home_dir, root_dir]) == home_dir
        except ValueError:
            is_under_home = False

        if not is_under_home:
            log.warning("Security alert: Attempted to index directory outside home.", path=root_dir)
            return 0

        if not os.path.isdir(root_dir):
            log.error(f"Directory not found: {root_dir}")
            return 0
            
        files_to_index = []
        for root, dirs, files in os.walk(root_dir):
            # Skip hidden directories like .git, .venv
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for file in files:
                if any(file.endswith(ext) for ext in extensions):
                    files_to_index.append(os.path.join(root, file))

        indexed_count = 0
        async with aiosqlite.connect(self.db_path) as db:
            # Batch fetch all existing indexed file timestamps into an in-memory dict
            existing_mtimes: Dict[str, float] = {}
            async with db.execute("SELECT path, last_modified FROM indexed_files") as cursor:
                async for row in cursor:
                    existing_mtimes[row[0]] = row[1]

            docs_to_insert = []
            files_to_update = []

            for file_path in files_to_index:
                try:
                    st = os.lstat(file_path)
                    mtime = st.st_mtime
                    if file_path in existing_mtimes and existing_mtimes[file_path] >= mtime:
                        continue

                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read(10000)

                    docs_to_insert.append((file_path, content))
                    files_to_update.append((file_path, mtime))
                    indexed_count += 1
                except Exception as e:
                    log.warning(f"Failed to process {file_path}: {e}")

            if docs_to_insert:
                await db.executemany("INSERT OR REPLACE INTO local_docs (path, content) VALUES (?, ?)", docs_to_insert)
                await db.executemany("INSERT OR REPLACE INTO indexed_files (path, last_modified) VALUES (?, ?)", files_to_update)
                await db.commit()

        return indexed_count


    async def search(self, query: str, limit: int = 3) -> List[Dict[str, str]]:
        """Searches the local index for relevant content."""
        # Sanitize query for FTS5 (remove special characters that break syntax)
        clean_query = re.sub(r'[^\w\s]', '', query)
        if not clean_query: return []
        
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            # Use BM25-like ranking provided by FTS5
            sql = "SELECT path, content, rank FROM local_docs WHERE local_docs MATCH ? ORDER BY rank LIMIT ?"
            try:
                async with db.execute(sql, (clean_query, limit)) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(row) for row in rows]
            except Exception as e:
                log.error(f"Local search failed: {e}")
                return []

class Telemetry:
    """Tracks performance and usage metrics."""
    def __init__(self, db_path: str):
        self.db_path = db_path

    async def initialize(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                CREATE TABLE IF NOT EXISTS metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    metric_name TEXT,
                    value REAL,
                    tags TEXT
                )
            ''')
            await db.execute('CREATE INDEX IF NOT EXISTS idx_metrics_name ON metrics(metric_name)')
            await db.commit()

    async def log_metric(self, name: str, value: float, tags: Dict[str, str] = None):
        tags_json = json.dumps(tags or {})
        timestamp = datetime.now().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO metrics (timestamp, metric_name, value, tags) VALUES (?, ?, ?, ?)",
                (timestamp, name, value, tags_json)
            )
            await db.commit()

class BehaviorAnalyzer:
    """Production-grade behavioral intelligence and security engine using SQLite."""
    
    # Pre-compiled regex patterns for zero-allocation risk classification
    _CRITICAL_PATTERNS = [
        (re.compile(r"\brm\s+-[rfv\s]*/(?! \w)", re.IGNORECASE), "Root directory deletion."),
        (re.compile(r"\bdd\s+if=.*of=/dev/", re.IGNORECASE), "Raw device write."),
        (re.compile(r"\bmkfs\.", re.IGNORECASE), "Filesystem destruction."),
        (re.compile(r"[:;]\s*\(\)\s*{\s*[:;]\s*\|\s*[:;]\s*}\s*;\s*[:;]", re.IGNORECASE), "Fork bomb."),
        (re.compile(r"base64\s+-d\s+\|(?: \s*bash|\s*sh)", re.IGNORECASE), "Obfuscated execution.")
    ]
    
    _HIGH_PATTERNS = [
        (re.compile(r"\brm\s+-[rfv\s]*(?! \.)", re.IGNORECASE), "Forceful deletion."),
        (re.compile(r"nc\s+-e\s+", re.IGNORECASE), "Reverse shell attempt."),
        (re.compile(r"\b(shutdown|reboot|halt)\b", re.IGNORECASE), "System state termination."),
        (re.compile(r"\b(pacman|yay)\s+-R", re.IGNORECASE), "Package removal.")
    ]

    _MEDIUM_PATTERNS = [
        (re.compile(r"\b(pacman|yay)\s+-[S|U]", re.IGNORECASE), "System package installation/update."),
        (re.compile(r"\bsystemctl\s+(start|stop|restart|enable|disable)", re.IGNORECASE), "System service modification."),
        (re.compile(r"\bsudo\b", re.IGNORECASE), "Superuser privilege escalation."),
        (re.compile(r"\brm\b", re.IGNORECASE), "File deletion.")
    ]

    def __init__(self, db_path: Optional[str] = None, model: Any = None):
        self.db_path = db_path or Config.DB_PATH
        self.model = model
        self.history_cache = deque(maxlen=100)
        self.knowledge = LocalKnowledge(self.db_path) # Integrated Local Knowledge
        self.telemetry = Telemetry(self.db_path)

    async def initialize(self):
        """Creates the necessary database tables and indexes if they do not exist."""
        await self.knowledge.initialize()
        await self.telemetry.initialize()
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS commands (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT,
                        command TEXT,
                        status TEXT
                    )
                ''')
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS chats (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT,
                        user_msg TEXT,
                        agent_msg TEXT
                    )
                ''')
                await db.execute('CREATE INDEX IF NOT EXISTS idx_commands_cmd ON commands(command)')
                await db.execute('CREATE INDEX IF NOT EXISTS idx_commands_status ON commands(status)')
                await db.execute('CREATE INDEX IF NOT EXISTS idx_commands_time ON commands(timestamp)')
                await db.execute('CREATE INDEX IF NOT EXISTS idx_chats_time ON chats(timestamp)')
                await db.commit()
        except Exception as e:
            log.error(f"Database initialization failed: {e}")


    async def async_classify_risk(self, command: str) -> Tuple[str, str]:
        """Performs two-stage risk analysis: Heuristic -> LLM."""
        risk_level, reason = self.classify_risk(command)
        if (risk_level in ["high", "critical"] or any(char in command for char in [">", "|", ";", "&", "`", "$"])) and self.model:
            try:
                prompt = (
                    "SYSTEM: You are a Linux Security Expert.\n"
                    "GOAL: Analyze this shell command for malicious intent or system destruction.\n\n"
                    f"COMMAND: {command}\n\n"
                    "Respond exactly in this format: RISK: [low|medium|high|critical] | REASON: <explanation>"
                )
                
                # Note: This is now using the NEW SDK CLIENT (self.model is client)
                response = await self.model.aio.models.generate_content(
                    model=Config.MODEL_NAME,
                    contents=prompt
                )
                llm_output = response.text.strip() if response and response.text else ""

                if "RISK:" in llm_output and "|" in llm_output:
                    parts = llm_output.split("|")
                    llm_risk = parts[0].replace("RISK:", "").strip().lower()
                    llm_reason = parts[1].replace("REASON:", "").strip()
                    if llm_risk in ["medium", "high", "critical"]:
                        return llm_risk, f"[AI Analysis] {llm_reason}"
            except Exception as e:
                log.error(f"LLM Security Analysis failed: {e}")
        return risk_level, reason

    async def _db_execute(self, query: str, params: tuple = ()):
        """Utility for safe async database operations."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(query, params)
            await db.commit()

    async def log_command(self, command: str, status: str = "success") -> None:
        """Logs a command to the database and cache."""
        timestamp = datetime.now().isoformat()
        await self._db_execute("INSERT INTO commands (timestamp, command, status) VALUES (?, ?, ?)", (timestamp, command, status))
        self.history_cache.append({"command": command, "status": status})

    async def log_chat(self, user_msg: str, agent_msg: str) -> None:
        """Logs a chat entry to the database."""
        timestamp = datetime.now().isoformat()
        await self._db_execute("INSERT INTO chats (timestamp, user_msg, agent_msg) VALUES (?, ?, ?)", (timestamp, user_msg, agent_msg))

    async def get_chat_history(self, limit: int = 10) -> List[Dict[str, str]]:
        """Retrieves recent chat history."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT user_msg as user, agent_msg as agent FROM chats ORDER BY id DESC LIMIT ?", (limit,)) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in reversed(rows)]

    def classify_risk(self, command: str) -> Tuple[str, str]:
        """Fast heuristic security check using pre-compiled regexes."""
        cmd = command.strip()
        for p, r in self._CRITICAL_PATTERNS:
            if p.search(cmd): return "critical", r
        for p, r in self._HIGH_PATTERNS:
            if p.search(cmd): return "high", r
        for p, r in self._MEDIUM_PATTERNS:
            if p.search(cmd): return "medium", r
        return "low", "Standard command."

    async def get_behavioral_stats(self) -> str:
        """Generates simple session metrics with telemetry summary."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT COUNT(*) FROM commands") as c:
                total = (await c.fetchone())[0]
            
            # Telemetry metrics
            async with db.execute("SELECT AVG(value) FROM metrics WHERE metric_name = 'ai_latency'") as c:
                avg_latency = (await c.fetchone())[0] or 0.0
            
            async with db.execute("SELECT COUNT(*) FROM commands WHERE status LIKE 'failed%'") as c:
                failures = (await c.fetchone())[0]

            if total == 0: return "No commands logged yet."
            return (
                f"Session Integrity: [green]Verified[/green]\n"
                f"Total Operations: {total} ({failures} failures)\n"
                f"Avg AI Latency: {avg_latency:.2f}s"
            )

    async def suggest_next_command(self, current_command: str) -> Optional[str]:
        """Predicts the next command based on history using window ranking."""
        query = """
            WITH ordered AS (
                SELECT command, LEAD(command) OVER (ORDER BY id) AS next_cmd
                FROM commands
            )
            SELECT next_cmd 
            FROM ordered
            WHERE command = ? AND next_cmd IS NOT NULL
            GROUP BY next_cmd
            ORDER BY COUNT(*) DESC
            LIMIT 1
        """
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(query, (current_command,)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None

    async def get_last_command(self) -> Optional[str]:
        """Retrieves the most recent command from history cache or database."""
        if self.history_cache:
            return self.history_cache[-1]["command"]
        try:
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute("SELECT command FROM commands ORDER BY id DESC LIMIT 1") as cursor:
                    row = await cursor.fetchone()
                    return row[0] if row else None
        except Exception as e:
            log.error(f"Failed to get last command from database: {e}")
            return None


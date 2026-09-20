"""Lightweight Git and temporal evidence extractor for PatchForge Repository Intelligence."""
import logging
import os
import subprocess
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class GitEvidenceExtractor:
    """Extracts git commit history, blame, and line-level historical diffs for target symbols."""

    def __init__(self, repo_dir: str):
        self.repo_dir = repo_dir

    def _run_git(self, args: List[str]) -> str:
        try:
            res = subprocess.run(
                ["git"] + args,
                cwd=self.repo_dir,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            return res.stdout.strip()
        except Exception as e:
            logger.debug(f"Git command failed: git {' '.join(args)}: {e}")
            return ""

    def commits_for_file(self, file_path: str, max_count: int = 5) -> List[Dict[str, Any]]:
        """Returns recent commit log for the given file."""
        rel_path = os.path.relpath(file_path, self.repo_dir).replace("\\", "/")
        out = self._run_git(["log", f"-n{max_count}", "--pretty=format:%h|%an|%ad|%s", "--date=short", "--", rel_path])
        if not out:
            return []
            
        commits = []
        for line in out.splitlines():
            parts = line.split("|", 3)
            if len(parts) == 4:
                commits.append({
                    "hash": parts[0],
                    "author": parts[1],
                    "date": parts[2],
                    "message": parts[3],
                })
        return commits

    def blame_for_lines(self, file_path: str, start_line: int, end_line: int) -> List[Dict[str, Any]]:
        """Returns blame for specific lines of a file."""
        rel_path = os.path.relpath(file_path, self.repo_dir).replace("\\", "/")
        out = self._run_git(["blame", f"-L{start_line},{end_line}", "--porcelain", "--", rel_path])
        if not out:
            return []
            
        blame_entries = []
        curr_hash = ""
        curr_author = ""
        curr_summary = ""
        
        for line in out.splitlines():
            if line.startswith("author "):
                curr_author = line[7:]
            elif line.startswith("summary "):
                curr_summary = line[8:]
            elif line.startswith("\t"):
                content = line[1:]
                blame_entries.append({
                    "hash": curr_hash,
                    "author": curr_author,
                    "summary": curr_summary,
                    "line_content": content,
                })
            else:
                parts = line.split()
                if len(parts) >= 2 and len(parts[0]) >= 8:
                    curr_hash = parts[0]
                    
        return blame_entries

    def symbol_history_summary(self, file_path: str, symbol_name: str, start_line: int, end_line: int) -> str:
        """Formats a concise historical context summary for a symbol."""
        commits = self.commits_for_file(file_path, max_count=3)
        if not commits:
            return "No recent git commit history available."
            
        lines = [f"Recent Git History for '{symbol_name}' in {os.path.basename(file_path)}:"]
        for c in commits:
            lines.append(f"  * {c['hash']} ({c['date']}, {c['author']}): {c['message']}")
            
        return "\n".join(lines)

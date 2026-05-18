"""
File State Management System

Tracks the source and modification history of every file in the CVE reproduction process.
This enables precise feedback routing when agents encounter issues with specific files.
"""

import json
import os
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from .tool_controller import AgentType


@dataclass
class FileModification:
    """Represents a single file modification event"""
    agent: str
    time: str
    action: str  # 'created', 'modified', 'deleted'


@dataclass
class FileState:
    """Represents the complete state of a file"""
    source_agent: str
    last_edit_agent: str
    created_time: str
    last_edit_time: str
    modifications: List[FileModification]


class FileStateManager:
    """
    Manages file state tracking throughout the CVE reproduction pipeline.
    """

    def __init__(self, working_dir: Path):
        """
        Initialize file state manager.
        
        Args:
            working_dir: CVE working directory
        """
        self.working_dir = Path(working_dir)
        self.state_dir = self.working_dir / ".agent_state"
        self.state_file = self.state_dir / "file_states.json"
        
        # Create state directory if it doesn't exist
        self.state_dir.mkdir(parents=True, exist_ok=True)
        
        # Load existing states
        self.file_states: Dict[str, FileState] = self._load_file_states()
    
    def _load_file_states(self) -> Dict[str, FileState]:
        """Load file states from JSON file"""
        if not self.state_file.exists():
            return {}
        
        try:
            with open(self.state_file, 'r') as f:
                data = json.load(f)
            
            states = {}
            for path, state_data in data.items():
                modifications = [
                    FileModification(**mod) for mod in state_data.get('modifications', [])
                ]
                
                states[path] = FileState(
                    source_agent=state_data['source_agent'],
                    last_edit_agent=state_data['last_edit_agent'],
                    created_time=state_data['created_time'],
                    last_edit_time=state_data['last_edit_time'],
                    modifications=modifications
                )
            
            return states
        except (json.JSONDecodeError, KeyError) as e:
            print(f"Warning: Failed to load file states: {e}")
            return {}
    
    def _save_file_states(self) -> None:
        """Save file states to JSON file"""
        data = {}
        for path, state in self.file_states.items():
            data[path] = {
                'source_agent': state.source_agent,
                'last_edit_agent': state.last_edit_agent,
                'created_time': state.created_time,
                'last_edit_time': state.last_edit_time,
                'modifications': [
                    {
                        'agent': mod.agent,
                        'time': mod.time,
                        'action': mod.action
                    }
                    for mod in state.modifications
                ]
            }
        
        with open(self.state_file, 'w') as f:
            json.dump(data, f, indent=2)
    
    # Only track these key files (no full directory walk)
    TRACKED_FILES = ['docker-compose.yaml', 'Dockerfile', 'solution.sh', 'task.yaml']
    TRACKED_DIRS = ['tests', 'task-deps']

    def _get_tracked_files(self) -> List[Path]:
        """Get list of tracked file paths that exist."""
        files = []
        for f in self.TRACKED_FILES:
            p = self.working_dir / f
            if p.exists():
                files.append(p)
        for d in self.TRACKED_DIRS:
            dir_path = self.working_dir / d
            if dir_path.is_dir():
                for item in dir_path.rglob('*'):
                    if item.is_file():
                        files.append(item)
        return files

    def update_file_states(self, agent_type: AgentType) -> List[str]:
        """
        Update file states after agent execution.
        Only tracks key files: docker-compose.yaml, Dockerfile, tests/*

        Args:
            agent_type: Agent that just executed

        Returns:
            List of modified file paths
        """
        agent_name = agent_type.value
        modified_files = []

        for file_path_abs in self._get_tracked_files():
            file_path_rel = file_path_abs.relative_to(self.working_dir)
            file_path_str = str(file_path_rel)

            # Get file modification time
            try:
                mtime = file_path_abs.stat().st_mtime
                mtime_str = datetime.fromtimestamp(mtime).isoformat()
            except OSError:
                continue

            if file_path_str not in self.file_states:
                # New file created by this agent
                modification = FileModification(
                    agent=agent_name,
                    time=mtime_str,
                    action="created"
                )

                self.file_states[file_path_str] = FileState(
                    source_agent=agent_name,
                    last_edit_agent=agent_name,
                    created_time=mtime_str,
                    last_edit_time=mtime_str,
                    modifications=[modification]
                )
                modified_files.append(file_path_str)

            else:
                # Check if file was modified
                existing_state = self.file_states[file_path_str]

                if mtime_str > existing_state.last_edit_time:
                    modification = FileModification(
                        agent=agent_name,
                        time=mtime_str,
                        action="modified"
                    )

                    existing_state.last_edit_agent = agent_name
                    existing_state.last_edit_time = mtime_str
                    existing_state.modifications.append(modification)
                    modified_files.append(file_path_str)

        # Save updated states
        self._save_file_states()
        return modified_files
    
    def find_responsible_agent(self, file_name: str) -> Optional[AgentType]:
        """
        Find the agent responsible for a specific file.
        
        Args:
            file_name: Name or path of the file
            
        Returns:
            AgentType of responsible agent, or None if not found
        """
        # Try exact match first
        if file_name in self.file_states:
            agent_name = self.file_states[file_name].source_agent
            try:
                return AgentType(agent_name)
            except ValueError:
                return None
        
        # Try pattern matching
        for path, state in self.file_states.items():
            if (file_name in path or 
                path.endswith(file_name) or 
                Path(path).name == file_name):
                try:
                    return AgentType(state.source_agent)
                except ValueError:
                    continue
        
        return None
    
    def get_file_history(self, file_name: str) -> Optional[FileState]:
        """
        Get complete modification history for a file.
        
        Args:
            file_name: Name or path of the file
            
        Returns:
            FileState object or None if file not found
        """
        # Try exact match first
        if file_name in self.file_states:
            return self.file_states[file_name]
        
        # Try pattern matching
        for path, state in self.file_states.items():
            if (file_name in path or 
                path.endswith(file_name) or 
                Path(path).name == file_name):
                return state
        
        return None
    
    def get_agent_files(self, agent_type: AgentType) -> List[str]:
        """
        Get all files created by a specific agent.
        
        Args:
            agent_type: Agent type
            
        Returns:
            List of file paths created by the agent
        """
        agent_name = agent_type.value
        return [
            path for path, state in self.file_states.items()
            if state.source_agent == agent_name
        ]
    
    def validate_file_syntax(self, file_path: str) -> tuple[bool, Optional[str]]:
        """
        Validate file syntax based on extension.
        
        Args:
            file_path: Path to the file
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        full_path = self.working_dir / file_path
        
        if not full_path.exists():
            return False, f"File does not exist: {file_path}"
        
        ext = full_path.suffix.lower()
        
        try:
            if ext == '.py':
                return self._validate_python(full_path)
            elif ext in ['.yaml', '.yml']:
                return self._validate_yaml(full_path)
            elif ext == '.json':
                return self._validate_json(full_path)
            elif ext == '.xml':
                return self._validate_xml(full_path)
            elif ext == '.sh':
                return self._validate_bash(full_path)
            else:
                return True, None  # No validator, assume valid
                
        except Exception as e:
            return False, str(e)
    
    def _validate_python(self, file_path: Path) -> tuple[bool, Optional[str]]:
        """Validate Python syntax"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                source = f.read()
            compile(source, str(file_path), 'exec')
            return True, None
        except SyntaxError as e:
            return False, f"Python syntax error: {e}"
        except Exception as e:
            return False, f"Error reading file: {e}"
    
    def _validate_yaml(self, file_path: Path) -> tuple[bool, Optional[str]]:
        """Validate YAML syntax"""
        try:
            import yaml
            with open(file_path, 'r', encoding='utf-8') as f:
                yaml.safe_load(f)
            return True, None
        except yaml.YAMLError as e:
            return False, f"YAML syntax error: {e}"
        except Exception as e:
            return False, f"Error reading file: {e}"
    
    def _validate_json(self, file_path: Path) -> tuple[bool, Optional[str]]:
        """Validate JSON syntax"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                json.load(f)
            return True, None
        except json.JSONDecodeError as e:
            return False, f"JSON syntax error: {e}"
        except Exception as e:
            return False, f"Error reading file: {e}"
    
    def _validate_xml(self, file_path: Path) -> tuple[bool, Optional[str]]:
        """Validate XML syntax"""
        try:
            import xml.etree.ElementTree as ET
            ET.parse(str(file_path))
            return True, None
        except ET.ParseError as e:
            return False, f"XML syntax error: {e}"
        except Exception as e:
            return False, f"Error reading file: {e}"
    
    def _validate_bash(self, file_path: Path) -> tuple[bool, Optional[str]]:
        """Validate Bash script syntax"""
        try:
            # Basic bash syntax check using bash -n
            import subprocess
            result = subprocess.run(
                ['bash', '-n', str(file_path)],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                return True, None
            else:
                return False, f"Bash syntax error: {result.stderr}"
        except Exception as e:
            return False, f"Error validating bash script: {e}"
    
    def save_to_logs(self) -> Path:
        """
        Save a copy of file states to .logs/ directory for persistence across phases.

        Returns:
            Path to the saved file
        """
        logs_dir = self.working_dir / ".logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

        backup_file = logs_dir / "file_states.json"

        # Copy current state to logs
        data = {}
        for path, state in self.file_states.items():
            data[path] = {
                'source_agent': state.source_agent,
                'last_edit_agent': state.last_edit_agent,
                'created_time': state.created_time,
                'last_edit_time': state.last_edit_time,
                'modifications': [
                    {
                        'agent': mod.agent,
                        'time': mod.time,
                        'action': mod.action
                    }
                    for mod in state.modifications
                ]
            }

        with open(backup_file, 'w') as f:
            json.dump(data, f, indent=2)

        return backup_file

    @classmethod
    def load_from_logs(cls, working_dir: Path) -> 'FileStateManager':
        """
        Load FileStateManager from .logs/ backup if available.

        This enables resuming from a previous phase.

        Args:
            working_dir: CVE working directory

        Returns:
            FileStateManager instance with restored state
        """
        instance = cls(working_dir)

        logs_backup = working_dir / ".logs" / "file_states.json"
        if logs_backup.exists():
            try:
                with open(logs_backup, 'r') as f:
                    data = json.load(f)

                for path, state_data in data.items():
                    modifications = [
                        FileModification(**mod) for mod in state_data.get('modifications', [])
                    ]

                    instance.file_states[path] = FileState(
                        source_agent=state_data['source_agent'],
                        last_edit_agent=state_data['last_edit_agent'],
                        created_time=state_data['created_time'],
                        last_edit_time=state_data['last_edit_time'],
                        modifications=modifications
                    )

                # Save to .agent_state as well
                instance._save_file_states()

            except (json.JSONDecodeError, KeyError) as e:
                print(f"Warning: Failed to load file states from logs: {e}")

        return instance

    def export_summary(self) -> Dict[str, Any]:
        """
        Export a summary of all file states.

        Returns:
            Dictionary with file state summary
        """
        summary = {
            'total_files': len(self.file_states),
            'agents': {},
            'files': {}
        }
        
        # Count files per agent
        for path, state in self.file_states.items():
            agent = state.source_agent
            if agent not in summary['agents']:
                summary['agents'][agent] = {
                    'created_files': 0,
                    'modified_files': 0
                }
            summary['agents'][agent]['created_files'] += 1
            
            # Count modifications by other agents
            for mod in state.modifications:
                if mod.action == 'modified' and mod.agent != agent:
                    if mod.agent not in summary['agents']:
                        summary['agents'][mod.agent] = {
                            'created_files': 0,
                            'modified_files': 0
                        }
                    summary['agents'][mod.agent]['modified_files'] += 1
        
        # File details
        for path, state in self.file_states.items():
            summary['files'][path] = {
                'source_agent': state.source_agent,
                'last_edit_agent': state.last_edit_agent,
                'modifications_count': len(state.modifications)
            }
        
        return summary
"""
Agent Runner - Claude Agent SDK Integration

This module handles running individual agents using Claude Agent SDK (Official),
managing sessions, and enforcing access controls.
"""

import asyncio
import json
import logging
import traceback
from pathlib import Path
from typing import Dict, Optional, Any, List
from dataclasses import dataclass
from datetime import datetime

from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
from claude_agent_sdk.types import (
    AssistantMessage, UserMessage, SystemMessage, ResultMessage, StreamEvent,
    TextBlock, ThinkingBlock, ToolUseBlock, ToolResultBlock
)

from .tool_controller import AgentType, ToolController
from .file_access_controller import FileAccessController, create_file_access_hooks


@dataclass
class AgentSession:
    """Represents an active agent session"""
    agent_type: AgentType
    working_dir: Path
    session_id: str
    created_at: datetime
    last_active: datetime
    metadata: Dict[str, Any]
    model: str  # Model used for this session
    sdk_client: Optional[ClaudeSDKClient] = None  # Real Claude Agent SDK client


class AgentRunner:
    """
    Manages Claude Code agent sessions with access control.

    This class:
    1. Creates Claude Code sessions for each agent
    2. Enforces tool permissions
    3. Enforces file access restrictions
    4. Manages session lifecycle
    5. Handles agent communication
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize Agent Runner.

        Args:
            config: Configuration dictionary from config.yaml
        """
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.sessions: Dict[str, AgentSession] = {}
        self.agent_prompts_dir = Path(__file__).parent.parent / "agents"
        
        # Verbose logging settings
        self.verbose_claude_code = config.get('logging', {}).get('verbose_claude_code', False)
        self.save_conversations = config.get('logging', {}).get('save_conversations', False)

        self.logger.info("AgentRunner initialized with Claude Agent SDK")
        if self.verbose_claude_code:
            self.logger.info("Verbose Claude Code logging enabled")

    def _get_model_for_agent(self, agent_type: AgentType) -> str:
        """
        Get the appropriate model for a specific agent type.
        
        Args:
            agent_type: Type of agent
            
        Returns:
            Model name to use for this agent
        """
        # Get model configuration from config
        models_config = self.config.get('models', {})
        agent_models = models_config.get('agent_models', {})
        default_model = models_config.get('default', 'claude-sonnet-4-5-20250929')
        
        # Get agent-specific model or use default
        model = agent_models.get(agent_type.value, default_model)
        
        self.logger.debug(f"Selected model for {agent_type.value}: {model}")
        return model

    def _get_session_logs_dir(self, session_id: str) -> Optional[Path]:
        """
        Get the .logs directory for a session's working directory.

        Args:
            session_id: Session ID

        Returns:
            Path to .logs directory, or None if session not found
        """
        session = self.sessions.get(session_id)
        if not session:
            return None

        logs_dir = session.working_dir / ".logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        return logs_dir

    def _format_message(self, message: Any) -> str:
        """
        Format SDK message in Markdown format.

        Parses AssistantMessage, SystemMessage, ResultMessage etc. and formats
        their content blocks nicely.

        Args:
            message: SDK message object

        Returns:
            Formatted markdown string
        """
        lines = []

        if isinstance(message, AssistantMessage):
            lines.append(f"### 🤖 Assistant `{message.model}`")
            if message.error:
                lines.append(f"\n> ⚠️ **ERROR**: {message.error}\n")
            for block in message.content:
                lines.append(self._format_content_block(block))

        elif isinstance(message, UserMessage):
            lines.append("### 👤 User")
            if isinstance(message.content, str):
                lines.append(f"\n{message.content}\n")
            else:
                for block in message.content:
                    lines.append(self._format_content_block(block))

        elif isinstance(message, SystemMessage):
            lines.append(f"### ⚙️ System: `{message.subtype}`")
            # Format important fields as table
            important_keys = ['session_id', 'model', 'cwd', 'permissionMode']
            table_lines = ["| Key | Value |", "|-----|-------|"]
            for key in important_keys:
                if key in message.data:
                    value = message.data[key]
                    table_lines.append(f"| {key} | `{value}` |")
            if 'tools' in message.data:
                tools = message.data['tools']
                table_lines.append(f"| tools | {len(tools)} available |")
            lines.append('\n' + '\n'.join(table_lines) + '\n')

        elif isinstance(message, ResultMessage):
            lines.append(f"### 📊 Result: `{message.subtype}`")
            lines.append(f"\n| Metric | Value |")
            lines.append("|--------|-------|")
            lines.append(f"| Duration | {message.duration_ms}ms (API: {message.duration_api_ms}ms) |")
            lines.append(f"| Turns | {message.num_turns} |")
            if message.total_cost_usd is not None:
                lines.append(f"| Cost | ${message.total_cost_usd:.4f} |")
            if message.usage:
                input_tokens = message.usage.get('input_tokens', 0)
                output_tokens = message.usage.get('output_tokens', 0)
                lines.append(f"| Tokens | {input_tokens} in / {output_tokens} out |")
            if message.is_error:
                lines.append(f"\n> ❌ **ERROR**: {message.result}\n")
            lines.append("")

        elif isinstance(message, StreamEvent):
            event_type = message.event.get('type', 'unknown')
            lines.append(f"### 📡 Stream Event: `{event_type}`")

        else:
            lines.append(f"### ❓ Unknown: `{type(message).__name__}`")
            lines.append(f"```\n{str(message)}\n```")

        return "\n".join(lines)

    def _format_tool_use(self, name: str, tool_id: str, input_data: Dict[str, Any]) -> str:
        """
        Format tool use block based on tool type.

        Args:
            name: Tool name
            tool_id: Tool use ID
            input_data: Tool input parameters

        Returns:
            Formatted markdown string
        """
        if name == 'Read':
            file_path = input_data.get('file_path', '')
            return f"**🔧 Tool: `Read`** `file_path: {file_path}` `id: {tool_id}`\n"

        elif name == 'Write':
            file_path = input_data.get('file_path', '')
            content = input_data.get('content', '')
            return f"**🔧 Tool: `Write`** `file_path: {file_path}` `id: {tool_id}`\n```\n{content}\n```\n"

        elif name == 'Edit':
            file_path = input_data.get('file_path', '')
            old_string = input_data.get('old_string', '')
            new_string = input_data.get('new_string', '')
            return f"**🔧 Tool: `Edit`** `file_path: {file_path}` `id: {tool_id}`\n**old_string:**\n```\n{old_string}\n```\n**new_string:**\n```\n{new_string}\n```\n"

        elif name == 'Bash':
            command = input_data.get('command', '')
            description = input_data.get('description', '')
            desc_part = f" `{description}`" if description else ""
            return f"**🔧 Tool: `Bash`**{desc_part} `id: {tool_id}`\n```bash\n{command}\n```\n"

        elif name == 'Glob':
            pattern = input_data.get('pattern', '')
            path = input_data.get('path', '')
            path_part = f" `path: {path}`" if path else ""
            return f"**🔧 Tool: `Glob`** `pattern: {pattern}`{path_part} `id: {tool_id}`\n"

        elif name == 'Grep':
            pattern = input_data.get('pattern', '')
            path = input_data.get('path', '')
            path_part = f" `path: {path}`" if path else ""
            return f"**🔧 Tool: `Grep`** `pattern: {pattern}`{path_part} `id: {tool_id}`\n"

        elif name == 'WebFetch':
            url = input_data.get('url', '')
            prompt = input_data.get('prompt', '')
            return f"**🔧 Tool: `WebFetch`** `url: {url}` `id: {tool_id}`\n> {prompt}\n"

        elif name == 'WebSearch':
            query = input_data.get('query', '')
            return f"**🔧 Tool: `WebSearch`** `query: {query}` `id: {tool_id}`\n"

        elif name == 'Task':
            description = input_data.get('description', '')
            prompt = input_data.get('prompt', '')
            return f"**🔧 Tool: `Task`** `{description}` `id: {tool_id}`\n```\n{prompt}\n```\n"

        else:
            # Default: show as JSON for unknown tools
            input_str = json.dumps(input_data, indent=2, ensure_ascii=False)
            return f"**🔧 Tool: `{name}`** `id: {tool_id}`\n```json\n{input_str}\n```\n"

    def _format_content_block(self, block: Any) -> str:
        """
        Format a single content block in Markdown format.

        Args:
            block: Content block (TextBlock, ToolUseBlock, etc.)

        Returns:
            Formatted markdown string
        """
        if isinstance(block, TextBlock):
            # Text content rendered as-is (already markdown from Claude)
            return f"\n{block.text}\n"

        elif isinstance(block, ThinkingBlock):
            # Thinking in a details block
            return f"\n<details>\n<summary>💭 Thinking</summary>\n\n{block.thinking}\n\n</details>\n"

        elif isinstance(block, ToolUseBlock):
            # Format based on tool type
            return "\n" + self._format_tool_use(block.name, block.id, block.input)

        elif isinstance(block, ToolResultBlock):
            content = block.content
            if isinstance(content, list):
                content = json.dumps(content, indent=2, ensure_ascii=False)
            error_icon = "❌" if block.is_error else "✅"
            return f"\n**{error_icon} ToolResult** `{block.tool_use_id}`\n```\n{content}\n```\n"

        else:
            return f"\n**Unknown Block: {type(block).__name__}**\n```\n{str(block)}\n```\n"

    def _serialize_message(self, content: Any) -> Any:
        """
        Serialize SDK message object to JSON-compatible dict.
        Preserves raw SDK data as much as possible.

        Args:
            content: Message content (str or SDK message object)

        Returns:
            JSON-serializable representation of the message
        """
        if isinstance(content, str):
            return content

        # Try SDK's native serialization methods first (Pydantic models)
        if hasattr(content, 'model_dump'):
            try:
                return content.model_dump()
            except Exception as e:
                self.logger.warning(f"model_dump() failed for {type(content).__name__}: {e}, content: {repr(content)}")

        if hasattr(content, 'to_dict'):
            try:
                return content.to_dict()
            except Exception as e:
                self.logger.warning(f"to_dict() failed for {type(content).__name__}: {e}, content: {repr(content)}")

        # Fallback: convert using __dict__ but keep ALL fields
        if hasattr(content, '__dict__'):
            result = {'_type': type(content).__name__}
            for key, value in vars(content).items():
                # Recursively serialize nested objects
                if isinstance(value, list):
                    result[key] = [self._serialize_message(item) for item in value]
                elif isinstance(value, dict):
                    result[key] = {k: self._serialize_message(v) for k, v in value.items()}
                elif hasattr(value, '__dict__') or hasattr(value, 'model_dump'):
                    result[key] = self._serialize_message(value)
                else:
                    try:
                        json.dumps(value)  # Test if serializable
                        result[key] = value
                    except (TypeError, ValueError):
                        result[key] = repr(value)  # Use repr instead of str for more detail
            return result

        # Fallback: try direct JSON serialization
        try:
            json.dumps(content)
            return content
        except (TypeError, ValueError):
            return repr(content)

    def _log_conversation(
        self,
        session_id: str,
        message_type: str,
        content: Any,  # Can be str or SDK message object
        metadata: Optional[Dict] = None
    ) -> None:
        """
        Log conversation content for verbose mode and/or save to file.

        Conversations are saved to the per-CVE .logs/ directory as:
        - Markdown files (.md) for human-readable format
        - JSON files (.json) for raw data preservation

        Args:
            session_id: Session ID
            message_type: Type of message ('user_message', 'agent_response', 'system_info')
            content: Message content (str or SDK message object)
            metadata: Optional metadata about the message
        """
        if not self.verbose_claude_code and not self.save_conversations:
            return

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        session = self.sessions.get(session_id)
        agent_type = session.agent_type.value if session else "unknown"

        # Format content based on type
        if isinstance(content, str):
            # String content (system prompts, user messages)
            if message_type == 'system_prompt':
                formatted_content = f"## 📋 System Prompt\n\n{content}\n"
            elif message_type == 'user_message':
                formatted_content = f"## 💬 User Message\n\n{content}\n"
            else:
                formatted_content = f"## {message_type}\n\n{content}\n"
        else:
            # SDK message object - format nicely
            formatted_content = self._format_message(content)

        # Format the markdown entry with timestamp header and separator
        formatted_entry = f"## 🕐 `{timestamp}` - {message_type.upper()}\n\n"
        formatted_entry += f"{formatted_content}\n"
        formatted_entry += "\n---\n\n"  # Markdown horizontal rule as separator

        # Print to console if verbose mode is on
        if self.verbose_claude_code:
            print(formatted_content)
            print("\n---\n")

        # Save to files in per-CVE .logs/ directory
        if self.save_conversations:
            logs_dir = self._get_session_logs_dir(session_id)
            if logs_dir:
                # Save Markdown format (human-readable)
                md_file = logs_dir / f"{agent_type}_conversation.md"
                with open(md_file, 'a', encoding='utf-8') as f:
                    f.write(formatted_entry)

                # Save JSON format (raw data for analysis)
                json_file = logs_dir / f"{agent_type}_conversation.json"
                json_entry = {
                    'timestamp': timestamp,
                    'message_type': message_type,
                    'content': self._serialize_message(content),
                    'metadata': metadata or {}
                }

                # Append to JSON array (read existing, append, write back)
                existing_data = []
                if json_file.exists():
                    try:
                        with open(json_file, 'r', encoding='utf-8') as f:
                            existing_data = json.load(f)
                    except (json.JSONDecodeError, IOError):
                        existing_data = []

                existing_data.append(json_entry)
                with open(json_file, 'w', encoding='utf-8') as f:
                    json.dump(existing_data, f, indent=2, ensure_ascii=False)

    def _log_workflow_step(self, session_id: str, step: str, details: Optional[str] = None, agent_type: Optional[AgentType] = None) -> None:
        """
        Log workflow steps for better visibility.

        Args:
            session_id: Session ID
            step: Workflow step name
            details: Optional step details
            agent_type: Optional agent type (use when session not yet in self.sessions)
        """
        if not self.verbose_claude_code:
            return

        # Get agent type from parameter or session
        if agent_type:
            agent_name = agent_type.value
        else:
            session = self.sessions.get(session_id)
            agent_name = session.agent_type.value if session else "unknown"

        print(f"\n🔄 [{agent_name.upper()}] WORKFLOW: {step}")
        if details:
            print(f"   Details: {details}")
        print()

    def _get_agent_prompt(self, agent_type: AgentType) -> str:
        """
        Load agent prompt from markdown file.

        Prompt filename is read from config.yaml agents.prompts section,
        with fallback to {agent_type}.md if not specified.

        Args:
            agent_type: Type of agent

        Returns:
            Prompt text content
        """
        # Get prompt filename from config, fallback to default
        prompts_config = self.config.get('agents', {}).get('prompts', {})
        prompt_filename = prompts_config.get(agent_type.value, f"{agent_type.value}.md")

        prompt_file = self.agent_prompts_dir / prompt_filename
        if not prompt_file.exists():
            raise FileNotFoundError(f"Agent prompt not found: {prompt_file}")

        return prompt_file.read_text()

    async def create_session(
        self,
        agent_type: AgentType,
        cve_id: str,
        working_dir: Path,
        metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Create a new Claude Code session for an agent.

        Args:
            agent_type: Type of agent to create
            cve_id: CVE identifier
            working_dir: Working directory for agent
            metadata: Optional metadata for session

        Returns:
            Session ID
        """
        self.logger.debug(f"Creating {agent_type.value} session for {cve_id}")

        # Generate session ID
        session_id = f"{agent_type.value}_{cve_id}_{datetime.now().timestamp()}"

        # Log workflow step (pass agent_type since session not yet stored)
        self._log_workflow_step(session_id, "Session Creation", f"Creating {agent_type.value} agent for {cve_id}", agent_type)

        # Get tool restrictions (denylist)
        disallowed_tools = ToolController.get_disallowed_tools(agent_type)
        self._log_workflow_step(session_id, "Tool Permission Setup", f"Disallowed tools: {disallowed_tools}", agent_type)

        # Create file access hooks for SDK enforcement
        # Note: Using hooks instead of can_use_tool callback due to SDK bugs (GitHub #227, #200)
        file_access_hooks = create_file_access_hooks(agent_type, working_dir)
        self._log_workflow_step(session_id, "File Access Control", f"Enforcing file access rules via hooks for {agent_type.value}", agent_type)

        # Load agent prompt
        agent_prompt = self._get_agent_prompt(agent_type)
        self._log_workflow_step(session_id, "Agent Prompt Loaded", f"System prompt configured for {agent_type.value}", agent_type)

        # Get agent-specific model
        selected_model = self._get_model_for_agent(agent_type)
        self._log_workflow_step(session_id, "Model Selection", f"Using model: {selected_model}", agent_type)

        # Create Claude Agent SDK options
        # Security is enforced via PreToolUse hooks (application-level):
        # - Dangerous command pattern detection (rm -rf /, docker prune, etc.)
        # - System path write detection (echo > /etc/passwd, etc.)
        # - File tool path whitelist (Read/Write/Edit only in working directory)
        #
        # Note: Using hooks instead of can_use_tool callback due to SDK bugs
        # (see GitHub issues #227, #200). Hooks work reliably in streaming mode.
        #
        # Note: OS-level sandbox is disabled because:
        # - Most commands need network access (git, pip, docker, curl)
        # - Sandbox's file isolation conflicts with our workflow

        # Create stderr handler to capture CLI errors
        def stderr_handler(line: str):
            # Filter out CLI's built-in skill improvement noise (non-fatal AbortError)
            if 'skill_improvement_apply' in line or 'Error in hook callback' in line:
                return
            self.logger.warning(f"[{session_id}] CLI stderr: {line}")

        options = ClaudeAgentOptions(
            system_prompt=agent_prompt,  # Agent's instruction
            model=selected_model,  # Agent-specific model
            cwd=str(working_dir),  # Working directory
            disallowed_tools=disallowed_tools,  # Tool restrictions (denylist)
            permission_mode='acceptEdits',  # Hooks handle all permission decisions (compatible with root)
            hooks=file_access_hooks,  # File access control via PreToolUse hooks
            stderr=stderr_handler,  # Capture CLI stderr for debugging
            setting_sources=[],  # Don't load user/project settings (prevents plugin conflicts)
            max_buffer_size=10 * 1024 * 1024,  # 10MB buffer (default 1MB too small for large file content)
        )
        self._log_workflow_step(session_id, "SDK Client Configuration", "Claude Agent SDK options prepared", agent_type)

        # Create Claude SDK client (context manager)
        sdk_client = ClaudeSDKClient(options=options)

        # Connect SDK client (preferred over __aenter__)
        try:
            await sdk_client.connect()
        except Exception as e:
            # Clean up SDK client if connect fails
            try:
                await sdk_client.disconnect()
            except Exception:
                pass  # Ignore disconnect errors
            raise  # Re-raise original exception
        self._log_workflow_step(session_id, "SDK Client Initialized", "Claude Agent SDK client started", agent_type)

        # Store session info
        self.sessions[session_id] = AgentSession(
            agent_type=agent_type,
            working_dir=working_dir,
            session_id=session_id,
            created_at=datetime.now(),
            last_active=datetime.now(),
            metadata=metadata or {},
            model=selected_model,  # Store selected model
            sdk_client=sdk_client  # Store SDK client
        )

        # Log system prompt and initial message
        self._log_conversation(session_id, "system_prompt", agent_prompt, {
            "agent_type": agent_type.value,
            "model": selected_model
        })
        
        self.logger.info(f"Session created: {session_id}")
        self.logger.debug(f"Disallowed tools: {disallowed_tools}")

        return session_id

    async def run_message(
        self,
        session_id: str,
        message: str,
        timeout: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Send a message and wait for completion - unified entry point.

        This is the single method for all agent interactions:
        - Initial messages
        - Missing files retry messages
        - Feedback messages
        - Retry messages

        Args:
            session_id: ID of the session
            message: Message to send
            timeout: Optional timeout in seconds

        Returns:
            Completion result from wait_for_completion
        """
        if session_id not in self.sessions:
            raise ValueError(f"Session not found: {session_id}")

        session = self.sessions[session_id]
        session.last_active = datetime.now()

        self.logger.debug(f"Running message for {session.agent_type.value}")

        # Log the message being sent
        self._log_workflow_step(session_id, "Running Message", f"Sending message to {session.agent_type.value}")
        self._log_conversation(session_id, "user_message", message, {"model": session.model})

        if session.sdk_client is None:
            raise ValueError(f"SDK client not initialized for session: {session_id}")

        # Send message via Claude Agent SDK
        await session.sdk_client.query(message)

        # Wait for completion and return result
        return await self.wait_for_completion(session_id, timeout)

    async def wait_for_completion(
        self,
        session_id: str,
        timeout: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Wait for agent to complete its task.

        Args:
            session_id: ID of the session
            timeout: Optional timeout in seconds

        Returns:
            Completion result
        """
        if session_id not in self.sessions:
            raise ValueError(f"Session not found: {session_id}")

        session = self.sessions[session_id]

        # Get timeout from config if not specified
        if timeout is None:
            agent_name = session.agent_type.value
            timeout = self.config.get('agents', {}).get('timeouts', {}).get(agent_name, 1200)

        self.logger.debug(f"Waiting for {session.agent_type.value} completion (timeout: {timeout}s)")
        self._log_workflow_step(session_id, "Waiting for Completion", f"Listening for {session.agent_type.value} responses")

        if session.sdk_client is None:
            raise ValueError(f"SDK client not initialized for session: {session_id}")

        # Receive and process all responses from agent
        responses = []
        start_time = datetime.now()

        try:
            # Use asyncio.wait_for to enforce timeout on the entire receive loop
            async def receive_all_responses():
                async for message in session.sdk_client.receive_response():
                    responses.append(message)

                    # Log each agent response (pass message object for proper formatting)
                    self._log_conversation(session_id, "agent_response", message)

                    self.logger.debug(f"Received message: {message}")

            await asyncio.wait_for(receive_all_responses(), timeout=timeout)

            duration = (datetime.now() - start_time).total_seconds()
            self._log_workflow_step(session_id, "Session Completed", f"Completed successfully in {duration:.2f}s with {len(responses)} responses")
            return {
                'status': 'completed',
                'session_id': session_id,
                'duration': duration,
                'responses': responses
            }

        except asyncio.TimeoutError:
            elapsed = (datetime.now() - start_time).total_seconds()
            self.logger.warning(f"Session {session_id} timed out after {elapsed}s")
            self._log_workflow_step(session_id, "Session Timeout", f"Timed out after {elapsed}s")
            return {
                'status': 'timeout',
                'session_id': session_id,
                'duration': elapsed,
                'responses': responses
            }

        except asyncio.CancelledError:
            # CancelledError is BaseException in Python 3.8+, must be caught explicitly
            # This prevents cancellation from propagating and affecting other concurrent tasks
            elapsed = (datetime.now() - start_time).total_seconds()
            self.logger.warning(f"Session {session_id} was cancelled (SIGTERM/-15) after {elapsed}s")
            self._log_workflow_step(session_id, "Session Cancelled", f"Cancelled after {elapsed}s with {len(responses)} responses")
            return {
                'status': 'cancelled',
                'session_id': session_id,
                'duration': elapsed,
                'responses': responses,
                'error': 'Task was cancelled (likely SIGTERM from another task failure)'
            }

        except Exception as e:
            error_with_tb = f"{str(e)}\n\nTraceback:\n{traceback.format_exc()}"
            self.logger.error(f"Session {session_id} error: {e}", exc_info=True)
            self._log_workflow_step(session_id, "Session Error", f"Error: {str(e)}")
            self._log_conversation(session_id, "system_error", error_with_tb)
            return {
                'status': 'error',
                'session_id': session_id,
                'error': str(e),
                'responses': responses
            }

    async def close_session(self, session_id: str) -> None:
        """
        Close an agent session and clean up resources.

        Args:
            session_id: ID of the session
        """
        if session_id not in self.sessions:
            self.logger.warning(f"Attempted to close non-existent session: {session_id}")
            return

        session = self.sessions[session_id]
        self.logger.info(f"Closing session: {session_id}")
        self._log_workflow_step(session_id, "Session Cleanup", f"Closing {session.agent_type.value} session")

        # Disconnect SDK client (preferred over __aexit__)
        if session.sdk_client:
            try:
                await session.sdk_client.disconnect()
                self._log_workflow_step(session_id, "SDK Client Closed", "Claude Agent SDK client terminated")
            except RuntimeError as e:
                # Cancel scope errors are common when tasks are cancelled - log at debug level
                if "cancel scope" in str(e).lower():
                    self.logger.debug(f"SDK client already closed or cancelled: {session_id}")
                else:
                    self.logger.warning(f"Error disconnecting SDK client: {session_id}, error={e}")
            except Exception as e:
                # Log other errors but don't raise - session cleanup should continue
                self.logger.warning(f"Error disconnecting SDK client: {session_id}, error={e}")

        # Log session summary
        if self.verbose_claude_code or self.save_conversations:
            duration = (datetime.now() - session.created_at).total_seconds()
            self._log_conversation(session_id, "session_summary", 
                f"Session Summary:\n"
                f"Agent Type: {session.agent_type.value}\n"
                f"Model: {session.model}\n"
                f"Duration: {duration:.2f} seconds\n"
                f"Working Directory: {session.working_dir}\n"
                f"Created: {session.created_at}\n"
                f"Last Active: {session.last_active}\n"
                f"Metadata: {session.metadata}")

        # Remove from tracking
        del self.sessions[session_id]

    def get_active_sessions(self, agent_type: Optional[AgentType] = None) -> List[AgentSession]:
        """
        Get list of active sessions, optionally filtered by agent type.

        Args:
            agent_type: Optional filter by agent type

        Returns:
            List of active sessions
        """
        sessions = list(self.sessions.values())

        if agent_type:
            sessions = [s for s in sessions if s.agent_type == agent_type]

        return sessions

    async def cleanup_all_sessions(self) -> None:
        """Close all active sessions"""
        self.logger.info("Cleaning up all sessions")

        for session_id in list(self.sessions.keys()):
            await self.close_session(session_id)

    def get_session_info(self, session_id: str) -> Optional[AgentSession]:
        """Get information about a session"""
        return self.sessions.get(session_id)

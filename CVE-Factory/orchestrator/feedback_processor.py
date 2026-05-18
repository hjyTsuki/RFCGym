"""
Agent Feedback Processing System

Handles parsing of agent result XML files and routing feedback to responsible agents.
Supports the feedback loop where agents can report issues with files created by other agents.
"""

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from .tool_controller import AgentType


@dataclass
class FeedbackIssue:
    """Represents a single feedback issue"""
    name: str  # File name or issue identifier
    reason: str  # Description of the issue


@dataclass
class AgentResult:
    """Represents the complete result from an agent"""
    status: str  # "success", "pause", "error" 
    message: Optional[str] = None
    issues: List[FeedbackIssue] = None
    
    def __post_init__(self):
        if self.issues is None:
            self.issues = []


class FeedbackProcessor:
    """
    Processes agent feedback and routes issues to responsible agents.
    """
    
    def __init__(self, working_dir: Path):
        """
        Initialize feedback processor.
        
        Args:
            working_dir: CVE working directory
        """
        self.working_dir = Path(working_dir)
        self.state_dir = self.working_dir / ".agent_state"
    
    def parse_agent_result_xml(self, agent_type: AgentType) -> AgentResult:
        """
        Parse agent result XML file.
        
        Args:
            agent_type: Type of agent that generated the result
            
        Returns:
            AgentResult object containing parsed information
        """
        xml_file = self.state_dir / f"{agent_type.value}-res.xml"
        
        if not xml_file.exists():
            return AgentResult(
                status="error",
                message=f"No {agent_type.value}-res.xml found",
                issues=[FeedbackIssue(
                    name="missing_result_file",
                    reason=f"Agent {agent_type.value} did not generate result file: {xml_file}"
                )]
            )
        
        try:
            tree = ET.parse(str(xml_file))
            root = tree.getroot()
            
            # Parse basic result info
            status = self._get_text_safe(root, 'status', 'unknown')
            message = self._get_text_safe(root, 'message')
            
            # Parse issues/feedback - ONLY feedback structure allowed
            issues = []
            
            if status == "pause":
                # Agent must use feedback structure when pausing
                feedback_elem = root.find('feedback')
                if feedback_elem is not None:
                    issues.extend(self._parse_feedback_element(feedback_elem))
                else:
                    # No feedback element found but status is pause - this is an error
                    return AgentResult(
                        status="error",
                        message="Status is 'pause' but no <feedback> element found",
                        issues=[FeedbackIssue(
                            name="invalid_xml_structure",
                            reason="When status is 'pause', must include <feedback><file><name>...</name><reason>...</reason></file></feedback> structure"
                        )]
                    )
            
            return AgentResult(
                status=status,
                message=message,
                issues=issues
            )
            
        except ET.ParseError as e:
            return AgentResult(
                status="error",
                message=f"Failed to parse XML: {e}",
                issues=[FeedbackIssue(
                    name="xml_parse_error",
                    reason=f"Invalid XML format in {xml_file}: {e}"
                )]
            )
        except Exception as e:
            return AgentResult(
                status="error",
                message=f"Unexpected error: {e}",
                issues=[FeedbackIssue(
                    name="processing_error",
                    reason=f"Error processing result file: {e}"
                )]
            )
    
    def _get_text_safe(self, element: ET.Element, tag: str, default: str = None) -> Optional[str]:
        """Safely get text content from XML element"""
        child = element.find(tag)
        if child is not None and child.text:
            return child.text.strip()
        return default
    
    def _parse_feedback_element(self, feedback_elem: ET.Element) -> List[FeedbackIssue]:
        """Parse feedback element containing multiple file issues"""
        issues = []
        
        # Only look for file-specific feedback
        for file_elem in feedback_elem.findall('file'):
            name = self._get_text_safe(file_elem, 'name')
            reason = self._get_text_safe(file_elem, 'reason')
            
            if name and reason:
                issues.append(FeedbackIssue(
                    name=name,
                    reason=reason
                ))
        
        return issues
    
    
    def format_feedback_message(
        self,
        issue: FeedbackIssue,
        from_agent: AgentType,
        to_agent: AgentType
    ) -> str:
        """
        Format a feedback message to send to the responsible agent.
        
        Args:
            issue: The issue to report
            from_agent: Agent that reported the issue
            to_agent: Agent that should fix the issue
            
        Returns:
            Formatted feedback message
        """
        message = f"""
FEEDBACK from {from_agent.value.upper()}:

File: {issue.name}
Problem: {issue.reason}

Please address this issue and update your work accordingly. 
Once fixed, continue with your original task.
"""
        
        return message.strip()
    
    def create_sample_result_xml(
        self,
        agent_type: AgentType,
        status: str = "success",
        message: str = None,
        issues: List[FeedbackIssue] = None
    ) -> None:
        """
        Create a sample result XML file for testing.
        
        Args:
            agent_type: Agent type
            status: Result status  
            message: Optional message
            issues: List of issues
        """
        if issues is None:
            issues = []
        
        root = ET.Element("result")
        
        # Basic info
        status_elem = ET.SubElement(root, "status")
        status_elem.text = status
        
        if message:
            msg_elem = ET.SubElement(root, "message") 
            msg_elem.text = message
        
        # Issues - only feedback structure allowed
        if issues and status == "pause":
            feedback_elem = ET.SubElement(root, "feedback")
            for issue in issues:
                file_elem = ET.SubElement(feedback_elem, "file")
                
                name_elem = ET.SubElement(file_elem, "name")
                name_elem.text = issue.name
                
                reason_elem = ET.SubElement(file_elem, "reason")
                reason_elem.text = issue.reason
        
        # Create the XML file
        xml_file = self.state_dir / f"{agent_type.value}-res.xml"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        
        tree = ET.ElementTree(root)
        tree.write(str(xml_file), encoding='utf-8', xml_declaration=True)
    
    def validate_xml_schema(self, agent_type: AgentType) -> tuple[bool, Optional[str]]:
        """
        Validate that the agent result XML follows expected schema.
        
        Args:
            agent_type: Agent type
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        xml_file = self.state_dir / f"{agent_type.value}-res.xml"
        
        if not xml_file.exists():
            return False, f"Result file not found: {xml_file}"
        
        try:
            tree = ET.parse(str(xml_file))
            root = tree.getroot()
            
            if root.tag != "result":
                return False, "Root element must be 'result'"
            
            # Check required elements
            status = root.find('status')
            if status is None:
                return False, "Missing required 'status' element"
            
            status_value = status.text
            if status_value not in ['success', 'pause', 'error']:
                return False, f"Invalid status value: {status_value}"
            
            return True, None
            
        except ET.ParseError as e:
            return False, f"XML parse error: {e}"
        except Exception as e:
            return False, f"Validation error: {e}"


# XML format specification:

XML_FORMAT_EXAMPLES = {
    "success": """
<result>
    <status>success</status>
    <message>All tasks completed successfully</message>
</result>
""",
    
    "pause_with_feedback": """
<result>
    <status>pause</status>
    <feedback>
        <file>
            <name>task-deps/config.json</name>
            <reason><![CDATA[
Missing required configuration file.
Generator should create task-deps/config.json with:
{
    "database": {
        "host": "localhost", 
        "port": 5432,
        "name": "app_db"
    }
}
            ]]></reason>
        </file>
        <file>
            <name>Dockerfile</name>
            <reason><![CDATA[
Missing COPY command for config file.
Add: COPY task-deps/config.json /app/config/
            ]]></reason>
        </file>
    </feedback>
</result>
""",
    
    "error": """
<result>
    <status>error</status>
    <message>Failed to complete task due to unexpected error</message>
</result>
"""
}
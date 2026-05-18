"""
Data Models for Multi-Agent CVE Reproduction System

This module contains all dataclasses and type definitions used across the orchestrator.
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from datetime import datetime

from .tool_controller import AgentType
from .feedback_processor import FeedbackIssue


# =============================================================================
# Result Types
# =============================================================================

@dataclass
class AgentResult:
    """Result of an agent execution."""
    success: bool
    error: Optional[str] = None
    phase_key: Optional[str] = None
    attempts: int = 1
    missing_files: List[str] = field(default_factory=list)

    @staticmethod
    def ok(phase_key: str = None, attempts: int = 1) -> 'AgentResult':
        return AgentResult(success=True, phase_key=phase_key, attempts=attempts)

    @staticmethod
    def fail(error: str, phase_key: str = None, attempts: int = 1, missing_files: List[str] = None) -> 'AgentResult':
        return AgentResult(success=False, error=error, phase_key=phase_key, attempts=attempts, missing_files=missing_files or [])


# =============================================================================
# Phase Definitions
# =============================================================================

@dataclass
class PhaseDefinition:
    """Definition for an agent phase - structural data only.

    Note: max_retries comes from config.yaml's orchestrator.max_retries,
    not from this definition. This keeps tunable parameters in config.yaml.
    """
    agent_type: AgentType
    phase_num: int
    phase_name: str
    output_dirs: List[str] = field(default_factory=list)
    required_files: List[str] = field(default_factory=list)
    needs_cve_content: bool = False  # legacy name; semantics: needs_scenario_content
    # RFCGym additions
    # bug_layer (single-value enum; single-implementation bugs are explicitly
    # OUT OF SCOPE and have no layer assigned):
    #   'L1' - Spec-level design flaw (RFC itself ambiguous/conflicting)
    #          # protocols: 1; attack: formal analysis
    #   'L2' - Cross-version translation within one protocol family
    #          # protocols: 1; attack: diff translated wire output
    #          # e.g. HTTP/3 <-> HTTP/1.1 (H3Act)
    #   'L3' - Cross-vendor variance (same protocol+version)
    #          # protocols: 1; attack: diff parallel implementations
    #          # e.g. Request Smuggling, T-Reqs
    #   'L4' - Cross-protocol composition mismatch
    #          # protocols: >=2; attack: trust-chain analysis
    #          # e.g. Composition Kills (SPF/From), TLS SNI/HTTP Host
    bug_layer: Optional[str] = None
    requires_known_attacks: bool = False      # if True, attack_verifier requires
                                              # >=1 known attack to reproduce


# Phase definitions for all RFCGym agent phases
# Tunable parameters (timeouts, retries, limits) are in config.yaml
PHASE_DEFINITIONS: Dict[str, PhaseDefinition] = {
    'protocol_analyzer': PhaseDefinition(
        agent_type=AgentType.PROTOCOL_ANALYZER,
        phase_num=1,
        phase_name="Protocol Analyzer - Spec, Vendor Matrix, Known Attacks",
        output_dirs=['.agent_state/analyzer_output'],
        required_files=[
            '.agent_state/protocol_analyzer-res.xml',
            '.agent_state/analyzer_output/public.md',
            '.agent_state/analyzer_output/for_scenario_generator.md',
            '.agent_state/analyzer_output/for_scenario_builder.md',
            '.agent_state/analyzer_output/for_attack_verifier.md',
            '.agent_state/analyzer_output/vendor_matrix.md',
            '.agent_state/analyzer_output/known_attacks.yaml',
            # NOTE: for_fuzzer.md intentionally omitted - the fuzzer / POC judger
            # belong to the evaluation pipeline, not env construction.
        ],
        needs_cve_content=True,
    ),
    'scenario_generator': PhaseDefinition(
        agent_type=AgentType.SCENARIO_GENERATOR,
        phase_num=2,
        phase_name="Scenario Generator - Tests for Service Liveness + Known Attacks",
        output_dirs=['.agent_state/generator_output', 'tests'],
        required_files=[
            '.agent_state/scenario_generator-res.xml',
            'task.yaml',
            'tests/test_service_alive.py',   # replaces test_func.py
            'tests/test_known_attacks.py',   # replaces test_vuln.py (assertion inverted)
            'tests/run-tests.sh',
            # NOTE: solution.sh deliberately omitted - no fix verification in RFCGym
        ],
        needs_cve_content=False,
    ),
    'scenario_builder': PhaseDefinition(
        agent_type=AgentType.SCENARIO_BUILDER,
        phase_num=3,
        phase_name="Scenario Builder - Multi-Vendor Protocol Service Stack",
        output_dirs=['.agent_state/builder_output', 'pcaps'],
        required_files=[
            '.agent_state/scenario_builder-res.xml',
            'Dockerfile',
            'docker-compose.yaml',
        ],
        needs_cve_content=False,
    ),
    'attack_verifier': PhaseDefinition(
        agent_type=AgentType.ATTACK_VERIFIER,
        phase_num=4,
        phase_name="Attack Verifier - Known Attacks Reproduce + Env Ready",
        output_dirs=['.agent_state/verifier_output'],
        required_files=['.agent_state/attack_verifier-res.xml'],
        requires_known_attacks=True,
    ),
    # SOLVER phase removed (RFCGym does not verify fixes)
    'env_finalizer': PhaseDefinition(
        agent_type=AgentType.ENV_FINALIZER,
        phase_num=5,
        phase_name="Environment Finalizer - Compliance + Stack Sanity",
        output_dirs=['.agent_state/finalizer_output'],
        required_files=['.agent_state/env_finalizer-res.xml'],
        needs_cve_content=False,
    ),

    # === Runtime evaluation phases (run independently after synthesis) ===
    'fuzzer': PhaseDefinition(
        agent_type=AgentType.FUZZER,
        phase_num=6,
        phase_name="Fuzzer - Runtime Bug Hunting (Evaluation Target)",
        output_dirs=[
            '.agent_state/fuzzer_output',
            'pocs',                       # POC archive (one dir per POC)
            'fuzz_results',               # all testcases + responses
        ],
        required_files=[
            '.agent_state/fuzzer-res.xml',
            'pocs/index.json',
        ],
        needs_cve_content=False,
    ),
    'poc_judger': PhaseDefinition(
        agent_type=AgentType.POC_JUDGER,
        phase_num=7,
        phase_name="POC Judger - Validity + Novelty + Severity",
        output_dirs=['.agent_state/poc_judger_output'],
        required_files=[
            '.agent_state/poc_judger-res.xml',
            '.agent_state/poc_judger_output/poc_report.md',
            '.agent_state/poc_judger_output/poc_scores.json',
        ],
        needs_cve_content=False,
    ),

    # === Retained extension phases (lightly repurposed) ===
    'changer': PhaseDefinition(
        agent_type=AgentType.CHANGER,
        phase_num=8,
        phase_name="Changer - Terminal Bench Transformation",
        output_dirs=['.agent_state/changer_output'],
        required_files=[
            '.agent_state/changer-res.xml',
            '.agent_state/changer_output/transform_report.md',
        ],
        needs_cve_content=False,
    ),
    'comparer': PhaseDefinition(
        agent_type=AgentType.COMPARER,
        phase_num=9,
        phase_name="Comparer - POC Coverage vs Reference Paper",
        output_dirs=['.agent_state/comparer_output'],
        required_files=[
            '.agent_state/comparer-res.xml',
            '.agent_state/comparer_output/comparison_report.md',
        ],
        needs_cve_content=False,
    ),
    'expert': PhaseDefinition(
        agent_type=AgentType.EXPERT,
        phase_num=10,
        phase_name="Expert - Reference POC Adaptation Test",
        output_dirs=['.agent_state/expert_output'],
        required_files=[
            '.agent_state/expert-res.xml',
            '.agent_state/expert_output/adaptation_report.md',
        ],
        needs_cve_content=False,
    ),
}


# =============================================================================
# Phase Records and Task Status
# =============================================================================

@dataclass
class PhaseRecord:
    """Record of a single phase execution"""
    name: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    attempts: int = 1
    status: str = 'completed'  # 'completed', 'failed'
    feedbacks: List[FeedbackIssue] = None  # Parsed from {agent}-res.xml

    def __post_init__(self):
        if self.feedbacks is None:
            self.feedbacks = []

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return {
            'name': self.name,
            'started_at': self.started_at.isoformat(),
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'attempts': self.attempts,
            'status': self.status,
            'feedbacks': [{'name': f.name, 'reason': f.reason} for f in self.feedbacks]
        }


@dataclass
class CVETaskStatus:
    """Status of a CVE reproduction task"""
    cve_id: str
    status: str  # 'pending', 'in_progress', 'completed', 'failed'
    current_phase: str
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error: Optional[str] = None
    phases_completed: List[PhaseRecord] = None
    working_dir: Optional[Path] = None  # For saving to .logs/

    def __post_init__(self):
        if self.phases_completed is None:
            self.phases_completed = []

    def add_phase(self, name: str, started_at: datetime, completed_at: datetime = None,
                  attempts: int = 1, status: str = 'completed',
                  feedbacks: List[FeedbackIssue] = None) -> None:
        """Add a phase record and persist to .logs/task_status.json"""
        self.phases_completed.append(PhaseRecord(
            name=name,
            started_at=started_at,
            completed_at=completed_at or datetime.now(),
            attempts=attempts,
            status=status,
            feedbacks=feedbacks or []
        ))
        # Persist to .logs/
        if self.working_dir:
            logs_dir = self.working_dir / ".logs"
            logs_dir.mkdir(parents=True, exist_ok=True)
            with open(logs_dir / "task_status.json", 'w') as f:
                json.dump(self.to_dict(), f, indent=2)

    def get_phase_names(self) -> List[str]:
        """Get list of completed phase names (for backward compatibility)"""
        return [p.name for p in self.phases_completed]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return {
            'cve_id': self.cve_id,
            'status': self.status,
            'current_phase': self.current_phase,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'error': self.error,
            'phases_completed': [p.to_dict() for p in self.phases_completed]
        }

    @classmethod
    def load_from_logs(cls, working_dir: Path) -> Optional['CVETaskStatus']:
        """Load task status from .logs/task_status.json"""
        status_file = working_dir / ".logs" / "task_status.json"
        if not status_file.exists():
            return None

        try:
            with open(status_file, 'r') as f:
                data = json.load(f)

            # Reconstruct PhaseRecords
            phases = []
            for p in data.get('phases_completed', []):
                feedbacks = [FeedbackIssue(**fb) for fb in p.get('feedbacks', [])]
                phases.append(PhaseRecord(
                    name=p['name'],
                    started_at=datetime.fromisoformat(p['started_at']),
                    completed_at=datetime.fromisoformat(p['completed_at']) if p.get('completed_at') else None,
                    attempts=p.get('attempts', 1),
                    status=p.get('status', 'completed'),
                    feedbacks=feedbacks
                ))

            return cls(
                cve_id=data['cve_id'],
                status=data['status'],
                current_phase=data['current_phase'],
                started_at=datetime.fromisoformat(data['started_at']) if data.get('started_at') else None,
                completed_at=datetime.fromisoformat(data['completed_at']) if data.get('completed_at') else None,
                error=data.get('error'),
                phases_completed=phases,
                working_dir=working_dir
            )
        except Exception as e:
            logging.getLogger(__name__).warning(f"Failed to load task status from {status_file}: {e}")
            return None

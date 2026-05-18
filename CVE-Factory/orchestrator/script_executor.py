"""
Script Executor - Automatic Script Execution

This module handles automatic execution of scripts like docker_auto_start.py
and test_validator.py. These scripts are executed by the orchestrator, not by agents.
"""

import asyncio
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional


class ScriptExecutor:
    """
    Executes automatic scripts for Docker management, testing, etc.

    These scripts are part of the orchestrator, not agent tools.
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize Script Executor.

        Args:
            config: Configuration dictionary
        """
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.base_dir = Path(__file__).parent.parent

        # Get Docker settings
        docker_config = config.get('docker', {})
        self.proxy_config = docker_config.get('proxy', {})
        self.max_memory = docker_config.get('max_memory_per_container', '20G')
        self.max_cpu = docker_config.get('max_cpu_per_container', 16)

    def _get_env_with_proxy(self) -> Dict[str, str]:
        """
        Get environment variables with proxy settings.

        Returns:
            Environment dictionary
        """
        env = os.environ.copy()

        if self.proxy_config:
            env.update({
                'http_proxy': self.proxy_config.get('http_proxy', ''),
                'https_proxy': self.proxy_config.get('https_proxy', ''),
                'HTTP_PROXY': self.proxy_config.get('http_proxy', ''),
                'HTTPS_PROXY': self.proxy_config.get('https_proxy', ''),
                'no_proxy': self.proxy_config.get('no_proxy', ''),
                'NO_PROXY': self.proxy_config.get('no_proxy', ''),
            })

        return env

    def _get_docker_build_args(self) -> list:
        """Get --build-arg flags for docker build to pass proxy settings."""
        args = []
        if self.proxy_config:
            http_proxy = self.proxy_config.get('http_proxy', '')
            https_proxy = self.proxy_config.get('https_proxy', '')
            no_proxy = self.proxy_config.get('no_proxy', '')
            if http_proxy:
                args.extend(['--build-arg', f'http_proxy={http_proxy}'])
                args.extend(['--build-arg', f'HTTP_PROXY={http_proxy}'])
            if https_proxy:
                args.extend(['--build-arg', f'https_proxy={https_proxy}'])
                args.extend(['--build-arg', f'HTTPS_PROXY={https_proxy}'])
            if no_proxy:
                args.extend(['--build-arg', f'no_proxy={no_proxy}'])
                args.extend(['--build-arg', f'NO_PROXY={no_proxy}'])
        return args

    def _get_container_proxy_env(self) -> str:
        """Get proxy environment export commands for container execution."""
        if not self.proxy_config:
            return ""

        exports = []
        http_proxy = self.proxy_config.get('http_proxy', '')
        https_proxy = self.proxy_config.get('https_proxy', '')
        no_proxy = self.proxy_config.get('no_proxy', '')

        if http_proxy:
            exports.append(f'export http_proxy="{http_proxy}"')
            exports.append(f'export HTTP_PROXY="{http_proxy}"')
        if https_proxy:
            exports.append(f'export https_proxy="{https_proxy}"')
            exports.append(f'export HTTPS_PROXY="{https_proxy}"')
        if no_proxy:
            exports.append(f'export no_proxy="{no_proxy}"')
            exports.append(f'export NO_PROXY="{no_proxy}"')

        return ' && '.join(exports) + ' && ' if exports else ""

    async def _get_app_container(self, working_dir: Path, cve_id: str) -> Optional[str]:
        """
        Get the main application container ID from docker-compose.

        For multi-service setups (app + db), we need to find the app container,
        not the database container. This method uses heuristics:
        1. If only one container, return it
        2. Otherwise, find container that is NOT a database (mysql, postgres, db, etc.)

        Args:
            working_dir: CVE working directory
            cve_id: CVE identifier for logging

        Returns:
            Container ID string, or None if not found
        """
        # Get all container IDs
        ps_result = await self._run_command(
            ['docker', 'compose', 'ps', '-q'],
            cwd=working_dir,
            timeout=30,
            cve_id=cve_id
        )

        if not ps_result['success'] or not ps_result['stdout'].strip():
            self.logger.error(f"[{cve_id}] No running containers found")
            return None

        container_ids = [c.strip() for c in ps_result['stdout'].strip().split('\n') if c.strip()]

        if len(container_ids) == 1:
            self.logger.debug(f"[{cve_id}] Single container found: {container_ids[0][:12]}")
            return container_ids[0]

        # Multiple containers - need to find the app container
        self.logger.debug(f"[{cve_id}] Multiple containers found ({len(container_ids)}), identifying app container...")

        # Database-related keywords to exclude
        db_keywords = ['mysql', 'postgres', 'mariadb', 'mongo', 'redis', 'database', 'db']

        for container_id in container_ids:
            # Get container name
            inspect_result = await self._run_command(
                ['docker', 'inspect', '--format', '{{.Name}}', container_id],
                cwd=working_dir,
                timeout=10,
                cve_id=cve_id
            )

            if inspect_result['success']:
                container_name = inspect_result['stdout'].strip().lower().lstrip('/')
                self.logger.debug(f"[{cve_id}] Checking container: {container_name}")

                # Check if this looks like a database container
                is_db = any(kw in container_name for kw in db_keywords)

                if not is_db:
                    self.logger.debug(f"[{cve_id}] Selected app container: {container_name} ({container_id[:12]})")
                    return container_id

        # Fallback: return first container if no clear app container found
        self.logger.warning(f"[{cve_id}] Could not identify app container, using first: {container_ids[0][:12]}")
        return container_ids[0]

    async def _run_command(
        self,
        cmd: list,
        cwd: Path,
        timeout: Optional[int] = None,
        cve_id: str = "unknown"
    ) -> Dict[str, Any]:
        """
        Run a command asynchronously.

        Args:
            cmd: Command to run
            cwd: Working directory
            timeout: Timeout in seconds
            cve_id: CVE identifier for logging

        Returns:
            Result dictionary
        """
        self.logger.debug(f"[{cve_id}] Running command: {' '.join(cmd)}")

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._get_env_with_proxy()
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout
            )

            return {
                'success': process.returncode == 0,
                'returncode': process.returncode,
                'stdout': stdout.decode('utf-8', errors='replace'),
                'stderr': stderr.decode('utf-8', errors='replace'),
            }

        except asyncio.TimeoutError:
            # Try to get partial output before killing the process
            partial_stdout = ''
            partial_stderr = ''
            try:
                # Kill the process
                process.kill()
                # Wait briefly for process to terminate and collect any buffered output
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=5)
                partial_stdout = stdout.decode('utf-8', errors='replace')
                partial_stderr = stderr.decode('utf-8', errors='replace')
            except Exception:
                pass  # Best effort to get output

            cmd_str = ' '.join(cmd)
            self.logger.error(f"[{cve_id}] Command timed out after {timeout}s: {cmd_str}")
            if partial_stdout or partial_stderr:
                self.logger.error(f"[{cve_id}] Partial output before timeout:\nSTDOUT: {partial_stdout[-500:]}\nSTDERR: {partial_stderr[-500:]}")

            return {
                'success': False,
                'error': f"Timeout after {timeout}s running: {cmd_str}",
                'stdout': partial_stdout,
                'stderr': partial_stderr,
                'command': cmd_str
            }
        except Exception as e:
            self.logger.error(f"[{cve_id}] Command failed: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'stdout': '',
                'stderr': ''
            }

    async def docker_build_and_start(
        self,
        working_dir: Path,
        cve_id: str
    ) -> Dict[str, Any]:
        """
        Build and start Docker environment for CVE.

        This replaces the need for agents to manually run docker commands.

        Args:
            working_dir: CVE working directory
            cve_id: CVE identifier

        Returns:
            Result dictionary
        """
        self.logger.debug(f"[{cve_id}] Building and starting Docker environment")

        # Check if docker-compose.yaml exists
        compose_file = working_dir / "docker-compose.yaml"
        if not compose_file.exists():
            return {
                'success': False,
                'error': 'docker-compose.yaml not found'
            }

        # Build images with proxy settings
        self.logger.debug(f"[{cve_id}] Building Docker images...")
        build_cmd = ['docker', 'compose', 'build'] + self._get_docker_build_args()
        build_result = await self._run_command(
            build_cmd,
            cwd=working_dir,
            timeout=600,  # 10 minutes for build
            cve_id=cve_id
        )

        if not build_result['success']:
            self.logger.error(f"[{cve_id}] Docker build failed")
            return {
                'success': False,
                'error': 'Docker build failed',
                'details': build_result
            }

        # Start containers with resource limits
        self.logger.debug(f"[{cve_id}] Starting containers (memory={self.max_memory}, cpus={self.max_cpu})...")

        # Note: Resource limits are best set in docker-compose.yaml
        # But we can also use environment variables or override
        start_result = await self._run_command(
            ['docker', 'compose', 'up', '-d'],
            cwd=working_dir,
            timeout=300,  # 5 minutes to start
            cve_id=cve_id
        )

        if not start_result['success']:
            self.logger.error(f"[{cve_id}] Container start failed")
            return {
                'success': False,
                'error': 'Container start failed',
                'details': start_result
            }

        # Wait for containers to be healthy
        await asyncio.sleep(10)

        # Check container status
        status_result = await self._run_command(
            ['docker', 'compose', 'ps'],
            cwd=working_dir,
            timeout=30,
            cve_id=cve_id
        )

        self.logger.debug(f"[{cve_id}] Docker environment started successfully")

        return {
            'success': True,
            'build_output': build_result['stdout'],
            'start_output': start_result['stdout'],
            'status': status_result['stdout']
        }

    async def docker_stop_and_cleanup(
        self,
        working_dir: Path,
        cve_id: str,
        remove_images: bool = False
    ) -> Dict[str, Any]:
        """
        Stop and remove Docker containers/images for CVE.

        Args:
            working_dir: CVE working directory
            cve_id: CVE identifier
            remove_images: If True, also remove images (slower but frees disk space).
                          If False, keep images for faster rebuilds (default).

        Returns:
            Result dictionary
        """
        if remove_images:
            self.logger.debug(f"[{cve_id}] Stopping Docker and removing local images/volumes")
            cmd = ['docker', 'compose', 'down', '--rmi', 'local', '--volumes']
        else:
            self.logger.debug(f"[{cve_id}] Stopping Docker (keeping images for cache)")
            cmd = ['docker', 'compose', 'down', '--volumes']

        compose_file = working_dir / "docker-compose.yaml"
        if not compose_file.exists():
            return {'success': True, 'message': 'No docker-compose.yaml found'}

        cleanup_result = await self._run_command(
            cmd,
            cwd=working_dir,
            timeout=120,
            cve_id=cve_id
        )

        return {
            'success': cleanup_result['success'],
            'output': cleanup_result['stdout'],
            'errors': cleanup_result['stderr'] if not cleanup_result['success'] else None
        }

    async def run_tests(
        self,
        working_dir: Path,
        cve_id: str,
        stage: str = 'vulnerable',
        restart_docker: bool = True
    ) -> Dict[str, Any]:
        """
        Run test suites in Docker environment via run-tests.sh.

        Args:
            working_dir: CVE working directory
            cve_id: CVE identifier
            stage: 'vulnerable' or 'fixed' - determines validation logic
            restart_docker: If True, stop and restart Docker before running tests

        Returns:
            Result dictionary with test results and validation
        """
        from .test_result_parser import validate_test_output

        self.logger.info(f"[{cve_id}] Running tests for stage: {stage}")

        # Helper to create error result with consistent format
        def make_error_result(error_msg: str, details: str = '') -> Dict[str, Any]:
            return {
                'success': False,
                'stage': stage,
                'validation': {
                    'valid': False,
                    'issues': [error_msg],
                    'expected': {},
                    'actual': {}
                },
                'raw_output': f'ERROR: {error_msg}\n\n{details}' if details else f'ERROR: {error_msg}',
                'script_exit_code': -1
            }

        # Check if tests directory exists
        tests_dir = working_dir / "tests"
        if not tests_dir.exists():
            return make_error_result('tests/ directory not found', 'Generator should create tests/ directory with test files.')

        # Check if run-tests.sh exists
        run_tests_script = tests_dir / "run-tests.sh"
        if not run_tests_script.exists():
            return make_error_result('tests/run-tests.sh not found', 'Generator should create tests/run-tests.sh script.')

        # Restart Docker if requested
        if restart_docker:
            self.logger.debug(f"[{cve_id}] Restarting Docker environment before tests...")
            await self.docker_stop_and_cleanup(working_dir, cve_id)
            build_result = await self.docker_build_and_start(working_dir, cve_id)
            if not build_result['success']:
                error_msg = f"Docker restart failed: {build_result.get('error', 'unknown error')}"
                details = build_result.get('details', {})
                stderr = details.get('stderr', '') if isinstance(details, dict) else str(details)
                return make_error_result(error_msg, f'Build/start output:\n{stderr}')

        # Get the main app container (not db container in multi-service setups)
        container_id = await self._get_app_container(working_dir, cve_id)

        if not container_id:
            return make_error_result('No running containers found', 'Docker containers failed to start. Check docker-compose.yaml and Dockerfile.')

        # Step 1: Copy tests/ directory to container
        self.logger.debug(f"[{cve_id}] Copying tests/ directory to container...")

        # Create tests directory in container
        await self._run_command(
            ['docker', 'exec', container_id, 'mkdir', '-p', '/tests'],
            cwd=working_dir,
            timeout=30,
            cve_id=cve_id
        )

        # Copy all test files to container
        copy_result = await self._run_command(
            ['docker', 'cp', 'tests/.', f'{container_id}:/tests/'],
            cwd=working_dir,
            timeout=60,
            cve_id=cve_id
        )

        if not copy_result['success']:
            self.logger.error(f"[{cve_id}] Failed to copy tests to container")
            return make_error_result('Failed to copy tests to container', f"stderr: {copy_result.get('stderr', '')}")

        self.logger.debug(f"[{cve_id}] Tests copied successfully")

        # Step 2: Run tests via run-tests.sh (with proxy if configured)
        self.logger.debug(f"[{cve_id}] Executing run-tests.sh...")
        proxy_env = self._get_container_proxy_env()
        test_cmd = f'{proxy_env}bash /tests/run-tests.sh'
        test_result = await self._run_command(
            ['docker', 'exec', container_id, 'bash', '-c', test_cmd],
            cwd=working_dir,
            timeout=600,  # 10 minutes for uv install + tests
            cve_id=cve_id
        )

        pytest_output = test_result['stdout'] + '\n' + test_result['stderr']

        # Step 3: Parse and validate results
        self.logger.debug(f"[{cve_id}] Parsing test results...")
        validation = validate_test_output(pytest_output, stage=stage)

        self.logger.debug(f"[{cve_id}] Validation result: valid={validation['valid']}")
        if validation['issues']:
            for issue in validation['issues']:
                self.logger.warning(f"[{cve_id}] Issue: {issue}")

        return {
            'success': validation['valid'],
            'stage': stage,
            'validation': validation,
            'raw_output': pytest_output,
            'script_exit_code': test_result.get('returncode', -1)
        }

    async def apply_solution(
        self,
        working_dir: Path,
        cve_id: str,
        restart_docker: bool = True
    ) -> Dict[str, Any]:
        """
        Apply solution.sh script in Docker container.

        Args:
            working_dir: CVE working directory
            cve_id: CVE identifier
            restart_docker: If True, stop and restart Docker before applying solution.
                           Set to False if Docker is already running from previous phase.

        Returns:
            Result dictionary
        """
        self.logger.info(f"[{cve_id}] Applying solution script")

        solution_file = working_dir / "solution.sh"
        if not solution_file.exists():
            return {
                'success': False,
                'error': 'solution.sh not found'
            }

        # Restart Docker if requested (default: True for clean state)
        if restart_docker:
            self.logger.debug(f"[{cve_id}] Restarting Docker environment before applying solution...")
            await self.docker_stop_and_cleanup(working_dir, cve_id)
            build_result = await self.docker_build_and_start(working_dir, cve_id)
            if not build_result['success']:
                return {
                    'success': False,
                    'error': f"Docker restart failed: {build_result.get('error', 'unknown error')}",
                    'details': build_result
                }
        else:
            self.logger.debug(f"[{cve_id}] Skipping Docker restart (using existing environment)")

        # Get the main app container (not db container in multi-service setups)
        container_id = await self._get_app_container(working_dir, cve_id)

        if not container_id:
            return {
                'success': False,
                'error': 'No running containers found'
            }

        # Ensure /app directory exists in container
        await self._run_command(
            ['docker', 'exec', container_id, 'mkdir', '-p', '/app'],
            cwd=working_dir,
            timeout=30,
            cve_id=cve_id
        )

        # Copy solution to container /app directory
        # Use relative path 'solution.sh' since cwd is already working_dir
        copy_result = await self._run_command(
            ['docker', 'cp', 'solution.sh', f'{container_id}:/app/solution.sh'],
            cwd=working_dir,
            timeout=30,
            cve_id=cve_id
        )

        if not copy_result['success']:
            return {
                'success': False,
                'error': 'Failed to copy solution.sh to container',
                'details': copy_result
            }

        # Run solution.sh from /app directory (with proxy if configured)
        proxy_env = self._get_container_proxy_env()
        solution_cmd = f'{proxy_env}cd /app && bash /app/solution.sh'
        self.logger.debug(f"[{cve_id}] Running solution.sh in container")
        run_result = await self._run_command(
            ['docker', 'exec', container_id, 'bash', '-c', solution_cmd],
            cwd=working_dir,
            timeout=300,
            cve_id=cve_id
        )

        return {
            'success': run_result['success'],
            'output': run_result['stdout'],
            'errors': run_result['stderr']
        }

    async def cleanup_cve_task(
        self,
        working_dir: Path,
        cve_id: str
    ) -> Dict[str, Any]:
        """
        Clean up temporary files and Docker resources for CVE task.

        Args:
            working_dir: CVE working directory
            cve_id: CVE identifier

        Returns:
            Result dictionary
        """
        self.logger.info(f"[{cve_id}] Cleaning up CVE task")

        # Stop and remove Docker resources (full cleanup to free disk space)
        docker_cleanup = await self.docker_stop_and_cleanup(working_dir, cve_id, remove_images=True)

        # TODO: Clean up temporary files
        # Keep only essential reproduction files

        return {
            'success': True,
            'docker_cleanup': docker_cleanup
        }

    async def get_running_container_count(self) -> int:
        """
        Get the number of currently running Docker containers.

        Returns:
            Number of running containers
        """
        result = await self._run_command(
            ['docker', 'ps', '-q'],
            cwd=self.base_dir,
            timeout=30,
            cve_id="system"
        )

        if not result['success']:
            return 0

        containers = result['stdout'].strip().split('\n')
        return len([c for c in containers if c])  # Filter empty strings

    async def cleanup_stale_containers(self) -> Dict[str, Any]:
        """
        Clean up stopped containers and dangling images.

        This is called periodically by the background cleanup task.

        Returns:
            Cleanup results
        """
        self.logger.info("Running stale container cleanup...")

        # Remove stopped containers
        prune_containers = await self._run_command(
            ['docker', 'container', 'prune', '-f'],
            cwd=self.base_dir,
            timeout=60,
            cve_id="system"
        )

        # NOTE: Disabled image prune to preserve build cache for faster rebuilds
        # prune_images = await self._run_command(
        #     ['docker', 'image', 'prune', '-f'],
        #     cwd=self.base_dir,
        #     timeout=60
        # )

        # Remove unused volumes
        prune_volumes = await self._run_command(
            ['docker', 'volume', 'prune', '-f'],
            cwd=self.base_dir,
            timeout=60,
            cve_id="system"
        )

        self.logger.info("Stale container cleanup completed")

        return {
            'success': True,
            'containers': prune_containers.get('stdout', ''),
            'volumes': prune_volumes.get('stdout', '')
        }

    async def get_docker_resource_usage(self) -> Dict[str, Any]:
        """
        Get current Docker resource usage statistics.

        Returns:
            Resource usage information
        """
        # Get container stats
        stats_result = await self._run_command(
            ['docker', 'stats', '--no-stream', '--format',
             '{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}'],
            cwd=self.base_dir,
            timeout=30,
            cve_id="system"
        )

        # Get disk usage
        disk_result = await self._run_command(
            ['docker', 'system', 'df'],
            cwd=self.base_dir,
            timeout=30,
            cve_id="system"
        )

        return {
            'container_stats': stats_result.get('stdout', ''),
            'disk_usage': disk_result.get('stdout', ''),
            'running_containers': await self.get_running_container_count()
        }

    def _check_required_files(self, working_dir: Path) -> Dict[str, Any]:
        """
        Check if all required files exist for CVE reproduction.

        Args:
            working_dir: CVE working directory

        Returns:
            Result dictionary with success status and missing files
        """
        required_files = [
            "task.yaml",
            "solution.sh",
            "Dockerfile",
            "docker-compose.yaml",
            "tests/test_func.py",
            "tests/test_vuln.py",
            "tests/run-tests.sh",
        ]

        missing = [f for f in required_files if not (working_dir / f).exists()]

        return {
            'success': len(missing) == 0,
            'missing': missing,
            'checked': required_files
        }

    async def run_cve_check(
        self,
        working_dir: Path,
        cve_id: str,
        skip_docker: bool = False,
        skip_vuln: bool = False,
        verbose: bool = False,
        cleanup_images: bool = False
    ) -> Dict[str, Any]:
        """
        Run complete CVE ready check.

        This is the core implementation used by both:
        - scripts/check_cve_ready.py (CLI tool)
        - async_orchestrator.py (pipeline phase)
        - Checker agent (verification)

        Args:
            working_dir: CVE working directory
            cve_id: CVE identifier
            skip_docker: Skip Docker tests (files only)
            skip_vuln: Skip vulnerable environment test
            verbose: Enable verbose logging
            cleanup_images: If True, remove images after check (default False).
                           Images should typically be kept during pipeline for faster rebuilds.
                           Final cleanup happens in Phase 7 (Cleanup).

        Returns:
            {
                'ready': bool,
                'cve_id': str,
                'checks': {
                    'files': {'success': bool, 'missing': list},
                    'vulnerable_test': {'success': bool, 'details': dict},
                    'solution': {'success': bool, 'details': dict},
                    'fixed_test': {'success': bool, 'details': dict}
                }
            }
        """
        self.logger.info(f"[{cve_id}] Starting CVE ready check")

        results = {
            'cve_id': cve_id,
            'checks': {},
            'ready': False
        }

        # Step 1: Check required files
        self.logger.debug(f"[{cve_id}] Step 1: Checking required files...")
        file_check = self._check_required_files(working_dir)
        results['checks']['files'] = file_check

        if not file_check['success']:
            self.logger.error(f"[{cve_id}] Missing files: {file_check['missing']}")
            return results

        self.logger.debug(f"[{cve_id}] All required files present")

        if skip_docker:
            self.logger.info(f"[{cve_id}] Skipping Docker tests (--skip-docker)")
            results['checks']['vulnerable_test'] = {'success': True, 'skipped': True}
            results['checks']['solution'] = {'success': True, 'skipped': True}
            results['checks']['fixed_test'] = {'success': True, 'skipped': True}
            results['ready'] = True
            return results

        try:
            # Step 2: Test vulnerable environment
            if skip_vuln:
                self.logger.debug(f"[{cve_id}] Skipping vulnerable environment test (--skip-vuln)")
                results['checks']['vulnerable_test'] = {'success': True, 'skipped': True}
                need_docker_restart = True
            else:
                self.logger.debug(f"[{cve_id}] Step 2: Testing vulnerable environment...")
                self.logger.debug(f"[{cve_id}]   Expected: func tests PASS, vuln tests FAIL")

                vuln_result = await self.run_tests(
                    working_dir, cve_id, stage='vulnerable', restart_docker=True
                )
                results['checks']['vulnerable_test'] = {
                    'success': vuln_result['success'],
                    'details': vuln_result
                }

                if not vuln_result['success']:
                    issues = vuln_result.get('validation', {}).get('issues', [])
                    self.logger.error(f"[{cve_id}] Vulnerable environment test failed: {issues}")
                    return results

                self.logger.debug(f"[{cve_id}] Vulnerable environment validated")
                need_docker_restart = False

            # Step 3: Apply solution
            self.logger.debug(f"[{cve_id}] Step 3: Applying solution.sh...")
            solution_result = await self.apply_solution(
                working_dir, cve_id, restart_docker=True
            )
            results['checks']['solution'] = {
                'success': solution_result['success'],
                'details': solution_result
            }

            if not solution_result['success']:
                self.logger.error(f"[{cve_id}] Solution failed: {solution_result.get('error')}")
                return results

            self.logger.debug(f"[{cve_id}] Solution applied successfully")

            # Step 4: Test fixed environment
            self.logger.debug(f"[{cve_id}] Step 4: Testing fixed environment...")
            self.logger.debug(f"[{cve_id}]   Expected: func tests PASS, vuln tests PASS")

            fixed_result = await self.run_tests(
                working_dir, cve_id, stage='fixed', restart_docker=False
            )
            results['checks']['fixed_test'] = {
                'success': fixed_result['success'],
                'details': fixed_result
            }

            if fixed_result['success']:
                self.logger.info(f"[{cve_id}] Fixed environment validated!")
                results['ready'] = True
            else:
                issues = fixed_result.get('validation', {}).get('issues', [])
                self.logger.error(f"[{cve_id}] Fixed environment test failed: {issues}")

        finally:
            # Cleanup: stop containers, optionally remove images
            # Default: keep images for faster rebuilds during pipeline
            # Final cleanup (remove_images=True) happens in Phase 7 (Cleanup)
            if cleanup_images and results['ready']:
                self.logger.debug(f"[{cve_id}] Cleaning up Docker (removing local images)...")
                await self.docker_stop_and_cleanup(working_dir, cve_id, remove_images=True)
            else:
                self.logger.debug(f"[{cve_id}] Stopping Docker (keeping images for faster rebuild)...")
                await self.docker_stop_and_cleanup(working_dir, cve_id, remove_images=False)

        return results

    def run_tb_transform(
        self,
        working_dir: Path,
        cve_id: str
    ) -> Dict[str, Any]:
        """
        Run Terminal Bench transformation on a CVE directory.

        Creates a lowercase subdirectory with transformed files:
        CVE-2025-1234/ -> CVE-2025-1234/cve-2025-1234/

        Args:
            working_dir: CVE working directory (e.g., cve_tasks/CVE-2025-1234)
            cve_id: CVE identifier

        Returns:
            Result dictionary with transformation details, including output_dir
        """
        # self.logger.info(f"[{cve_id}] Running Terminal Bench transformation")

        try:
            from scripts.tb_transformer import transform_cve_directory

            result = transform_cve_directory(working_dir)

            return {
                'success': result.success,
                'output_dir': str(result.output_dir) if result.output_dir else None,
                'steps_completed': result.steps_completed,
                'warnings': result.warnings,
                'errors': result.errors,
                'changes': result.changes
            }
        except Exception as e:
            self.logger.error(f"[{cve_id}] Transform error: {e}")
            return {
                'success': False,
                'output_dir': None,
                'error': str(e),
                'steps_completed': [],
                'warnings': [],
                'errors': [str(e)],
                'changes': {}
            }

    def run_tb_validate(
        self,
        working_dir: Path,
        cve_id: str,
        strict: bool = False
    ) -> Dict[str, Any]:
        """
        Run Terminal Bench validation on a CVE directory.

        Directly calls tb_validator functions.

        Args:
            working_dir: CVE working directory
            cve_id: CVE identifier
            strict: If True, treat warnings as errors

        Returns:
            Result dictionary with validation details
        """
        # self.logger.info(f"[{cve_id}] Running Terminal Bench validation")

        try:
            from scripts.tb_validator import validate_cve_directory

            result = validate_cve_directory(working_dir, strict=strict)

            return {
                'success': result.valid,
                'valid': result.valid,
                'checks_passed': result.checks_passed,
                'checks_failed': result.checks_failed,
                'warnings': result.warnings
            }
        except Exception as e:
            self.logger.error(f"[{cve_id}] Validate error: {e}")
            return {
                'success': False,
                'valid': False,
                'error': str(e),
                'checks_passed': [],
                'checks_failed': [str(e)],
                'warnings': []
            }


    async def run_tb_test(
        self,
        working_dir: Path,
        cve_id: str,
        agent_type: str = 'nop',
        timeout: int = 1200
    ) -> Dict[str, Any]:
        """
        Run Terminal Bench test on a CVE task.

        Command format:
            tb run --dataset-path <working_dir> --task-id <lowercase-cve-id> --agent <nop|oracle> --output-path <working_dir>/<nop|oracle>

        Validation:
            - nop: Parse post-test.txt, check func all PASSED, vuln all FAILED
            - oracle: Read results.json, check ["results"][0]["is_resolved"] == true

        Args:
            working_dir: CVE working directory (e.g., cve_tasks/CVE-2025-1234)
            cve_id: CVE identifier (will be lowercased for task-id)
            agent_type: 'nop' or 'oracle'
            timeout: Test timeout in seconds

        Returns:
            Result dictionary with test details
        """
        task_id = cve_id.lower()  # tb uses lowercase task IDs
        output_path = working_dir / agent_type

        self.logger.info(f"[{cve_id}] Running tb {agent_type} test...")

        cmd = [
            'tb', 'run',
            '--dataset-path', str(working_dir),
            '--task-id', task_id,
            '--agent', agent_type,
            '--output-path', str(output_path)
        ]

        result = await self._run_command(
            cmd,
            cwd=self.base_dir,
            timeout=timeout,
            cve_id=cve_id
        )

        stdout = result.get('stdout', '')
        stderr = result.get('stderr', '')

        # Parse results based on agent type
        test_passed = False
        validation_details = {}

        if result['success']:
            if agent_type == 'nop':
                # Find post-test.txt and parse with test_result_parser
                validation_details = await self._validate_nop_result(working_dir, task_id, cve_id)
                test_passed = validation_details.get('valid', False)
            elif agent_type == 'oracle':
                # Read results.json and check is_resolved
                validation_details = await self._validate_oracle_result(working_dir, cve_id)
                test_passed = validation_details.get('is_resolved', False)

        return {
            'success': result['success'] and test_passed,
            'agent_type': agent_type,
            'task_id': task_id,
            'test_passed': test_passed,
            'validation': validation_details,
            'output_path': str(output_path),
            'stdout': stdout,
            'stderr': stderr,
            'error': result.get('error')
        }

    async def _validate_nop_result(
        self,
        working_dir: Path,
        task_id: str,
        cve_id: str
    ) -> Dict[str, Any]:
        """
        Validate nop test result by parsing post-test.txt.

        Expected: test_func.py all PASSED, test_vuln.py all FAILED

        Args:
            working_dir: CVE working directory
            task_id: Lowercase task ID
            cve_id: CVE identifier for logging

        Returns:
            Validation result dictionary
        """
        from .test_result_parser import TestResultParser

        nop_dir = working_dir / 'nop'
        if not nop_dir.exists():
            return {'valid': False, 'error': 'nop output directory not found'}

        # Find the latest timestamp directory
        timestamp_dirs = sorted([d for d in nop_dir.iterdir() if d.is_dir()], reverse=True)
        if not timestamp_dirs:
            return {'valid': False, 'error': 'No timestamp directory found in nop/'}

        latest_dir = timestamp_dirs[0]

        # Find post-test.txt: nop/<timestamp>/<task_id>/<task_id>.xxx/panes/post-test.txt
        post_test_files = list(latest_dir.glob(f'{task_id}/{task_id}.*/panes/post-test.txt'))
        if not post_test_files:
            return {'valid': False, 'error': f'post-test.txt not found in {latest_dir}'}

        post_test_file = post_test_files[0]
        self.logger.debug(f"[{cve_id}] Parsing nop result from: {post_test_file}")

        try:
            content = post_test_file.read_text()
            parser = TestResultParser()
            parsed = parser.parse(content)
            validation = parser.validate_vulnerable_env(parsed)

            return {
                'valid': validation['valid'],
                'func_tests': validation['actual']['func_tests'],
                'vuln_tests': validation['actual']['vuln_tests'],
                'issues': validation['issues'],
                'post_test_file': str(post_test_file)
            }
        except Exception as e:
            self.logger.error(f"[{cve_id}] Failed to parse nop result: {e}")
            return {'valid': False, 'error': str(e)}

    async def _validate_oracle_result(
        self,
        working_dir: Path,
        cve_id: str
    ) -> Dict[str, Any]:
        """
        Validate oracle test result by reading results.json.

        Expected: ["results"][0]["is_resolved"] == true

        Args:
            working_dir: CVE working directory
            cve_id: CVE identifier for logging

        Returns:
            Validation result dictionary
        """
        oracle_dir = working_dir / 'oracle'
        if not oracle_dir.exists():
            return {'is_resolved': False, 'error': 'oracle output directory not found'}

        # Find the latest timestamp directory
        timestamp_dirs = sorted([d for d in oracle_dir.iterdir() if d.is_dir()], reverse=True)
        if not timestamp_dirs:
            return {'is_resolved': False, 'error': 'No timestamp directory found in oracle/'}

        latest_dir = timestamp_dirs[0]
        results_file = latest_dir / 'results.json'

        if not results_file.exists():
            return {'is_resolved': False, 'error': f'results.json not found in {latest_dir}'}

        self.logger.debug(f"[{cve_id}] Reading oracle result from: {results_file}")

        try:
            with open(results_file, 'r') as f:
                data = json.load(f)

            results = data.get('results', [])
            if not results:
                return {'is_resolved': False, 'error': 'No results in results.json'}

            is_resolved = results[0].get('is_resolved', False)
            parser_results = results[0].get('parser_results', {})

            return {
                'is_resolved': is_resolved,
                'parser_results': parser_results,
                'results_file': str(results_file)
            }
        except Exception as e:
            self.logger.error(f"[{cve_id}] Failed to parse oracle result: {e}")
            return {'is_resolved': False, 'error': str(e)}

    async def run_tb_nop_test(
        self,
        working_dir: Path,
        cve_id: str,
        timeout: int = 1200
    ) -> Dict[str, Any]:
        """
        Run tb nop test.

        Expected: test_func.py all PASSED, test_vuln.py all FAILED
        """
        return await self.run_tb_test(working_dir, cve_id, 'nop', timeout)

    async def run_tb_oracle_test(
        self,
        working_dir: Path,
        cve_id: str,
        timeout: int = 1200
    ) -> Dict[str, Any]:
        """
        Run tb oracle test.

        Expected: results.json["results"][0]["is_resolved"] == true
        """
        return await self.run_tb_test(working_dir, cve_id, 'oracle', timeout)


if __name__ == "__main__":
    # Test script executor
    import yaml

    logging.basicConfig(level=logging.INFO)

    with open('config.yaml', 'r') as f:
        config = yaml.safe_load(f)

    executor = ScriptExecutor(config)

    # Test commands
    async def test():
        result = await executor._run_command(
            ['docker', '--version'],
            cwd=Path.cwd(),
            timeout=10,
            cve_id="test"
        )
        print(f"Docker version check: {result}")

        # Test container count
        count = await executor.get_running_container_count()
        print(f"Running containers: {count}")

        # Test resource usage
        usage = await executor.get_docker_resource_usage()
        print(f"Resource usage: {usage}")

    asyncio.run(test())

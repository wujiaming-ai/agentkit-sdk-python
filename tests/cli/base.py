# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd. and/or its affiliates.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
AgentKit CLI Test Framework - Base Components

This module provides the foundational components for CLI testing,
separated from the main business logic.
"""

import sys
import os
import json
import time
import subprocess
from typing import Dict, List, Optional, Any, Callable
from datetime import datetime
from abc import ABC, abstractmethod


class CLITestResult:
    """Test result record class"""

    def __init__(
        self,
        name: str,
        success: bool,
        duration: float,
        command: str,
        output: str = "",
        error: str = "",
    ):
        self.name = name
        self.success = success
        self.duration = duration
        self.command = command
        self.output = output
        self.error = error
        self.timestamp = datetime.now()


class CLITestRunner:
    """CLI test runner"""

    def __init__(self, module_name: str):
        self.module_name = module_name
        self.results: List[CLITestResult] = []
        self.test_resources: Dict[str, List[str]] = {}

    def run_command(
        self,
        args: List[str],
        test_name: str = "",
        expected_success: bool = True,
        timeout: int = 30,
        capture_output: bool = True,
        verbose: bool = False,
    ) -> CLITestResult:
        """Run CLI command"""

        start_time = time.time()
        command_str = f"agentkit {' '.join(args)}"

        print(f"\n🧪 {test_name or 'Running: ' + ' '.join(args)}")
        print(f"Command: {command_str}")

        try:
            # Build the full command
            full_command = [
                "python",
                "-c",
                "from agentkit.toolkit.cli.cli import app; import sys; sys.argv = ['agentkit'] + sys.argv[1:]; app()",
            ] + args

            if verbose:
                print(f"Full command: {' '.join(full_command)}")

            # Execute the command
            if capture_output:
                completed = subprocess.run(
                    full_command, capture_output=True, text=True, timeout=timeout
                )
                output = completed.stdout + completed.stderr
                success = (completed.returncode == 0) == expected_success
                return_code = completed.returncode
            else:
                completed = subprocess.run(full_command, timeout=timeout)
                output = ""
                success = (completed.returncode == 0) == expected_success
                return_code = completed.returncode

            duration = time.time() - start_time

            if success:
                print(f"✅ Success ({duration:.2f}s)")
            else:
                print(f"❌ Failed ({duration:.2f}s, return code: {return_code})")

            if output and len(output) < 1000:
                print("Output preview:")
                print(output[:500] + ("..." if len(output) > 500 else ""))

        except subprocess.TimeoutExpired:
            duration = time.time() - start_time
            success = False
            output = ""
            return_code = -1
            print(f"⏰ Timeout ({duration:.2f}s)")

        except Exception as e:
            duration = time.time() - start_time
            success = False
            output = ""
            return_code = -1
            print(f"💥 Exception ({duration:.2f}s): {e}")

        test_result = CLITestResult(
            name=test_name or command_str,
            success=success,
            duration=duration,
            command=command_str,
            output=output[:1000],  # Limit output length
            error=f"Return code: {return_code}" if not success else "",
        )

        self.results.append(test_result)
        return test_result

    def add_test_resource(self, resource_type: str, resource_id: str):
        """Add test resource for later cleanup"""
        if resource_type not in self.test_resources:
            self.test_resources[resource_type] = []
        self.test_resources[resource_type].append(resource_id)

    def get_summary(self) -> Dict[str, Any]:
        """Get test summary"""
        total = len(self.results)
        passed = sum(1 for r in self.results if r.success)
        failed = total - passed
        total_duration = sum(r.duration for r in self.results)

        return {
            "module": self.module_name,
            "total_tests": total,
            "passed": passed,
            "failed": failed,
            "success_rate": passed / total * 100 if total > 0 else 0,
            "total_duration": total_duration,
            "test_resources": self.test_resources,
        }

    def print_summary(self):
        """Print test summary"""
        summary = self.get_summary()

        print(f"\n📊 {self.module_name} Module Test Summary")
        print("=" * 50)
        print(f"Total Tests: {summary['total_tests']}")
        print(f"Passed: {summary['passed']}")
        print(f"Failed: {summary['failed']}")
        print(f"Success Rate: {summary['success_rate']:.1f}%")
        print(f"Total Duration: {summary['total_duration']:.2f}s")

        if summary["failed"] > 0:
            print("\n❌ Failed Tests:")
            for result in self.results:
                if not result.success:
                    print(f"  - {result.name}: {result.error}")


class BaseModuleTester(ABC):
    """Base module test class"""

    def __init__(self, runner: CLITestRunner):
        self.runner = runner
        self.test_start_time = datetime.now()

    @abstractmethod
    def run_tests(self) -> bool:
        """Run all tests for the module"""
        pass

    @abstractmethod
    def cleanup_resources(self):
        """Clean up test resources"""
        pass


class CLITestHelper:
    """CLI test helper utility class"""

    TEST_RESOURCE_FILE = ".agentkit_cli_test_resources.json"

    def __init__(self):
        self.test_resources = self._load_test_resources()

    def _load_test_resources(self):
        """Load existing test resources from file"""
        if os.path.exists(self.TEST_RESOURCE_FILE):
            try:
                with open(self.TEST_RESOURCE_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "tools": {},
            "sessions": {},
            "memories": {},
            "knowledge": {},
            "runtimes": {},
        }

    def _save_test_resources(self):
        """Save test resources to file"""
        with open(self.TEST_RESOURCE_FILE, "w") as f:
            json.dump(self.test_resources, f, indent=2)

    def add_test_resource(self, resource_type: str, resource_id: str, metadata=None):
        """Add test resource for tracking"""
        if resource_type not in self.test_resources:
            self.test_resources[resource_type] = {}

        self.test_resources[resource_type][resource_id] = {
            "created_at": time.time(),
            "metadata": metadata or {},
        }
        self._save_test_resources()
        return resource_id

    def get_test_resources(self, resource_type: str):
        """Get all test resources of specific type"""
        return self.test_resources.get(resource_type, {})

    def cleanup_test_resources(self, resource_type: Optional[str] = None):
        """Clean up test resources"""
        if resource_type:
            resources = self.test_resources.get(resource_type, {})
            self.test_resources[resource_type] = {}
        else:
            resources = {}
            for rt in self.test_resources:
                resources.update(self.test_resources[rt])
            self.test_resources = {k: {} for k in self.test_resources}

        self._save_test_resources()
        return resources

    def generate_test_name(self, prefix: str = "test") -> str:
        """Generate unique test resource name"""
        import uuid

        return f"{prefix}_{uuid.uuid4().hex[:8]}"


def extract_resource_id(output: str, pattern: str = r"\w+-\w+") -> Optional[str]:
    """Extract resource ID from command output"""
    import re

    match = re.search(pattern, output)
    return match.group() if match else None


def validate_json_output(output: str) -> bool:
    """Validate output is valid JSON"""
    try:
        json.loads(output)
        return True
    except json.JSONDecodeError:
        return False


def validate_yaml_output(output: str) -> bool:
    """Validate output is valid YAML"""
    try:
        import yaml

        yaml.safe_load(output)
        return True
    except Exception:
        return False

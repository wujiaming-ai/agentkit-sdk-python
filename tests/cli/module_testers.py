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
AgentKit CLI Module Testers

Module-specific test implementations for each CLI module.
"""

import time
from typing import Dict, List, Optional, Any
from .base import BaseModuleTester, CLITestRunner
from .config import TEST_CONFIG


class ToolsModuleTester(BaseModuleTester):
    """Tools module tester"""

    def __init__(self, runner: CLITestRunner):
        super().__init__(runner)
        self.test_tool_name = f"{TEST_CONFIG['TOOL_NAME_PREFIX']}_{int(time.time())}"
        self.test_session_name = (
            f"{TEST_CONFIG['SESSION_NAME_PREFIX']}_{int(time.time())}"
        )
        self.created_tool_id = None

    def run_tests(self) -> bool:
        """Run Tools module tests"""
        print("\n🔧 Starting Tools Module Tests")

        # Test 1: List tools
        self.runner.run_command(["tools", "list", "--limit", "2"], "List tools")

        # Test 2: Different output formats
        self.runner.run_command(
            ["tools", "list", "--limit", "1", "--output", "json"], "JSON format output"
        )

        self.runner.run_command(
            ["tools", "list", "--limit", "1", "--output", "yaml"], "YAML format output"
        )

        self.runner.run_command(
            ["tools", "list", "--limit", "1", "--quiet"], "Quiet mode"
        )

        # Test 3: Error handling
        self.runner.run_command(
            ["tools", "show", "--tool-id", "t-invalid-tool-id"],
            "Invalid tool ID handling",
            expected_success=False,
        )

        # Test 4: Create tool (requires more parameters)
        self.runner.run_command(
            [
                "tools",
                "create",
                "--name",
                self.test_tool_name,
                "--tool-type",
                "All-in-one",
                "--description",
                TEST_CONFIG["TEST_DESCRIPTION"],
            ],
            "Create tool (expected failure - requires auth config)",
            expected_success=False,
        )

        # Test 5: Session related operations
        self.runner.run_command(
            ["tools", "session", "list", "--tool-id", "t-placeholder", "--limit", "1"],
            "Session list (expected failure - invalid tool ID)",
            expected_success=False,
        )

        return True

    def cleanup_resources(self):
        """Clean up Tools test resources"""
        print("\n🧹 Cleaning up Tools test resources")
        if self.created_tool_id:
            self.runner.run_command(
                ["tools", "delete", "--tool-id", self.created_tool_id],
                f"Delete test tool: {self.created_tool_id}",
                expected_success=False,  # May already not exist
            )


class MemoryModuleTester(BaseModuleTester):
    """Memory module tester"""

    def __init__(self, runner: CLITestRunner):
        super().__init__(runner)
        self.test_memory_name = (
            f"{TEST_CONFIG['MEMORY_NAME_PREFIX']}_{int(time.time())}"
        )

    def run_tests(self) -> bool:
        """Run Memory module tests"""
        print("\n🧠 Starting Memory Module Tests")

        # Test 1: List memory collections
        self.runner.run_command(
            ["memory", "list", "--limit", "2"], "List memory collections"
        )

        # Test 2: Different output formats
        self.runner.run_command(
            ["memory", "list", "--limit", "1", "--output", "json"], "JSON format output"
        )

        self.runner.run_command(
            ["memory", "list", "--limit", "1", "--quiet"], "Quiet mode"
        )

        # Test 3: Provider types
        self.runner.run_command(["memory", "provider-types"], "List provider types")

        # Test 4: Error handling
        self.runner.run_command(
            ["memory", "show", "--collection-id", "mem-invalid"],
            "Invalid memory collection ID",
            expected_success=False,
        )

        return True

    def cleanup_resources(self):
        """Clean up Memory test resources"""
        print("\n🧹 Cleaning up Memory test resources")
        # Add specific cleanup logic here


class KnowledgeModuleTester(BaseModuleTester):
    """Knowledge module tester"""

    def __init__(self, runner: CLITestRunner):
        super().__init__(runner)
        self.test_knowledge_name = (
            f"{TEST_CONFIG['KNOWLEDGE_NAME_PREFIX']}_{int(time.time())}"
        )

    def run_tests(self) -> bool:
        """Run Knowledge module tests"""
        print("\n📚 Starting Knowledge Module Tests")

        # Test 1: List knowledge bases
        self.runner.run_command(
            ["knowledge", "list", "--limit", "2"], "List knowledge bases"
        )

        # Test 2: Different output formats
        self.runner.run_command(
            ["knowledge", "list", "--limit", "1", "--output", "json"],
            "JSON format output",
        )

        self.runner.run_command(
            ["knowledge", "list", "--limit", "1", "--quiet"], "Quiet mode"
        )

        # Test 3: Provider types
        self.runner.run_command(["knowledge", "provider-types"], "List provider types")

        # Test 4: Error handling
        self.runner.run_command(
            ["knowledge", "show", "--knowledge-id", "kb-invalid"],
            "Invalid knowledge base ID",
            expected_success=False,
        )

        return True

    def cleanup_resources(self):
        """Clean up Knowledge test resources"""
        print("\n🧹 Cleaning up Knowledge test resources")


class RuntimeModuleTester(BaseModuleTester):
    """Runtime module tester"""

    def __init__(self, runner: CLITestRunner):
        super().__init__(runner)
        self.test_runtime_name = (
            f"{TEST_CONFIG['RUNTIME_NAME_PREFIX']}_{int(time.time())}"
        )

    def test_advanced_filters(self):
        """Test advanced filtering functionality - verify correct API structure"""
        print("\n🔍 Testing Runtime Advanced Filtering (API Structure Verification)")

        # Test status exact filtering - should use Name field
        self.runner.run_command(
            ["runtime", "list", "--filter-status", "Ready", "--limit", "1"],
            "Status exact filtering - Name=Status",
        )

        # Test status fuzzy filtering - should use NameContains field
        self.runner.run_command(
            ["runtime", "list", "--name-contains", "Customer", "--limit", "1"],
            "Name fuzzy filtering - NameContains=Name",
        )

        # Test ID fuzzy filtering - should use NameContains field
        self.runner.run_command(
            ["runtime", "list", "--filter-id-contains", "r-", "--limit", "1"],
            "ID fuzzy filtering - NameContains=Id",
        )

        # Test description fuzzy filtering - should use NameContains field
        self.runner.run_command(
            [
                "runtime",
                "list",
                "--filter-description-contains",
                "Customer",
                "--limit",
                "1",
            ],
            "Description fuzzy filtering - NameContains=Description",
        )

        # Test project filtering - use ProjectName parameter
        self.runner.run_command(
            ["runtime", "list", "--project-name", "default", "--limit", "1"],
            "Project filtering - ProjectName parameter",
        )

        # Test multi-status filtering - should use Name field + multiple Values
        self.runner.run_command(
            ["runtime", "list", "--filter-status-in", "Ready,Creating", "--limit", "1"],
            "Multi-status filtering - Name=Status+multiple Values",
        )

        # Test combined filtering - include both exact and fuzzy filtering
        self.runner.run_command(
            [
                "runtime",
                "list",
                "--name",
                "CustomerService",
                "--filter-status",
                "Ready",
                "--limit",
                "1",
            ],
            "Combined filtering - Name+Status exact filtering",
        )

        # Test invalid status value (expected failure)
        self.runner.run_command(
            ["runtime", "list", "--filter-status", "InvalidStatus"],
            "Invalid status filtering - should fail",
            expected_success=False,
        )

    def test_get_commands(self):
        """Test get runtime related commands"""
        print("\n📖 Testing Runtime Get Commands")

        # First get a valid runtime ID for subsequent tests
        result = self.runner.run_command(
            ["runtime", "list", "--limit", "1", "--quiet"], "Get valid runtime ID"
        )

        if result:
            # Extract runtime ID from output
            import subprocess

            try:
                output = subprocess.check_output(
                    ["agentkit", "runtime", "list", "--limit", "1", "--quiet"],
                    text=True,
                ).strip()
                if output and not output.startswith("❌"):
                    valid_runtime_id = output.split("\n")[0]

                    # Test get runtime details
                    self.runner.run_command(
                        ["runtime", "get", "-r", valid_runtime_id],
                        "Get runtime details",
                    )

                    # Test get runtime version
                    self.runner.run_command(
                        ["runtime", "version", "-r", valid_runtime_id],
                        "Get runtime version",
                    )

                    # Test list versions
                    self.runner.run_command(
                        ["runtime", "versions", "-r", valid_runtime_id], "List versions"
                    )
            except Exception as e:
                print(f"Error in test_get_commands: {e}")
                pass

    def test_create_runtime(self):
        """Test create runtime (expected failure - requires complete configuration)"""
        print("\n➕ Testing Runtime Create Functionality")

        # Test create runtime - expected failure because complete configuration is needed
        self.runner.run_command(
            [
                "runtime",
                "create",
                "--name",
                f"{self.test_runtime_name}",
                "--role-name",
                "test-role",
                "--artifact-type",
                "DockerImage",
                "--artifact-url",
                "test-image:latest",
            ],
            "Create runtime (expected failure - requires complete configuration)",
            expected_success=False,
        )

        # Test JSON format creation
        self.runner.run_command(
            [
                "runtime",
                "create",
                "--json",
                '{"Name": "test", "RoleName": "test-role"}',
            ],
            "JSON format creation (expected failure)",
            expected_success=False,
        )

    def test_update_runtime(self):
        """Test update runtime"""
        print("\n✏️ Testing Runtime Update Functionality")

        # Test update invalid runtime
        self.runner.run_command(
            [
                "runtime",
                "update",
                "-r",
                "rt-invalid-update-test",
                "--description",
                "Updated description",
            ],
            "Update invalid runtime",
            expected_success=False,
        )

        # Test JSON format update
        self.runner.run_command(
            [
                "runtime",
                "update",
                "-r",
                "rt-invalid-update-test",
                "--json",
                '{"Description": "Updated via JSON"}',
            ],
            "JSON format update invalid runtime",
            expected_success=False,
        )

    def test_delete_runtime(self):
        """Test delete runtime"""
        print("\n🗑️ Testing Runtime Delete Functionality")

        # Test delete invalid runtime
        self.runner.run_command(
            ["runtime", "delete", "-r", "rt-invalid-delete-test", "--force"],
            "Delete invalid runtime",
            expected_success=False,
        )

        # Test delete help information
        self.runner.run_command(["runtime", "delete", "--help"], "Delete command help")

    def test_release_runtime(self):
        """Test release runtime version"""
        print("\n🚀 Testing Runtime Release Functionality")

        # Test release invalid runtime
        self.runner.run_command(
            ["runtime", "release", "-r", "rt-invalid-release-test"],
            "Release invalid runtime",
            expected_success=False,
        )

        # Test release with specific version number
        self.runner.run_command(
            [
                "runtime",
                "release",
                "-r",
                "rt-invalid-release-test",
                "--version-number",
                "1",
            ],
            "Release specific version",
            expected_success=False,
        )

    def run_tests(self) -> bool:
        """Run Runtime module tests"""
        print("\n⚙️ Starting Runtime Module Tests")

        # Test 1: Basic listing functionality
        self.runner.run_command(
            ["runtime", "list", "--limit", "2"], "Basic listing functionality"
        )

        # Test 2: Different output formats
        self.runner.run_command(
            ["runtime", "list", "--limit", "1", "--output", "json"],
            "JSON format output",
        )

        self.runner.run_command(
            ["runtime", "list", "--limit", "1", "--quiet"], "Quiet mode"
        )

        # Test 3: Advanced filtering functionality
        self.test_advanced_filters()

        # Test 4: Get commands test
        self.test_get_commands()

        # Test 5: Create runtime test
        self.test_create_runtime()

        # Test 6: Update runtime test
        self.test_update_runtime()

        # Test 7: Delete runtime test
        self.test_delete_runtime()

        # Test 8: Release runtime test
        self.test_release_runtime()

        # Test 9: Error handling (using short option)
        self.runner.run_command(
            ["runtime", "get", "-r", "rt-invalid"],
            "Invalid runtime ID (short option)",
            expected_success=False,
        )

        return True

    def cleanup_resources(self):
        """Clean up Runtime test resources"""
        print("\n🧹 Cleaning up Runtime test resources")


def get_module_tester(module_name: str, runner: CLITestRunner) -> BaseModuleTester:
    """Get module tester"""
    testers = {
        "tools": ToolsModuleTester,
        "memory": MemoryModuleTester,
        "knowledge": KnowledgeModuleTester,
        "runtime": RuntimeModuleTester,
    }

    if module_name not in testers:
        raise ValueError(
            f"Unsupported module: {module_name}. Supported modules: {list(testers.keys())}"
        )

    return testers[module_name](runner)

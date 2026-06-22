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
AgentKit CLI Test Configuration

Configuration constants and settings for CLI testing.
"""

import os

# Test configuration
TEST_CONFIG = {
    # Default timeouts
    "DEFAULT_TIMEOUT": 30,  # seconds
    "FAST_TIMEOUT": 15,  # seconds for fast mode
    # Wait times
    "DEFAULT_WAIT_TIME": 2,  # seconds between operations
    "FAST_WAIT_TIME": 0.5,  # seconds for fast mode
    # Retry settings
    "MAX_RETRIES": 3,
    "RETRY_DELAY": 1,  # seconds
    # Resource name prefixes
    "TOOL_NAME_PREFIX": "test_tool",
    "MEMORY_NAME_PREFIX": "test_memory",
    "KNOWLEDGE_NAME_PREFIX": "test_knowledge",
    "RUNTIME_NAME_PREFIX": "test_runtime",
    "SESSION_NAME_PREFIX": "test_session",
    # Test descriptions
    "TEST_DESCRIPTION": "CLI test resource created by automated test suite",
    "TEST_PROJECT": "cli_test_project",
    # Resource file
    "TEST_RESOURCE_FILE": ".agentkit_cli_test_resources.json",
    # Python command
    "PYTHON_CMD": os.environ.get("PYTHON_CMD", "python"),
    # Verbose mode
    "VERBOSE": os.environ.get("CLI_TEST_VERBOSE", "false").lower() == "true",
    # Fast mode
    "FAST_MODE": os.environ.get("CLI_TEST_FAST", "false").lower() == "true",
}

# Supported modules
SUPPORTED_MODULES = ["tools", "memory", "knowledge", "runtime"]

# Output formats
OUTPUT_FORMATS = ["summary", "detailed", "json"]

# Test patterns for resource ID extraction
RESOURCE_ID_PATTERNS = {
    "tools": r"t-[a-z0-9]+",
    "memory": r"mem-[a-z0-9]+",
    "knowledge": r"kb-[a-z0-9]+",
    "runtime": r"rt-[a-z0-9]+",
    "session": r"s-[a-z0-9]+",
}

# Error patterns to expect
EXPECTED_ERROR_PATTERNS = {
    "InvalidResource": "The specified resource does not exist",
    "MissingParameter": "The required parameter .* is not supplied",
    "AuthenticationFailed": "Invalid credentials",
    "AuthorizationFailed": "Access denied",
    "InvalidParameter": "Invalid parameter",
}

# Success indicators
SUCCESS_INDICATORS = ["✅", "success", "created", "updated", "deleted", "completed"]

# Failure indicators
FAILURE_INDICATORS = ["❌", "error", "failed", "exception", "timeout"]

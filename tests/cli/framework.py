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
AgentKit CLI Test Framework

Main framework for running CLI tests across different modules.
"""

import sys
import os
import json
import time
import argparse
from datetime import datetime
from typing import Dict, List, Optional, Any

# Import test modules
from .base import CLITestRunner, BaseModuleTester
from .module_testers import get_module_tester
from .config import TEST_CONFIG, SUPPORTED_MODULES, OUTPUT_FORMATS


def main():
    """Main function"""
    parser = argparse.ArgumentParser(
        description="AgentKit CLI Test Framework",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Test single module
    python -m tests.cli.framework --module tools
    
    # Test all modules
    python -m tests.cli.framework --all-modules
    
    # Fast test mode
    python -m tests.cli.framework --module tools --fast
    
    # Generate JSON report
    python -m tests.cli.framework --all-modules --output json
    
    # Cleanup only
    python -m tests.cli.framework --cleanup --module memory
        """,
    )

    parser.add_argument(
        "--module", "-m", choices=SUPPORTED_MODULES, help="Module to test"
    )
    parser.add_argument(
        "--all-modules", "-a", action="store_true", help="Test all modules"
    )
    parser.add_argument("--cleanup", "-c", action="store_true", help="Cleanup only")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument(
        "--fast", action="store_true", help="Fast mode (reduced wait times)"
    )
    parser.add_argument(
        "--output",
        "-o",
        default="summary",
        choices=OUTPUT_FORMATS,
        help="Output format",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=TEST_CONFIG["DEFAULT_TIMEOUT"],
        help="Command timeout in seconds",
    )

    args = parser.parse_args()

    if not args.module and not args.all_modules:
        print("❌ Please specify --module or --all-modules")
        return 1

    if args.all_modules:
        modules = SUPPORTED_MODULES
    else:
        modules = [args.module]

    all_results = {}
    start_time = datetime.now()

    try:
        print("🚀 AgentKit CLI Test Framework Starting")
        print(f"Start Time: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Test Modules: {', '.join(modules)}")
        if args.fast:
            print("⚡ Fast mode enabled")
        print("=" * 60)

        for module_name in modules:
            print(f"\n🔍 Starting Test Module: {module_name.upper()}")
            print("-" * 40)

            # Create test runner
            runner = CLITestRunner(module_name)

            # Get module tester
            tester = get_module_tester(module_name, runner)

            if args.cleanup:
                # Cleanup only
                print(f"🧹 Cleaning up {module_name} module test resources...")
                tester.cleanup_resources()
                continue

            # Run tests
            print(f"🧪 Running {module_name} module tests...")
            tester.run_tests()

            # Cleanup resources
            print(f"🧹 Cleaning up {module_name} module test resources...")
            tester.cleanup_resources()

            # Get results
            all_results[module_name] = runner.get_summary()

            # Print summary
            runner.print_summary()

        if not args.cleanup:
            # Print overall summary
            print(f"\n{'=' * 60}")
            print("📊 Overall Test Summary")
            print(f"{'=' * 60}")

            total_tests = sum(r["total_tests"] for r in all_results.values())
            total_passed = sum(r["passed"] for r in all_results.values())
            total_failed = sum(r["failed"] for r in all_results.values())
            total_duration = sum(r["total_duration"] for r in all_results.values())

            end_time = datetime.now()
            total_time = (end_time - start_time).total_seconds()

            print(f"Test Modules: {len(modules)}")
            print(f"Total Tests: {total_tests}")
            print(f"Total Passed: {total_passed}")
            print(f"Total Failed: {total_failed}")
            print(f"Test Duration: {total_duration:.2f}s")
            print(f"Total Duration: {total_time:.2f}s")
            print(
                f"Overall Success Rate: {total_passed / total_tests * 100:.1f}%"
                if total_tests > 0
                else "N/A"
            )

            # Module detailed results
            print("\n📋 Module Results:")
            for module_name, result in all_results.items():
                status = "✅" if result["failed"] == 0 else "❌"
                print(
                    f"{status} {module_name}: {result['passed']}/{result['total_tests']} ({result['success_rate']:.1f}%)"
                )

            # Save JSON report
            if args.output == "json":
                report = {
                    "timestamp": start_time.isoformat(),
                    "end_time": end_time.isoformat(),
                    "duration_seconds": total_time,
                    "modules": all_results,
                    "summary": {
                        "total_modules": len(modules),
                        "total_tests": total_tests,
                        "passed": total_passed,
                        "failed": total_failed,
                        "success_rate": total_passed / total_tests * 100
                        if total_tests > 0
                        else 0,
                        "total_duration": total_duration,
                    },
                }

                report_file = f"test_report_{start_time.strftime('%Y%m%d_%H%M%S')}.json"
                with open(report_file, "w", encoding="utf-8") as f:
                    json.dump(report, f, ensure_ascii=False, indent=2)

                print(f"\n📄 Detailed report saved to: {report_file}")

            # Return appropriate exit code
            return 0 if total_failed == 0 else 1

    except KeyboardInterrupt:
        print("\n⚠️ Tests interrupted by user")
        return 130
    except Exception as e:
        print(f"\n💥 Test framework error: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)

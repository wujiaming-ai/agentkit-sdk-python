<div align="center">
  <h1>
    Agentkit Platform Python SDK and Starter Toolkit
  </h1>

  <h2>
    Launch your local agent on Volcengine AgentKit Platform as a fully managed service.
  </h2>

  <div align="center">
    <a href="https://github.com/volcengine/agentkit-sdk-python/graphs/commit-activity"><img alt="GitHub commit activity" src="https://img.shields.io/github/commit-activity/m/volcengine/agentkit-sdk-python"/></a>
    <a href="https://github.com/volcengine/agentkit-sdk-python/pulls"><img alt="GitHub open pull requests" src="https://img.shields.io/github/issues-pr/volcengine/agentkit-sdk-python"/></a>
    <a href="https://github.com/volcengine/agentkit-sdk-python/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/github/license/volcengine/agentkit-sdk-python"/></a>
    <a href="https://pypi.org/project/agentkit-sdk-python"><img alt="PyPI version" src="https://img.shields.io/pypi/v/agentkit-sdk-python"/></a>
    <a href="https://python.org"><img alt="Python versions" src="https://img.shields.io/pypi/pyversions/agentkit-sdk-python"/></a>
  </div>

  <p>
  <a href="https://console.volcengine.com/agentkit/"> Volcengine AgentKit</a>
    ◆ <a href="https://volcengine.github.io/agentkit-sdk-python/">Documentation</a>
    ◆ <a href="https://github.com/volcengine/agentkit-samples/tree/main">Samples</a>
    ◆ <a href="https://pypi.org/project/agentkit-sdk-python/">PyPI Package</a>
    ◆ <a href="https://github.com/volcengine/agentkit-sdk-python">GitHub Repository</a>

  </p>
</div>

## Overview

AgentKit is a developer platform by Volcengine that supports the building, deployment, and operation of AI Agents. It lowers the entry barrier for developers and enterprises by providing essential infrastructure beyond the model—including security, built-in tools, memory, knowledge, monitoring, and evaluation. This empowers enterprises to efficiently build, deploy, and operate complex, intelligent, enterprise-grade Agents. The platform also includes a Python SDK and a Starter Toolkit to help developers build, deploy, publish, and manage Agent applications through an SDK and CLI.

AgentKit includes the following modular Services that you can use together or independently:

## AgentKit Runtime

AgentKit Runtime is a fully managed service that provides a secure, isolated environment for running AI Agents. It supports the deployment of Agents built with any framework and language, and provides a set of APIs for interacting with the Agents.

**[Runtime Quick Start](https://volcengine.github.io/agentkit-sdk-python/content/4.runtime/1.runtime_quickstart.html)**

## AgentKit Tools

AgentKit Tools is a service that provides a set of built-in tools for AI Agents. It supports the execution of common tasks, such as data retrieval, web search, and code execution, in a secure and scalable manner.

**[Tools Quick Start](https://volcengine.github.io/agentkit-sdk-python/content/5.tools/1.sandbox_quickstart.html)**

## AgentKit Memory

AgentKit Memory is a service that provides a persistent storage solution for AI Agents. It supports the storage of Agent states, memories, and other data in a secure and scalable manner.

**[Memory Quick Start](https://volcengine.github.io/agentkit-sdk-python/content/6.memory/1.memory_quickstart.html)**

## AgentKit Knowledge

AgentKit Knowledge is a service that provides a knowledge base solution for AI Agents. It supports the storage of Agent knowledge, facts, and other data in a secure and scalable manner.

**[Knowledge Quick Start](https://volcengine.github.io/agentkit-sdk-python/content/7.knowledge/1.knowledge_quickstart.html)**

## AgentKit MCP

AgentKit MCP is a service that provides a set of tools for managing AI Agents. It supports the deployment, configuration, and monitoring of Agents in a secure and scalable manner.

**[MCP Quick Start](https://volcengine.github.io/agentkit-sdk-python/content/8.mcp/2.mcp_quickstart.html)**

## Installation

### Stable Release (Recommended)

Install the latest stable version:

```bash
pip install agentkit-sdk-python
```

### Development/Pre-release Version

For testing new features or bug fixes before they're officially released:

```bash
# Install the latest pre-release version
pip install --pre agentkit-sdk-python

# Or install a specific development version
pip install agentkit-sdk-python==1.0.0.dev1
```

**Note**: Development versions may contain bugs and are not recommended for production use.

## Release Process

Stable releases are published from GitHub tags. The source tree keeps the
release version in `pyproject.toml` and `agentkit/version.py`, and the GitHub
workflow verifies that the pushed tag matches those files before building and
publishing the package.

The release workflow must already exist on `main` before you push the release
tag. For `0.7.5`, merge the tag-release automation change first, then create
and push `v0.7.5`.

Use the following workflow for a stable release:

```bash
git checkout main
git pull github main
git tag v0.7.5
git push github v0.7.5
```

When the tag reaches GitHub, `.github/workflows/release-pypi.yml` will:

- verify the tag is reachable from `main`
- verify that `pyproject.toml` and `agentkit/version.py` match the tag version
- build the wheel and source distribution from the tagged commit
- verify the built artifacts contain the expected version metadata
- publish the release to PyPI using the GitHub Actions secret `PYPI_API_TOKEN`

The internal `local_build.py` helper is a separate local-only workflow and is
not used by the GitHub tag release.

Before using the GitHub tag release workflow, configure a project-scoped PyPI
API token in the repository's GitHub Actions secrets as `PYPI_API_TOKEN`.

## Security and privacy

This project takes security seriously.
For vulnerability reporting and supported versions, see [SECURITY.md](SECURITY.md).

## License

This project is licensed under the [Apache 2.0 License](./LICENSE).

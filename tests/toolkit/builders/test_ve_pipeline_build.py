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

"""Behavior tests for the VeCPCRBuilder.build() 6-step orchestration spine.

The build() method (agentkit/toolkit/builders/ve_pipeline.py) wires together six
private steps: _render_dockerfile, _create_project_archive, _upload_to_tos,
_prepare_cr_resources, _prepare_pipeline_resources and _execute_build.  These
tests replace each step on the *instance* with a hand-rolled fake so the real
orchestration branch logic runs against controlled inputs -- asserting on the
returned BuildResult shape, error codes, ImageInfo assembly, the accumulated
`resources` metadata, and exactly which steps ran (and with which args).
"""

from __future__ import annotations

import types
from datetime import datetime

import pytest

from agentkit.toolkit.builders.ve_pipeline import VeCPCRBuilder, VeCPCRBuilderConfig
from agentkit.toolkit.config import CommonConfig
from agentkit.toolkit.errors import ErrorCode
from agentkit.toolkit.models import BuildResult, ImageInfo
from agentkit.toolkit.reporter import Reporter


# --------------------------------------------------------------------------- #
# Fakes                                                                        #
# --------------------------------------------------------------------------- #


class _SpyReporter(Reporter):
    """Records every reporter call so tests can assert on progress narration."""

    def __init__(self) -> None:
        self.infos: list[str] = []
        self.successes: list[str] = []
        self.warnings: list[str] = []
        self.errors: list[str] = []

    def info(self, message: str, **kwargs):
        self.infos.append(message)

    def success(self, message: str, **kwargs):
        self.successes.append(message)

    def warning(self, message: str, **kwargs):
        self.warnings.append(message)

    def error(self, message: str, **kwargs):
        self.errors.append(message)

    def progress(self, message: str, current: int, total: int = 100, **kwargs):
        pass

    def confirm(self, message: str, default: bool = False, **kwargs) -> bool:
        return default

    def long_task(self, description: str, total: float = 100):
        from contextlib import contextmanager

        @contextmanager
        def _cm():
            class _T:
                def update(self, description=None, completed=None):
                    pass

            yield _T()

        return _cm()

    def show_logs(self, title: str, lines, max_lines: int = 100):
        pass


class _StepRecorder:
    """Shared mutable record of which build steps were invoked, with args.

    Reset between tests by the autouse fixture so no state leaks across cases.
    """

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def record(self, name: str, *args, **kwargs) -> None:
        self.calls.append((name, args, kwargs))

    @property
    def names(self) -> list[str]:
        return [c[0] for c in self.calls]


@pytest.fixture()
def recorder() -> _StepRecorder:
    return _StepRecorder()


def _make_config(agent_name: str = "myagent") -> VeCPCRBuilderConfig:
    """Build a config whose real _validate_config() passes (all required truthy)."""
    common = CommonConfig(
        agent_name=agent_name,
        entry_point="agent.py",
        cloud_provider="volcengine",
    )
    return VeCPCRBuilderConfig(
        common_config=common,
        tos_bucket="my-bucket",
        tos_region="cn-beijing",
        cr_instance_name="my-instance",
        cr_namespace_name="my-namespace",
        cr_repo_name="my-repo",
        cr_region="cn-beijing",
        image_tag="v1",
    )


def _install_happy_steps(
    builder: VeCPCRBuilder,
    recorder: _StepRecorder,
    *,
    image_url: str = "registry.example.com/ns/repo:v1",
    set_build_resources: bool = True,
) -> None:
    """Patch all six private steps on the instance with success fakes."""

    def fake_render(self, config, docker_build_config=None):
        recorder.record("_render_dockerfile", config, docker_build_config)
        return "/tmp/Dockerfile"

    def fake_archive(self, config):
        recorder.record("_create_project_archive", config)
        return "/tmp/archive.tar.gz"

    def fake_upload(self, archive_path, config):
        recorder.record("_upload_to_tos", archive_path, config)
        # build() reads config.tos_object_key/tos_bucket right after; the real
        # step sets tos_object_key, so mimic that side effect.
        config.tos_object_key = "agentkit-builds/archive.tar.gz"
        return "tos://my-bucket/agentkit-builds/archive.tar.gz", "cn-beijing-actual"

    def fake_prepare_cr(self, config):
        recorder.record("_prepare_cr_resources", config)
        from agentkit.toolkit.volcengine.services import CRServiceConfig

        cr_config = CRServiceConfig(
            instance_name=config.cr_instance_name,
            namespace_name=config.cr_namespace_name,
            repo_name=config.cr_repo_name,
            region=config.cr_region,
        )
        return cr_config, "cr-region-actual"

    def fake_prepare_pipeline(self, config, tos_url, cr_config):
        recorder.record("_prepare_pipeline_resources", config, tos_url, cr_config)
        if set_build_resources:
            # Mirror the real step recording pipeline metadata on the instance,
            # which build() then lifts into resources (lines ~262-266).
            self._build_resources = {
                "pipeline_name": "agentkit-cli-myagent-abcd",
                "pipeline_id": "pl-from-resources",
            }
        return "pl-returned"

    def fake_execute(self, pipeline_id, config, runtime_overrides=None):
        recorder.record(
            "_execute_build", pipeline_id, config, runtime_overrides
        )
        return image_url

    builder._render_dockerfile = types.MethodType(fake_render, builder)
    builder._create_project_archive = types.MethodType(fake_archive, builder)
    builder._upload_to_tos = types.MethodType(fake_upload, builder)
    builder._prepare_cr_resources = types.MethodType(fake_prepare_cr, builder)
    builder._prepare_pipeline_resources = types.MethodType(
        fake_prepare_pipeline, builder
    )
    builder._execute_build = types.MethodType(fake_execute, builder)


# --------------------------------------------------------------------------- #
# Happy path                                                                   #
# --------------------------------------------------------------------------- #


def test_build_happy_path_returns_success_with_assembled_image_info(recorder):
    builder = VeCPCRBuilder(reporter=_SpyReporter())
    config = _make_config(agent_name="myagent")
    _install_happy_steps(
        builder, recorder, image_url="registry.example.com/ns/repo:v1"
    )

    result = builder.build(config)

    assert isinstance(result, BuildResult)
    assert result.success is True
    assert result.error is None
    assert result.error_code is None
    # ImageInfo is split on the LAST ':' -> repository / tag.
    assert isinstance(result.image, ImageInfo)
    assert result.image.repository == "registry.example.com/ns/repo"
    assert result.image.tag == "v1"
    assert result.image.digest is None
    assert result.image.full_name == "registry.example.com/ns/repo:v1"
    assert isinstance(result.build_timestamp, datetime)


def test_build_happy_path_invokes_all_six_steps_in_order(recorder):
    builder = VeCPCRBuilder(reporter=_SpyReporter())
    config = _make_config()
    _install_happy_steps(builder, recorder)

    builder.build(config)

    assert recorder.names == [
        "_render_dockerfile",
        "_create_project_archive",
        "_upload_to_tos",
        "_prepare_cr_resources",
        "_prepare_pipeline_resources",
        "_execute_build",
    ]


def test_build_happy_path_threads_step_outputs_into_downstream_calls(recorder):
    builder = VeCPCRBuilder(reporter=_SpyReporter())
    config = _make_config()
    _install_happy_steps(builder, recorder)

    builder.build(config)

    by_name = {c[0]: c for c in recorder.calls}

    # _upload_to_tos gets the archive path produced by _create_project_archive.
    upload_args = by_name["_upload_to_tos"][1]
    assert upload_args[0] == "/tmp/archive.tar.gz"

    # _prepare_pipeline_resources receives the tos_url from _upload_to_tos and
    # the cr_config from _prepare_cr_resources.
    pipeline_args = by_name["_prepare_pipeline_resources"][1]
    assert pipeline_args[1] == "tos://my-bucket/agentkit-builds/archive.tar.gz"
    assert pipeline_args[2].instance_name == "my-instance"

    # _execute_build is called with the pipeline id returned by the resource
    # prep step (overwritten from _build_resources -> "pl-from-resources"),
    # and runtime_overrides carrying the *actual* resolved regions.
    exec_call = by_name["_execute_build"]
    assert exec_call[1][0] == "pl-from-resources"
    runtime_overrides = exec_call[1][2]
    assert runtime_overrides == {
        "tos_region": "cn-beijing-actual",
        "cr_region": "cr-region-actual",
    }


def test_build_happy_path_metadata_maps_all_resources(recorder):
    builder = VeCPCRBuilder(reporter=_SpyReporter())
    config = _make_config()
    _install_happy_steps(
        builder, recorder, image_url="registry.example.com/ns/repo:v1"
    )

    result = builder.build(config)

    md = result.metadata
    assert md["cr_image_url"] == "registry.example.com/ns/repo:v1"
    # pipeline id/name are captured from self._build_resources.
    assert md["cp_pipeline_id"] == "pl-from-resources"
    assert md["cp_pipeline_name"] == "agentkit-cli-myagent-abcd"
    assert md["cr_instance_name"] == "my-instance"
    assert md["cr_namespace_name"] == "my-namespace"
    assert md["cr_repo_name"] == "my-repo"
    assert md["tos_object_url"] == "tos://my-bucket/agentkit-builds/archive.tar.gz"
    assert md["tos_object_key"] == "agentkit-builds/archive.tar.gz"
    assert md["tos_bucket"] == "my-bucket"

    # The nested resources dict accumulates every step output.
    res = md["resources"]
    assert res["dockerfile_path"] == "/tmp/Dockerfile"
    assert res["archive_path"] == "/tmp/archive.tar.gz"
    assert res["tos_url"] == "tos://my-bucket/agentkit-builds/archive.tar.gz"
    assert res["tos_actual_region"] == "cn-beijing-actual"
    assert res["cr_actual_region"] == "cr-region-actual"
    assert res["image_url"] == "registry.example.com/ns/repo:v1"


def test_build_happy_path_persists_results_back_onto_config(recorder):
    builder = VeCPCRBuilder(reporter=_SpyReporter())
    config = _make_config()
    _install_happy_steps(
        builder, recorder, image_url="registry.example.com/ns/repo:v1"
    )

    builder.build(config)

    # build() writes the final results onto the mutable config for persistence.
    assert config.image_url == "registry.example.com/ns/repo:v1"
    assert config.cp_pipeline_id == "pl-from-resources"
    assert config.tos_object_key == "agentkit-builds/archive.tar.gz"
    assert config.build_timestamp  # ISO string was set


def test_build_image_url_without_tag_falls_back_to_config_image_tag(recorder):
    builder = VeCPCRBuilder(reporter=_SpyReporter())
    config = _make_config()
    config.image_tag = "fallbacktag"
    # _execute_build returns a URL that has no ':' tag segment.
    _install_happy_steps(builder, recorder, image_url="registry.example.com/ns/repo")

    result = builder.build(config)

    assert result.success is True
    assert result.image.repository == "registry.example.com/ns/repo"
    assert result.image.tag == "fallbacktag"


def test_build_without_build_resources_uses_returned_pipeline_id(recorder):
    """When the pipeline step does not populate self._build_resources, build()
    keeps the returned pipeline id and reports pipeline_name as None."""
    builder = VeCPCRBuilder(reporter=_SpyReporter())
    config = _make_config()
    _install_happy_steps(builder, recorder, set_build_resources=False)

    result = builder.build(config)

    assert result.success is True
    assert result.metadata["cp_pipeline_id"] == "pl-returned"
    assert result.metadata["cp_pipeline_name"] is None
    assert config.cp_pipeline_id == "pl-returned"


# --------------------------------------------------------------------------- #
# Validation failure (early return, no steps run)                             #
# --------------------------------------------------------------------------- #


def test_build_validation_failure_returns_config_invalid_and_runs_no_steps(recorder):
    builder = VeCPCRBuilder(reporter=_SpyReporter())
    config = _make_config()
    _install_happy_steps(builder, recorder)

    # Force validation to fail deterministically.
    builder._validate_config = types.MethodType(
        lambda self, cfg: False, builder
    )

    result = builder.build(config)

    assert result.success is False
    assert result.error_code == ErrorCode.CONFIG_INVALID
    assert result.error == "Configuration validation failed"
    assert result.image is None
    # NONE of the six steps executed.
    assert recorder.names == []


def test_build_real_validation_fails_when_tos_bucket_missing(recorder):
    """Exercise the real _validate_config branch: empty tos_bucket -> CONFIG_INVALID."""
    builder = VeCPCRBuilder(reporter=_SpyReporter())
    config = _make_config()
    config.tos_bucket = ""  # trips the first guard in real _validate_config
    _install_happy_steps(builder, recorder)

    result = builder.build(config)

    assert result.success is False
    assert result.error_code == ErrorCode.CONFIG_INVALID
    assert recorder.names == []


# --------------------------------------------------------------------------- #
# Mid-pipeline failure (BUILD_FAILED, partial resources preserved)            #
# --------------------------------------------------------------------------- #


def test_build_upload_failure_returns_build_failed_and_preserves_partial_resources(
    recorder,
):
    builder = VeCPCRBuilder(reporter=_SpyReporter())
    config = _make_config()
    _install_happy_steps(builder, recorder)

    # Replace _upload_to_tos with a raising fake AFTER the happy install so the
    # first two steps still succeed and their outputs are in `resources`.
    def boom_upload(self, archive_path, config):
        recorder.record("_upload_to_tos", archive_path, config)
        raise RuntimeError("network exploded")

    builder._upload_to_tos = types.MethodType(boom_upload, builder)

    result = builder.build(config)

    assert result.success is False
    assert result.error_code == ErrorCode.BUILD_FAILED
    assert "network exploded" in result.error
    assert isinstance(result.build_timestamp, datetime)

    # Steps before the failure ran; steps after did not.
    assert recorder.names == [
        "_render_dockerfile",
        "_create_project_archive",
        "_upload_to_tos",
    ]

    # Partial resources collected before the failure are preserved in metadata.
    res = result.metadata["resources"]
    assert res["dockerfile_path"] == "/tmp/Dockerfile"
    assert res["archive_path"] == "/tmp/archive.tar.gz"
    # tos_url was never set because _upload_to_tos raised before assignment.
    assert "tos_url" not in res
    assert "image_url" not in res


def test_build_pipeline_failure_preserves_tos_and_pipeline_state_on_config(recorder):
    """A failure in _execute_build (last step) still preserves the tos_object_key
    and pipeline_id collected earlier, both in metadata and on the config."""
    builder = VeCPCRBuilder(reporter=_SpyReporter())
    config = _make_config()
    _install_happy_steps(builder, recorder)

    def boom_execute(self, pipeline_id, config, runtime_overrides=None):
        recorder.record("_execute_build", pipeline_id, config, runtime_overrides)
        raise RuntimeError("pipeline blew up")

    builder._execute_build = types.MethodType(boom_execute, builder)

    result = builder.build(config)

    assert result.success is False
    assert result.error_code == ErrorCode.BUILD_FAILED
    assert "pipeline blew up" in result.error

    # All steps ran up to and including the failing _execute_build.
    assert recorder.names[-1] == "_execute_build"

    # tos_object_key and pipeline_id preserved for cleanup/debugging.
    assert config.tos_object_key == "agentkit-builds/archive.tar.gz"
    assert config.cp_pipeline_id == "pl-from-resources"
    assert result.metadata["tos_object_key"] == "agentkit-builds/archive.tar.gz"
    assert result.metadata["cp_pipeline_id"] == "pl-from-resources"
    # Full resources dict is threaded into the failure metadata too.
    assert result.metadata["resources"]["cr_actual_region"] == "cr-region-actual"


def test_build_first_step_failure_still_returns_build_failed(recorder):
    """A failure in the very first step (_render_dockerfile) yields BUILD_FAILED
    with an essentially empty resources map (nothing collected yet)."""
    builder = VeCPCRBuilder(reporter=_SpyReporter())
    config = _make_config()
    _install_happy_steps(builder, recorder)

    def boom_render(self, config, docker_build_config=None):
        recorder.record("_render_dockerfile", config, docker_build_config)
        raise RuntimeError("no docker deps")

    builder._render_dockerfile = types.MethodType(boom_render, builder)

    result = builder.build(config)

    assert result.success is False
    assert result.error_code == ErrorCode.BUILD_FAILED
    assert "no docker deps" in result.error
    assert recorder.names == ["_render_dockerfile"]
    # resources is empty -> no dockerfile_path recorded.
    assert result.metadata["resources"] == {}

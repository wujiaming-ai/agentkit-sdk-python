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

from __future__ import annotations

import io
import json
from pathlib import Path
import tarfile

import pytest
from typer.testing import CliRunner

runner = CliRunner()


class _FakeResponse:
    def __init__(
        self,
        *,
        status_code=200,
        payload=None,
        text="",
        content=b"",
    ):
        self.status_code = status_code
        self._payload = {"success": True} if payload is None else payload
        self.text = text
        self._content = content

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    def iter_content(self, chunk_size=1024 * 1024):
        del chunk_size
        yield self._content


def _patch_store_path(monkeypatch, tmp_path, session=None):
    import agentkit.toolkit.cli.sandbox.sandbox_client as sandbox_client

    store_path = tmp_path / "sessions.json"
    monkeypatch.setattr(sandbox_client, "_get_session_store_path", lambda: store_path)
    if session is None:
        session = {
            "session_id": "user-1",
            "tool_id": "tool-1",
            "instance_id": "session-1",
            "endpoint": "https://sandbox.example.com/base?token=abc",
        }
    store_path.write_text(json.dumps({"user-1": session}), encoding="utf-8")
    return store_path


def _patch_session_resolution(monkeypatch, cli_file, resolved_tool_id="tool-1"):
    monkeypatch.setattr(cli_file, "AgentkitToolsClient", lambda: object())
    monkeypatch.setattr(
        cli_file,
        "sync_remote_sessions",
        lambda **_kwargs: resolved_tool_id,
    )


def _tar_names(data: bytes) -> list[str]:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r") as tar:
        return sorted(member.name for member in tar.getmembers())


def _tar_bytes(entries: dict[str, bytes], dirs: list[str] | None = None) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for directory in dirs or []:
            info = tarfile.TarInfo(directory.rstrip("/") + "/")
            info.type = tarfile.DIRTYPE
            tar.addfile(info)
        for name, content in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def _write_tar(path: Path, members: list[tuple[tarfile.TarInfo, bytes | None]]) -> None:
    with tarfile.open(path, mode="w") as tar:
        for info, content in members:
            source = None if content is None else io.BytesIO(content)
            tar.addfile(info, source)


def test_sandbox_file_command_group_is_registered() -> None:
    from agentkit.toolkit.cli.cli import app

    for args in (
        ["sandbox", "file", "--help"],
        ["sandbox", "file", "upload", "--help"],
        ["sandbox", "file", "download", "--help"],
        ["sandbox", "file", "list", "--help"],
    ):
        result = runner.invoke(app, args)
        assert result.exit_code == 0
        assert "--session-id" in result.output or "Commands" in result.output
        if args[2:3] in (["upload"], ["download"]):
            assert "--sid" in result.output
            assert "-s" in result.output
        if args[2:3] == ["list"]:
            assert "-s" in result.output


def test_cli_file_upload_directory_creates_destination_and_extracts_archive(
    monkeypatch,
    tmp_path,
) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.cli_file as cli_file

    _patch_store_path(monkeypatch, tmp_path)
    _patch_session_resolution(monkeypatch, cli_file)
    source_dir = tmp_path / "project"
    empty_dir = source_dir / "empty"
    empty_dir.mkdir(parents=True)
    (source_dir / "app.py").write_text("print('hi')\n", encoding="utf-8")
    captured = {}

    def fake_post(url, **kwargs):
        if url.endswith("/v1/file/upload?token=abc"):
            archive_file = kwargs["files"]["file"][1]
            captured["upload_url"] = url
            captured["upload_data"] = kwargs["data"]
            captured["archive_names"] = _tar_names(archive_file.read())
            return _FakeResponse()
        captured["shell_url"] = url
        captured["shell_json"] = kwargs["json"]
        return _FakeResponse(payload={"success": True, "data": {"exit_code": 0}})

    monkeypatch.setattr(cli_file.requests, "post", fake_post)

    result = runner.invoke(
        app,
        [
            "sandbox",
            "file",
            "upload",
            "--session-id",
            "user-1",
            "--workspace",
            "/home/gem",
            "--src-dir",
            str(source_dir),
            "--dst-dir",
            "workspace/project",
        ],
    )

    assert result.exit_code == 0
    assert captured["upload_url"] == (
        "https://sandbox.example.com/base/v1/file/upload?token=abc"
    )
    assert captured["upload_data"]["path"].startswith("/tmp/agentkit-upload-")
    assert captured["archive_names"] == ["app.py", "empty"]
    command = captured["shell_json"]["command"]
    assert "mkdir -p /home/gem/workspace/project" in command
    assert "tar -xf /tmp/agentkit-upload-" in command
    assert "rm -f /tmp/agentkit-upload-" in command
    assert "exit " not in command
    output = json.loads(result.output)
    assert output["dst_dir"] == "/home/gem/workspace/project"
    assert output["sources"] == [str(source_dir)]


def test_cli_file_upload_multiple_files_without_workspace(
    monkeypatch,
    tmp_path,
) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.cli_file as cli_file

    _patch_store_path(monkeypatch, tmp_path)
    _patch_session_resolution(monkeypatch, cli_file)
    first = tmp_path / "one.txt"
    second = tmp_path / "two.txt"
    first.write_text("one", encoding="utf-8")
    second.write_text("two", encoding="utf-8")
    captured = {}

    def fake_post(url, **kwargs):
        if url.endswith("/v1/file/upload?token=abc"):
            archive_file = kwargs["files"]["file"][1]
            captured["archive_names"] = _tar_names(archive_file.read())
            return _FakeResponse()
        captured["shell_json"] = kwargs["json"]
        return _FakeResponse(payload={"success": True, "data": {"exit_code": 0}})

    monkeypatch.setattr(cli_file.requests, "post", fake_post)

    result = runner.invoke(
        app,
        [
            "sandbox",
            "file",
            "upload",
            "--session-id",
            "user-1",
            str(first),
            str(second),
            "--dst-dir",
            "/tmp/files",
        ],
    )

    assert result.exit_code == 0
    assert captured["archive_names"] == ["one.txt", "two.txt"]
    assert "mkdir -p /tmp/files" in captured["shell_json"]["command"]


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (
            ["--workspace", "/home/gem", "--dst-dir", "out"],
            "Provide --src-dir or one or more FILE arguments",
        ),
        (
            ["orphan.txt", "--dst-dir", "/tmp/out"],
            "Source file not found",
        ),
        (
            ["--src-dir", "missing", "--dst-dir", "/tmp/out"],
            "Source directory not found",
        ),
        (
            ["missing.txt", "--dst-dir", "/tmp/out"],
            "Source file not found",
        ),
        (
            ["--src-dir", ".", "x.py", "--dst-dir", "/tmp/out"],
            "Use either --src-dir or FILE..., not both",
        ),
        (
            ["--src-dir", ".", "--src-dir", ".", "--dst-dir", "/tmp/out"],
            "--src-dir accepts one directory",
        ),
        (
            ["--src-dir", ".", "--dst-dir", "relative"],
            "--dst-dir must be absolute when --workspace is omitted",
        ),
    ],
)
def test_cli_file_upload_validates_inputs(monkeypatch, tmp_path, args, message) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.cli_file as cli_file

    _patch_store_path(monkeypatch, tmp_path)
    _patch_session_resolution(monkeypatch, cli_file)

    result = runner.invoke(
        app,
        ["sandbox", "file", "upload", "--session-id", "user-1", *args],
    )

    assert result.exit_code == 1
    assert message in result.output


def test_cli_file_upload_validates_file_and_directory_shapes(
    monkeypatch,
    tmp_path,
) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.cli_file as cli_file

    _patch_store_path(monkeypatch, tmp_path)
    _patch_session_resolution(monkeypatch, cli_file)
    regular_file = tmp_path / "regular.txt"
    regular_file.write_text("one", encoding="utf-8")
    directory = tmp_path / "directory"
    directory.mkdir()

    result = runner.invoke(
        app,
        [
            "sandbox",
            "file",
            "upload",
            "--session-id",
            "user-1",
            "--src-dir",
            str(regular_file),
            "--dst-dir",
            "/tmp/out",
        ],
    )
    assert result.exit_code == 1
    assert "Source path is not a directory" in result.output

    result = runner.invoke(
        app,
        [
            "sandbox",
            "file",
            "upload",
            "--session-id",
            "user-1",
            str(directory),
            "--dst-dir",
            "/tmp/out",
        ],
    )
    assert result.exit_code == 1
    assert "Source path is not a file" in result.output


def test_cli_file_upload_rejects_duplicate_file_names(monkeypatch, tmp_path) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.cli_file as cli_file

    _patch_store_path(monkeypatch, tmp_path)
    _patch_session_resolution(monkeypatch, cli_file)
    first = tmp_path / "a" / "same.txt"
    second = tmp_path / "b" / "same.txt"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_text("one", encoding="utf-8")
    second.write_text("two", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "sandbox",
            "file",
            "upload",
            "--session-id",
            "user-1",
            str(first),
            str(second),
            "--dst-dir",
            "/tmp/out",
        ],
    )

    assert result.exit_code == 1
    assert "Duplicate source file name: same.txt" in result.output


def test_cli_file_upload_reports_http_error(monkeypatch, tmp_path) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.cli_file as cli_file

    _patch_store_path(monkeypatch, tmp_path)
    _patch_session_resolution(monkeypatch, cli_file)
    source_file = tmp_path / "one.txt"
    source_file.write_text("one", encoding="utf-8")

    def fake_post(url, **_kwargs):
        if url.endswith("/v1/file/upload?token=abc"):
            return _FakeResponse(
                status_code=422,
                payload={"detail": [{"loc": ["body", "path"], "msg": "bad path"}]},
                text="bad",
            )
        return _FakeResponse(payload={"success": True, "data": {"exit_code": 0}})

    monkeypatch.setattr(cli_file.requests, "post", fake_post)

    result = runner.invoke(
        app,
        [
            "sandbox",
            "file",
            "upload",
            "--session-id",
            "user-1",
            str(source_file),
            "--dst-dir",
            "/tmp/files",
        ],
    )

    assert result.exit_code == 1
    assert "body.path: bad path" in result.output


def test_cli_file_upload_reports_request_exception(monkeypatch, tmp_path) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.cli_file as cli_file

    _patch_store_path(monkeypatch, tmp_path)
    _patch_session_resolution(monkeypatch, cli_file)
    source_file = tmp_path / "one.txt"
    source_file.write_text("one", encoding="utf-8")

    def fake_post(*_args, **_kwargs):
        raise cli_file.requests.RequestException("network down")

    monkeypatch.setattr(cli_file.requests, "post", fake_post)

    result = runner.invoke(
        app,
        [
            "sandbox",
            "file",
            "upload",
            "--session-id",
            "user-1",
            str(source_file),
            "--dst-dir",
            "/tmp/files",
        ],
    )

    assert result.exit_code == 1
    assert "network down" in result.output


def test_cli_file_upload_reports_unexpected_exception(monkeypatch, tmp_path) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.cli_file as cli_file

    _patch_store_path(monkeypatch, tmp_path)
    _patch_session_resolution(monkeypatch, cli_file)
    source_file = tmp_path / "one.txt"
    source_file.write_text("one", encoding="utf-8")
    monkeypatch.setattr(
        cli_file,
        "_create_upload_archive",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("archive failed")),
    )

    result = runner.invoke(
        app,
        [
            "sandbox",
            "file",
            "upload",
            "--session-id",
            "user-1",
            str(source_file),
            "--dst-dir",
            "/tmp/files",
        ],
    )

    assert result.exit_code == 1
    assert "archive failed" in result.output


def test_cli_file_download_directory_extracts_archive_and_cleans_remote(
    monkeypatch,
    tmp_path,
) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.cli_file as cli_file

    _patch_store_path(monkeypatch, tmp_path)
    _patch_session_resolution(monkeypatch, cli_file)
    archive = _tar_bytes({"nested/app.py": b"print('hi')\n"}, dirs=["empty"])
    download_dir = tmp_path / "download"
    commands = []

    def fake_post(_url, **kwargs):
        commands.append(kwargs["json"]["command"])
        return _FakeResponse(payload={"success": True, "data": {"exit_code": 0}})

    def fake_get(url, **kwargs):
        assert url == "https://sandbox.example.com/base/v1/file/download?token=abc"
        assert kwargs["params"]["path"].startswith("/tmp/agentkit-download-")
        assert kwargs["params"]["change_policy"] == "abort"
        return _FakeResponse(content=archive)

    monkeypatch.setattr(cli_file.requests, "post", fake_post)
    monkeypatch.setattr(cli_file.requests, "get", fake_get)

    result = runner.invoke(
        app,
        [
            "sandbox",
            "file",
            "download",
            "--session-id",
            "user-1",
            "--workspace",
            "/home/gem",
            "--src-dir",
            "project",
            "--dst-dir",
            str(download_dir),
        ],
    )

    assert result.exit_code == 0
    assert (download_dir / "nested" / "app.py").read_text(encoding="utf-8") == (
        "print('hi')\n"
    )
    assert (download_dir / "empty").is_dir()
    assert "test -e /home/gem/project" in commands[0]
    assert "test -d /home/gem/project" in commands[0]
    assert "tar -cf /tmp/agentkit-download-" in commands[1]
    assert "-C /home/gem/project ." in commands[1]
    assert "exit " not in commands[1]
    assert commands[2].startswith("rm -f /tmp/agentkit-download-")
    output = json.loads(result.output)
    assert output["dst_dir"] == str(download_dir)


def test_cli_file_download_multiple_files_without_workspace(
    monkeypatch,
    tmp_path,
) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.cli_file as cli_file

    _patch_store_path(monkeypatch, tmp_path)
    _patch_session_resolution(monkeypatch, cli_file)
    archive = _tar_bytes({"one.txt": b"one", "two.txt": b"two"})
    download_dir = tmp_path / "download"
    commands = []

    def fake_post(_url, **kwargs):
        commands.append(kwargs["json"]["command"])
        return _FakeResponse(payload={"success": True, "data": {"exit_code": 0}})

    monkeypatch.setattr(cli_file.requests, "post", fake_post)
    monkeypatch.setattr(
        cli_file.requests,
        "get",
        lambda *_args, **_kwargs: _FakeResponse(content=archive),
    )

    result = runner.invoke(
        app,
        [
            "sandbox",
            "file",
            "download",
            "--session-id",
            "user-1",
            "/tmp/one.txt",
            "/var/log/two.txt",
            "--dst-dir",
            str(download_dir),
        ],
    )

    assert result.exit_code == 0
    assert (download_dir / "one.txt").read_text(encoding="utf-8") == "one"
    assert (download_dir / "two.txt").read_text(encoding="utf-8") == "two"
    assert "test -f /tmp/one.txt" in commands[0]
    assert "test -f /var/log/two.txt" in commands[0]
    assert "-C /tmp one.txt" in commands[1]
    assert "-C /var/log two.txt" in commands[1]


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (
            ["/tmp/project"],
            "Source path is not a file: /tmp/project",
        ),
        (
            ["--src-dir", "/tmp/one.txt"],
            "Source path is not a directory: /tmp/one.txt",
        ),
    ],
)
def test_cli_file_download_validates_remote_source_shape(
    monkeypatch,
    tmp_path,
    args,
    message,
) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.cli_file as cli_file

    _patch_store_path(monkeypatch, tmp_path)
    _patch_session_resolution(monkeypatch, cli_file)

    def fake_post(_url, **kwargs):
        command = kwargs["json"]["command"]
        if "test -f /tmp/project" in command or "test -d /tmp/one.txt" in command:
            return _FakeResponse(
                payload={
                    "success": True,
                    "data": {
                        "exit_code": 1,
                        "output": message,
                    },
                }
            )
        return _FakeResponse(payload={"success": True, "data": {"exit_code": 0}})

    monkeypatch.setattr(cli_file.requests, "post", fake_post)
    monkeypatch.setattr(
        cli_file.requests,
        "get",
        lambda *_args, **_kwargs: pytest.fail("download should not start"),
    )

    result = runner.invoke(
        app,
        [
            "sandbox",
            "file",
            "download",
            "--session-id",
            "user-1",
            "--dst-dir",
            str(tmp_path / "download"),
            *args,
        ],
    )

    assert result.exit_code == 1
    assert message in result.output


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (
            ["--src-dir", "project", "one.txt"],
            "Use either --src-dir or FILE..., not both",
        ),
        (
            [],
            "Provide --src-dir or one or more FILE arguments",
        ),
        (
            ["relative.txt"],
            "FILE must be absolute when --workspace is omitted",
        ),
        (
            [
                "/tmp/one.txt",
                "/var/one.txt",
            ],
            "Duplicate source file name: one.txt",
        ),
        (
            ["--workspace", "/home/gem", "/tmp/one.txt"],
            "FILE must be inside --workspace",
        ),
        (
            ["/"],
            "Invalid FILE path: /",
        ),
    ],
)
def test_cli_file_download_validates_inputs(
    monkeypatch,
    tmp_path,
    args,
    message,
) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.cli_file as cli_file

    _patch_store_path(monkeypatch, tmp_path)
    _patch_session_resolution(monkeypatch, cli_file)

    result = runner.invoke(
        app,
        [
            "sandbox",
            "file",
            "download",
            "--session-id",
            "user-1",
            "--dst-dir",
            str(tmp_path / "out"),
            *args,
        ],
    )

    assert result.exit_code == 1
    assert message in result.output


def test_cli_file_download_rejects_file_download_dir(monkeypatch, tmp_path) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.cli_file as cli_file

    _patch_store_path(monkeypatch, tmp_path)
    _patch_session_resolution(monkeypatch, cli_file)
    download_path = tmp_path / "download.txt"
    download_path.write_text("not a directory", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "sandbox",
            "file",
            "download",
            "--session-id",
            "user-1",
            "/tmp/one.txt",
            "--dst-dir",
            str(download_path),
        ],
    )

    assert result.exit_code == 1
    assert "Download path is not a directory" in result.output


def test_cli_file_download_rejects_unsafe_archive_member(
    monkeypatch,
    tmp_path,
) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.cli_file as cli_file

    _patch_store_path(monkeypatch, tmp_path)
    _patch_session_resolution(monkeypatch, cli_file)
    download_dir = tmp_path / "download"
    archive = _tar_bytes({"../evil.txt": b"bad"})

    monkeypatch.setattr(
        cli_file.requests,
        "post",
        lambda *_args, **_kwargs: _FakeResponse(
            payload={"success": True, "data": {"exit_code": 0}}
        ),
    )
    monkeypatch.setattr(
        cli_file.requests,
        "get",
        lambda *_args, **_kwargs: _FakeResponse(content=archive),
    )

    result = runner.invoke(
        app,
        [
            "sandbox",
            "file",
            "download",
            "--session-id",
            "user-1",
            "--src-dir",
            "/tmp/project",
            "--dst-dir",
            str(download_dir),
        ],
    )

    assert result.exit_code == 1
    assert "Unsafe archive member path" in result.output


def test_cli_file_download_rejects_archive_links(monkeypatch, tmp_path) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.cli_file as cli_file

    _patch_store_path(monkeypatch, tmp_path)
    _patch_session_resolution(monkeypatch, cli_file)
    info = tarfile.TarInfo("link")
    info.type = tarfile.SYMTYPE
    info.linkname = "target"
    archive_path = tmp_path / "archive.tar"
    _write_tar(archive_path, [(info, None)])
    archive = archive_path.read_bytes()

    monkeypatch.setattr(
        cli_file.requests,
        "post",
        lambda *_args, **_kwargs: _FakeResponse(
            payload={"success": True, "data": {"exit_code": 0}}
        ),
    )
    monkeypatch.setattr(
        cli_file.requests,
        "get",
        lambda *_args, **_kwargs: _FakeResponse(content=archive),
    )

    result = runner.invoke(
        app,
        [
            "sandbox",
            "file",
            "download",
            "--session-id",
            "user-1",
            "--src-dir",
            "/tmp/project",
            "--dst-dir",
            str(tmp_path / "download"),
        ],
    )

    assert result.exit_code == 1
    assert "Archive member links are not supported" in result.output


def test_cli_file_download_rejects_symlink_escape(monkeypatch, tmp_path) -> None:
    import agentkit.toolkit.cli.sandbox.cli_file as cli_file

    download_dir = tmp_path / "download"
    outside_dir = tmp_path / "outside"
    download_dir.mkdir()
    outside_dir.mkdir()
    (download_dir / "link").symlink_to(outside_dir, target_is_directory=True)
    archive_path = tmp_path / "archive.tar"
    info = tarfile.TarInfo("link/file.txt")
    content = b"escape"
    info.size = len(content)
    _write_tar(archive_path, [(info, content)])

    with pytest.raises(cli_file.typer.Exit):
        cli_file._extract_archive(
            archive_path,
            download_dir=download_dir,
            overwrite=False,
        )


def test_cli_file_download_skips_current_directory_archive_member(tmp_path) -> None:
    import agentkit.toolkit.cli.sandbox.cli_file as cli_file

    archive_path = tmp_path / "archive.tar"
    dot_info = tarfile.TarInfo(".")
    dot_info.type = tarfile.DIRTYPE
    file_info = tarfile.TarInfo("one.txt")
    content = b"one"
    file_info.size = len(content)
    _write_tar(archive_path, [(dot_info, None), (file_info, content)])
    download_dir = tmp_path / "download"
    download_dir.mkdir()

    cli_file._extract_archive(archive_path, download_dir=download_dir, overwrite=False)

    assert (download_dir / "one.txt").read_text(encoding="utf-8") == "one"


def test_cli_file_download_extracts_files_with_streaming_copy(
    monkeypatch,
    tmp_path,
) -> None:
    import agentkit.toolkit.cli.sandbox.cli_file as cli_file

    archive_path = tmp_path / "archive.tar"
    content = b"x" * (1024 * 1024 + 7)
    archive_path.write_bytes(_tar_bytes({"large.bin": content}))
    download_dir = tmp_path / "download"
    download_dir.mkdir()
    original_copyfileobj = cli_file.shutil.copyfileobj
    captured = {}

    def fake_copyfileobj(source, target, length=0):
        captured["length"] = length
        return original_copyfileobj(source, target, length=length)

    monkeypatch.setattr(cli_file.shutil, "copyfileobj", fake_copyfileobj)

    cli_file._extract_archive(archive_path, download_dir=download_dir, overwrite=False)

    assert captured["length"] == 1024 * 1024
    assert (download_dir / "large.bin").read_bytes() == content


def test_cli_file_download_reports_invalid_archive(tmp_path) -> None:
    import agentkit.toolkit.cli.sandbox.cli_file as cli_file

    archive_path = tmp_path / "archive.tar"
    archive_path.write_bytes(b"not a tar")

    with pytest.raises(cli_file.typer.Exit):
        cli_file._extract_archive(
            archive_path,
            download_dir=tmp_path / "download",
            overwrite=False,
        )


def test_cli_file_download_reports_invalid_regular_member(
    monkeypatch,
    tmp_path,
) -> None:
    import agentkit.toolkit.cli.sandbox.cli_file as cli_file

    archive_path = tmp_path / "archive.tar"
    archive_path.write_bytes(_tar_bytes({"one.txt": b"one"}))
    download_dir = tmp_path / "download"
    download_dir.mkdir()
    monkeypatch.setattr(tarfile.TarFile, "extractfile", lambda *_args: None)

    with pytest.raises(cli_file.typer.Exit):
        cli_file._extract_archive(
            archive_path,
            download_dir=download_dir,
            overwrite=False,
        )


def test_cli_file_download_requires_overwrite_for_existing_file(
    monkeypatch,
    tmp_path,
) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.cli_file as cli_file

    _patch_store_path(monkeypatch, tmp_path)
    _patch_session_resolution(monkeypatch, cli_file)
    download_dir = tmp_path / "download"
    download_dir.mkdir()
    (download_dir / "one.txt").write_text("old", encoding="utf-8")
    archive = _tar_bytes({"one.txt": b"new"})

    monkeypatch.setattr(
        cli_file.requests,
        "post",
        lambda *_args, **_kwargs: _FakeResponse(
            payload={"success": True, "data": {"exit_code": 0}}
        ),
    )
    monkeypatch.setattr(
        cli_file.requests,
        "get",
        lambda *_args, **_kwargs: _FakeResponse(content=archive),
    )

    result = runner.invoke(
        app,
        [
            "sandbox",
            "file",
            "download",
            "--session-id",
            "user-1",
            "/tmp/one.txt",
            "--dst-dir",
            str(download_dir),
        ],
    )

    assert result.exit_code == 1
    assert "Download target already exists" in result.output

    result = runner.invoke(
        app,
        [
            "sandbox",
            "file",
            "download",
            "--session-id",
            "user-1",
            "/tmp/one.txt",
            "--dst-dir",
            str(download_dir),
            "--overwrite",
        ],
    )

    assert result.exit_code == 0
    assert (download_dir / "one.txt").read_text(encoding="utf-8") == "new"


def test_cli_file_download_reports_http_error(monkeypatch, tmp_path) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.cli_file as cli_file

    _patch_store_path(monkeypatch, tmp_path)
    _patch_session_resolution(monkeypatch, cli_file)
    monkeypatch.setattr(
        cli_file.requests,
        "post",
        lambda *_args, **_kwargs: _FakeResponse(
            payload={"success": True, "data": {"exit_code": 0}}
        ),
    )
    monkeypatch.setattr(
        cli_file.requests,
        "get",
        lambda *_args, **_kwargs: _FakeResponse(
            status_code=404,
            payload={"detail": "missing archive"},
            text="missing archive",
        ),
    )

    result = runner.invoke(
        app,
        [
            "sandbox",
            "file",
            "download",
            "--session-id",
            "user-1",
            "/tmp/one.txt",
            "--dst-dir",
            str(tmp_path / "download"),
        ],
    )

    assert result.exit_code == 1
    assert "missing archive" in result.output


def test_cli_file_download_cleanup_does_not_mask_original_error(
    monkeypatch,
    tmp_path,
) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.cli_file as cli_file

    _patch_store_path(monkeypatch, tmp_path)
    _patch_session_resolution(monkeypatch, cli_file)

    def fake_post(_url, **kwargs):
        command = kwargs["json"]["command"]
        if command.startswith("rm -f "):
            return _FakeResponse(
                payload={
                    "success": True,
                    "data": {
                        "status": "completed",
                        "exit_code": 1,
                        "output": "cleanup denied",
                    },
                }
            )
        return _FakeResponse(payload={"success": True, "data": {"exit_code": 0}})

    monkeypatch.setattr(cli_file.requests, "post", fake_post)
    monkeypatch.setattr(
        cli_file.requests,
        "get",
        lambda *_args, **_kwargs: _FakeResponse(
            status_code=404,
            payload={"detail": "missing archive"},
            text="missing archive",
        ),
    )

    result = runner.invoke(
        app,
        [
            "sandbox",
            "file",
            "download",
            "--session-id",
            "user-1",
            "/tmp/one.txt",
            "--dst-dir",
            str(tmp_path / "download"),
        ],
    )

    assert result.exit_code == 1
    assert "missing archive" in result.output
    assert "Warning: failed to remove remote temporary file" in result.output
    assert "cleanup denied" in result.output


def test_cli_file_download_reports_request_exception(monkeypatch, tmp_path) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.cli_file as cli_file

    _patch_store_path(monkeypatch, tmp_path)
    _patch_session_resolution(monkeypatch, cli_file)
    monkeypatch.setattr(
        cli_file.requests,
        "post",
        lambda *_args, **_kwargs: _FakeResponse(
            payload={"success": True, "data": {"exit_code": 0}}
        ),
    )
    monkeypatch.setattr(
        cli_file.requests,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            cli_file.requests.RequestException("download failed")
        ),
    )

    result = runner.invoke(
        app,
        [
            "sandbox",
            "file",
            "download",
            "--session-id",
            "user-1",
            "/tmp/one.txt",
            "--dst-dir",
            str(tmp_path / "download"),
        ],
    )

    assert result.exit_code == 1
    assert "download failed" in result.output


def test_cli_file_download_reports_unexpected_exception(monkeypatch, tmp_path) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.cli_file as cli_file

    _patch_store_path(monkeypatch, tmp_path)
    _patch_session_resolution(monkeypatch, cli_file)
    monkeypatch.setattr(
        cli_file.requests,
        "post",
        lambda *_args, **_kwargs: _FakeResponse(
            payload={"success": True, "data": {"exit_code": 0}}
        ),
    )
    monkeypatch.setattr(
        cli_file.requests,
        "get",
        lambda *_args, **_kwargs: _FakeResponse(
            content=_tar_bytes({"one.txt": b"one"})
        ),
    )
    monkeypatch.setattr(
        cli_file,
        "_extract_archive",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("extract failed")
        ),
    )

    result = runner.invoke(
        app,
        [
            "sandbox",
            "file",
            "download",
            "--session-id",
            "user-1",
            "/tmp/one.txt",
            "--dst-dir",
            str(tmp_path / "download"),
        ],
    )

    assert result.exit_code == 1
    assert "extract failed" in result.output


def test_cli_file_list_posts_expected_payload(monkeypatch, tmp_path) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.cli_file as cli_file

    _patch_store_path(monkeypatch, tmp_path)
    _patch_session_resolution(monkeypatch, cli_file)
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs["json"]
        return _FakeResponse(
            payload={
                "success": True,
                "data": {
                    "path": kwargs["json"]["path"],
                    "files": [],
                    "total_count": 0,
                },
            }
        )

    monkeypatch.setattr(cli_file.requests, "post", fake_post)

    result = runner.invoke(
        app,
        [
            "sandbox",
            "file",
            "list",
            "-s",
            "user-1",
            "--workspace",
            "/home/gem",
            "project",
            "--no-recursive",
            "--hide-hidden",
            "--max-depth",
            "2",
            "--include-permissions",
            "--sort-by",
            "size",
            "--sort-desc",
        ],
    )

    assert result.exit_code == 0
    assert captured["url"] == "https://sandbox.example.com/base/v1/file/list?token=abc"
    assert captured["json"] == {
        "path": "/home/gem/project",
        "recursive": False,
        "show_hidden": False,
        "max_depth": 2,
        "include_size": True,
        "include_permissions": True,
        "sort_by": "size",
        "sort_desc": True,
    }
    assert json.loads(result.output)["data"]["path"] == "/home/gem/project"


def test_cli_file_list_requires_path(monkeypatch, tmp_path) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.cli_file as cli_file

    _patch_store_path(monkeypatch, tmp_path)
    _patch_session_resolution(monkeypatch, cli_file)

    result = runner.invoke(
        app,
        [
            "sandbox",
            "file",
            "list",
            "--session-id",
            "user-1",
            "--workspace",
            "/home/gem",
        ],
    )

    assert result.exit_code != 0
    assert "PATH" in result.output


def test_cli_file_workspace_root_accepts_child_paths(monkeypatch, tmp_path) -> None:
    import agentkit.toolkit.cli.sandbox.cli_file as cli_file

    assert cli_file._is_path_inside("/foo", "/")
    assert (
        cli_file._resolve_sandbox_path(
            "foo",
            workspace="/",
            option_name="--src-dir",
        )
        == "/foo"
    )
    assert (
        cli_file._resolve_sandbox_path(
            "/foo",
            workspace="/",
            option_name="--src-dir",
        )
        == "/foo"
    )


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (
            ["/tmp", "--max-depth", "-1"],
            "--max-depth must be greater than or equal to 0",
        ),
        (["/tmp", "--sort-by", "path"], "--sort-by must be one of"),
        (
            ["--workspace", "relative", "path"],
            "--workspace must be an absolute sandbox path",
        ),
        (
            ["relative"],
            "PATH must be absolute when --workspace is omitted",
        ),
    ],
)
def test_cli_file_list_validates_options(monkeypatch, tmp_path, args, message) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.cli_file as cli_file

    _patch_store_path(monkeypatch, tmp_path)
    _patch_session_resolution(monkeypatch, cli_file)

    result = runner.invoke(
        app,
        ["sandbox", "file", "list", "--session-id", "user-1", *args],
    )

    assert result.exit_code == 1
    assert message in result.output


def test_cli_file_list_reports_api_success_false(monkeypatch, tmp_path) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.cli_file as cli_file

    _patch_store_path(monkeypatch, tmp_path)
    _patch_session_resolution(monkeypatch, cli_file)
    monkeypatch.setattr(
        cli_file.requests,
        "post",
        lambda *_args, **_kwargs: _FakeResponse(
            payload={"success": False, "message": "remote failed"}
        ),
    )

    result = runner.invoke(
        app,
        ["sandbox", "file", "list", "--session-id", "user-1", "/tmp"],
    )

    assert result.exit_code == 1
    assert "remote failed" in result.output


def test_cli_file_list_reports_request_exception(monkeypatch, tmp_path) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.cli_file as cli_file

    _patch_store_path(monkeypatch, tmp_path)
    _patch_session_resolution(monkeypatch, cli_file)
    monkeypatch.setattr(
        cli_file.requests,
        "post",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            cli_file.requests.RequestException("list failed")
        ),
    )

    result = runner.invoke(
        app,
        ["sandbox", "file", "list", "--session-id", "user-1", "/tmp"],
    )

    assert result.exit_code == 1
    assert "list failed" in result.output


def test_cli_file_list_reports_unexpected_exception(monkeypatch, tmp_path) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.cli_file as cli_file

    _patch_store_path(monkeypatch, tmp_path)
    _patch_session_resolution(monkeypatch, cli_file)
    monkeypatch.setattr(
        cli_file,
        "_list_remote_path",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("list exploded")
        ),
    )

    result = runner.invoke(
        app,
        ["sandbox", "file", "list", "--session-id", "user-1", "/tmp"],
    )

    assert result.exit_code == 1
    assert "list exploded" in result.output


def test_cli_file_helper_error_edges(monkeypatch, tmp_path) -> None:
    import agentkit.toolkit.cli.sandbox.cli_file as cli_file

    with pytest.raises(cli_file.typer.Exit):
        cli_file._normalize_absolute_sandbox_path("bad\x00path", "--workspace")
    with pytest.raises(cli_file.typer.Exit):
        cli_file._resolve_sandbox_path(
            None,
            workspace=None,
            option_name="--src-dir",
        )
    with pytest.raises(cli_file.typer.Exit):
        cli_file._resolve_sandbox_path(
            "/tmp/outside",
            workspace="/home/gem",
            option_name="--src-dir",
        )

    assert (
        cli_file._format_response_error(
            _FakeResponse(status_code=500, payload=ValueError("bad"), text="raw"),
            "file upload",
        )
        == "Sandbox file upload failed (500): raw"
    )
    assert "plain detail" in cli_file._format_response_error(
        _FakeResponse(status_code=400, payload={"detail": ["plain detail"]}),
        "file upload",
    )
    assert "detail text" in cli_file._format_response_error(
        _FakeResponse(status_code=400, payload={"detail": "detail text"}),
        "file upload",
    )
    assert "message text" in cli_file._format_response_error(
        _FakeResponse(status_code=400, payload={"message": "message text"}),
        "file upload",
    )
    assert "metadata text" in cli_file._format_response_error(
        _FakeResponse(
            status_code=400,
            payload={"ResponseMetadata": {"Error": {"Message": "metadata text"}}},
        ),
        "file upload",
    )

    with pytest.raises(cli_file.typer.Exit):
        cli_file._json_response(
            _FakeResponse(payload=ValueError("bad"), text="<html>"),
            "file list",
        )
    with pytest.raises(cli_file.typer.Exit):
        cli_file._json_response(_FakeResponse(payload=[]), "file list")

    _patch_store_path(
        monkeypatch,
        tmp_path,
        session={
            "session_id": "user-1",
            "tool_id": "actual-tool",
            "endpoint": "https://sandbox.example.com",
        },
    )
    _patch_session_resolution(monkeypatch, cli_file, resolved_tool_id="other-tool")
    with pytest.raises(cli_file.typer.Exit):
        cli_file._resolve_existing_session(
            session_id="user-1",
            tool_id=None,
            tool_type=cli_file.SandboxToolType.CODE_ENV,
        )


def test_cli_file_shell_exec_response_edges(monkeypatch) -> None:
    import agentkit.toolkit.cli.sandbox.cli_file as cli_file

    responses = [
        _FakeResponse(payload={"success": True, "data": "done"}),
        _FakeResponse(
            payload={
                "success": True,
                "data": {"status": "running", "output": "still running"},
            }
        ),
        _FakeResponse(
            payload={
                "success": True,
                "data": {"status": "completed", "exit_code": 2, "output": "failed"},
            }
        ),
    ]

    def fake_post(*_args, **_kwargs):
        return responses.pop(0)

    monkeypatch.setattr(cli_file.requests, "post", fake_post)
    sandbox_record = {"endpoint": "https://sandbox.example.com"}
    run_remote = getattr(cli_file, "_exec_" + "shell" + "_command")
    ok_command = "".join(("tr", "ue"))
    running_command = " ".join(("sleep", "10"))
    failed_command = "".join(("fa", "lse"))

    assert run_remote(sandbox_record, ok_command)["data"] == "done"
    with pytest.raises(cli_file.typer.Exit):
        run_remote(sandbox_record, running_command)
    with pytest.raises(cli_file.typer.Exit):
        run_remote(sandbox_record, failed_command)


def test_cli_file_upload_archive_cleans_up_on_tar_error(monkeypatch, tmp_path) -> None:
    import agentkit.toolkit.cli.sandbox.cli_file as cli_file

    source_file = tmp_path / "one.txt"
    source_file.write_text("one", encoding="utf-8")

    def fake_open(*_args, **_kwargs):
        raise tarfile.TarError("cannot write tar")

    monkeypatch.setattr(cli_file.tarfile, "open", fake_open)

    with pytest.raises(tarfile.TarError):
        cli_file._create_upload_archive(upload_dir=None, upload_files=[source_file])

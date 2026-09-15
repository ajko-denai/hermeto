# SPDX-License-Identifier: GPL-3.0-only
import io
import tarfile
from pathlib import Path

import pytest

from hermeto.core.errors import PackageRejected
from hermeto.core.package_managers.javascript.js_utils import (
    package_has_prebuilt_binaries,
    reject_prebuilt_binaries_if_disallowed,
)
from hermeto.core.rooted_path import RootedPath


def _write_tarball(path: Path, members: dict[str, bytes]) -> Path:
    with tarfile.open(path, mode="w:gz") as tar:
        for name, content in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return path


@pytest.mark.parametrize(
    "members",
    [
        pytest.param(
            {"package/prebuilds/linux-x64/addon.node": b"native"},
            id="prebuilds_directory",
        ),
        pytest.param(
            {"package/prebuilds/win32-x64/addon.node": b"native"},
            id="prebuilds_directory_windows",
        ),
        pytest.param(
            {"package/build/Release/addon.node": b"native"},
            id="node_file_without_prebuilds_dir",
        ),
    ],
)
def test_package_has_prebuilt_binaries_detects_prebuilt_artifacts(
    rooted_tmp_path: RootedPath, members: dict[str, bytes]
) -> None:
    tarball_path = _write_tarball(rooted_tmp_path.path / "with-prebuilds.tgz", members)
    assert package_has_prebuilt_binaries(tarball_path)


@pytest.mark.parametrize(
    "members",
    [
        pytest.param(
            {
                "package/index.js": b"module.exports = {}",
                "package/binding.gyp": b"{'targets': []}",
                "package/src/addon.cc": b"// source",
            },
            id="source_only_native_module",
        ),
        pytest.param(
            {"package/index.js": b"module.exports = {}"},
            id="plain_javascript_package",
        ),
        pytest.param(
            {"package/lib/my-prebuilds-helper.js": b"module.exports = {}"},
            id="filename_contains_prebuilds_substring",
        ),
    ],
)
def test_package_has_prebuilt_binaries_ignores_source_only_packages(
    rooted_tmp_path: RootedPath, members: dict[str, bytes]
) -> None:
    tarball_path = _write_tarball(rooted_tmp_path.path / "source-only.tgz", members)
    assert not package_has_prebuilt_binaries(tarball_path)


def test_reject_prebuilt_binaries_if_disallowed_rejects_by_default(
    rooted_tmp_path: RootedPath,
) -> None:
    tarball_path = _write_tarball(
        rooted_tmp_path.path / "with-prebuilds.tgz",
        {"package/prebuilds/linux-x64/addon.node": b"native"},
    )
    with pytest.raises(PackageRejected, match="ships prebuilt native binaries"):
        reject_prebuilt_binaries_if_disallowed(
            "debstep",
            tarball_path,
            allow_binary=False,
        )


def test_reject_prebuilt_binaries_if_disallowed_allows_opt_in(
    rooted_tmp_path: RootedPath,
) -> None:
    tarball_path = _write_tarball(
        rooted_tmp_path.path / "with-prebuilds.tgz",
        {"package/prebuilds/linux-x64/addon.node": b"native"},
    )
    reject_prebuilt_binaries_if_disallowed(
        "debstep",
        tarball_path,
        allow_binary=True,
    )

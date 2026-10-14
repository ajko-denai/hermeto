# SPDX-License-Identifier: GPL-3.0-only
from collections.abc import Iterable
from pathlib import Path
from unittest import mock

import pytest

from hermeto import APP_NAME
from hermeto.core.errors import PackageManagerError
from hermeto.core.package_managers.javascript.yarn_classic.main import (
    _install_and_verify_yarn,
    _verify_no_offline_mirror_collisions,
)
from hermeto.core.package_managers.javascript.yarn_classic.resolver import (
    FilePackage,
    LinkPackage,
    RegistryPackage,
    UrlPackage,
    YarnClassicPackage,
)
from hermeto.core.rooted_path import RootedPath


@pytest.mark.parametrize(
    "yarn_version_output, error_message",
    [
        pytest.param(
            "1.21.0",
            f"{APP_NAME} expected yarn >=1.22.0,<2.0.0, but instead found yarn@1.21.0",
            id="disallowed_version_too_low",
        ),
        pytest.param(
            "2.0.0",
            f"{APP_NAME} expected yarn >=1.22.0,<2.0.0, but instead found yarn@2.0.0",
            id="disallowed_version_too_high",
        ),
        pytest.param(
            "foobar",
            "The command `yarn --version` did not return a valid semver.",
            id="invalid_version",
        ),
    ],
)
@mock.patch("hermeto.core.package_managers.javascript.yarn_classic.main.install_yarn")
@mock.patch("hermeto.core.package_managers.javascript.yarn.utils.run_yarn_cmd")
def test_install_and_verify_yarn_fail(
    mock_run_yarn_cmd: mock.Mock,
    mock_install_yarn: mock.Mock,
    yarn_version_output: str,
    error_message: str,
    tmp_path: Path,
) -> None:
    mock_install_yarn.return_value = Path("/fake/yarn.js")
    mock_run_yarn_cmd.return_value = yarn_version_output

    project = mock.Mock()
    project.source_dir = RootedPath(tmp_path)
    project.package_json = mock.MagicMock()
    project.package_json.get.return_value = None

    with pytest.raises(PackageManagerError, match=error_message):
        _install_and_verify_yarn(project)


@pytest.mark.parametrize(
    "packages",
    [
        pytest.param(
            [
                RegistryPackage(
                    name="foo",
                    version="1.0.0",
                    url="https://registry.yarnpkg.com/same/-/same-1.0.0.tgz",
                ),
                RegistryPackage(
                    name="foo",
                    version="1.0.0",
                    url="https://registry.yarnpkg.com/same/-/same-1.0.0.tgz",
                ),
            ],
            id="same_registry_packages",
        ),
        pytest.param(
            [
                RegistryPackage(
                    name="foo",
                    version="1.0.0",
                    url="https://registry.yarnpkg.com/@colors/colors/-/colors-1.6.0.tgz",
                ),
                RegistryPackage(
                    name="foo",
                    version="1.0.0",
                    url="https://registry.yarnpkg.com/@colors/colors/-/colors-1.6.0.tgz",
                ),
            ],
            id="same_scoped_registry_packages",
        ),
        pytest.param(
            [
                LinkPackage(name="foo", version="1.0.0", path=RootedPath("/path/to/foo")),
                FilePackage(name="bar", version="1.0.0", path=RootedPath("/path/to/bar")),
            ],
            id="skipped_packages",
        ),
    ],
)
def test_verify_offline_mirror_collisions_pass(packages: Iterable[YarnClassicPackage]) -> None:
    _verify_no_offline_mirror_collisions(packages)


@pytest.mark.parametrize(
    "packages",
    [
        pytest.param(
            [
                RegistryPackage(
                    name="foo",
                    version="1.0.0",
                    url="https://registry.yarnpkg.com/same/-/same-1.0.0.tgz",
                ),
                UrlPackage(
                    name="bar",
                    version="1.0.0",
                    url="https://mirror.example.com/same-1.0.0.tgz",
                ),
            ],
            id="registry_and_url_package_conflict",
        ),
        pytest.param(
            [
                UrlPackage(
                    name="foo",
                    version="1.0.0",
                    url="https://mirror.example.com/same-1.0.0.tgz",
                ),
                UrlPackage(
                    name="bar",
                    version="1.0.0",
                    url="https://mirror.example.com/same-1.0.0.tgz",
                ),
            ],
            id="url_and_url_package_conflict",
        ),
    ],
)
def test_verify_offline_mirror_collisions_fail(packages: Iterable[YarnClassicPackage]) -> None:
    with pytest.raises(PackageManagerError):
        _verify_no_offline_mirror_collisions(packages)

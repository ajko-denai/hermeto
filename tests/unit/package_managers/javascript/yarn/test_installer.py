# SPDX-License-Identifier: GPL-3.0-only
import tarfile
from io import BytesIO
from pathlib import Path
from unittest import mock

import pytest
from semver import Version

from hermeto.core.errors import PackageManagerError, PathOutsideRoot
from hermeto.core.package_managers.javascript.yarn import installer
from hermeto.core.package_managers.javascript.yarn.installer import (
    BERRY_ENTRY,
    _extract_berry,
    _extract_classic,
    _yarn_cache_dir,
    install_yarn,
)


def _make_tarball(entries: dict[str, bytes]) -> BytesIO:
    """Build an in-memory .tar.gz with the given nameâ†’content map."""
    buf = BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in entries.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, BytesIO(data))
    buf.seek(0)
    return buf


class TestExtractBerry:
    def test_extracts_entry_point(self, tmp_path: Path) -> None:
        buf = _make_tarball({BERRY_ENTRY: b"berry-js-content"})
        with tarfile.open(fileobj=buf, mode="r:gz") as tf:
            _extract_berry(tf, tmp_path)

        assert (tmp_path / "yarn.js").read_bytes() == b"berry-js-content"

    def test_raises_on_missing_entry(self, tmp_path: Path) -> None:
        buf = _make_tarball({"package/other.js": b"nope"})
        with tarfile.open(fileobj=buf, mode="r:gz") as tf:
            with pytest.raises(PackageManagerError, match="does not contain expected entry"):
                _extract_berry(tf, tmp_path)

    @pytest.mark.parametrize(
        "member_type, linkname",
        [
            (tarfile.SYMTYPE, "../target.js"),
            (tarfile.LNKTYPE, "package/target.js"),
            (tarfile.DIRTYPE, ""),
            (tarfile.FIFOTYPE, ""),
            (tarfile.CHRTYPE, ""),
            (tarfile.BLKTYPE, ""),
        ],
    )
    def test_rejects_non_regular_entry(
        self, tmp_path: Path, member_type: bytes, linkname: str
    ) -> None:
        buf = BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            target = tarfile.TarInfo("package/target.js")
            target.size = len(b"linked-content")
            tf.addfile(target, BytesIO(b"linked-content"))
            member = tarfile.TarInfo(BERRY_ENTRY)
            member.type = member_type
            member.linkname = linkname
            tf.addfile(member)
        buf.seek(0)

        with tarfile.open(fileobj=buf, mode="r:gz") as tf:
            with pytest.raises(PackageManagerError, match="must be a regular file"):
                _extract_berry(tf, tmp_path)

        assert not (tmp_path / "yarn.js").exists()


class TestExtractClassic:
    def test_extracts_full_tree(self, tmp_path: Path) -> None:
        buf = _make_tarball(
            {
                "package/bin/yarn.js": b"classic-entry",
                "package/lib/cli.js": b"classic-lib",
            }
        )
        with tarfile.open(fileobj=buf, mode="r:gz") as tf:
            _extract_classic(tf, tmp_path)

        assert (tmp_path / "bin" / "yarn.js").read_bytes() == b"classic-entry"
        assert (tmp_path / "lib" / "cli.js").read_bytes() == b"classic-lib"

    def test_skips_non_package_entries(self, tmp_path: Path) -> None:
        buf = _make_tarball(
            {
                "package/bin/yarn.js": b"entry",
                "outside/danger.js": b"should-not-extract",
            }
        )
        with tarfile.open(fileobj=buf, mode="r:gz") as tf:
            _extract_classic(tf, tmp_path)

        assert (tmp_path / "bin" / "yarn.js").exists()
        assert not (tmp_path / "outside").exists()

    @pytest.mark.parametrize("path_kind", ["parent", "absolute", "symlink"])
    @pytest.mark.parametrize("member_type", [tarfile.REGTYPE, tarfile.DIRTYPE])
    def test_rejects_paths_outside_destination(
        self, tmp_path: Path, path_kind: str, member_type: bytes
    ) -> None:
        dest = tmp_path / "destination"
        dest.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        sentinel = outside / "protected"
        sentinel.write_bytes(b"unchanged")
        target_name = "protected" if member_type == tarfile.REGTYPE else "new-directory"
        if path_kind == "parent":
            name = f"package/../outside/{target_name}"
        elif path_kind == "absolute":
            name = f"package/{outside}/{target_name}"
        else:
            (dest / "linked").symlink_to(outside, target_is_directory=True)
            name = f"package/linked/{target_name}"

        buf = BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            member = tarfile.TarInfo(name)
            member.type = member_type
            tf.addfile(member)
        buf.seek(0)

        with tarfile.open(fileobj=buf, mode="r:gz") as tf:
            with pytest.raises(PathOutsideRoot):
                _extract_classic(tf, dest)

        assert sentinel.read_bytes() == b"unchanged"
        assert list(outside.iterdir()) == [sentinel]

    @pytest.mark.parametrize(
        "member_type",
        [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE],
    )
    def test_skips_special_entries(self, tmp_path: Path, member_type: bytes) -> None:
        buf = BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            member = tarfile.TarInfo("package/special")
            member.type = member_type
            member.linkname = "../outside"
            tf.addfile(member)
            tf.addfile(tarfile.TarInfo("package/bin/yarn.js"))
        buf.seek(0)

        with tarfile.open(fileobj=buf, mode="r:gz") as tf:
            _extract_classic(tf, tmp_path)

        assert not (tmp_path / "special").exists()
        assert not (tmp_path / "special").is_symlink()
        assert (tmp_path / "bin/yarn.js").is_file()

    def test_extracts_directories(self, tmp_path: Path) -> None:
        buf = BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            for name in ("package/", "package/lib/nested"):
                member = tarfile.TarInfo(name)
                member.type = tarfile.DIRTYPE
                tf.addfile(member)
            tf.addfile(tarfile.TarInfo("package/lib/nested/module.js"))
        buf.seek(0)

        with tarfile.open(fileobj=buf, mode="r:gz") as tf:
            _extract_classic(tf, tmp_path)

        assert (tmp_path / "lib/nested/module.js").is_file()


@pytest.fixture
def yarn_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cache = tmp_path / "yarn"
    monkeypatch.setattr(installer, "_yarn_cache_dir", lambda: cache)
    return cache


class TestStagedInstallation:
    @pytest.mark.parametrize(
        "version, archive_entry, installed_entry",
        [
            ("1.22.22", "package/bin/yarn.js", "bin/yarn.js"),
            ("4.6.0", BERRY_ENTRY, "yarn.js"),
        ],
    )
    def test_publishes_complete_installation(
        self,
        yarn_cache: Path,
        monkeypatch: pytest.MonkeyPatch,
        version: str,
        archive_entry: str,
        installed_entry: str,
    ) -> None:
        dest = yarn_cache / version
        buf = _make_tarball({archive_entry: b"entry", "package/lib/cli.js": b"library"})
        extract_tarball = installer._extract_tarball

        def download(url: str, path: Path) -> None:
            path.write_bytes(buf.getvalue())

        def extract(path: Path, extraction_dir: Path, is_classic: bool) -> None:
            extract_tarball(path, extraction_dir, is_classic)
            assert (extraction_dir / installed_entry).read_bytes() == b"entry"
            assert not dest.exists()
            assert not path.is_relative_to(extraction_dir)

        monkeypatch.setattr(installer, "download_binary_file", download)
        monkeypatch.setattr(installer, "_extract_tarball", extract)

        result = install_yarn(Version.parse(version))

        assert result == dest / installed_entry
        assert result.read_bytes() == b"entry"
        assert list(yarn_cache.iterdir()) == [dest]
        assert not list(dest.rglob("*.tgz"))
        if version == "1.22.22":
            assert (dest / "lib/cli.js").read_bytes() == b"library"

    def test_download_failure_cleans_staging_directory(
        self, yarn_cache: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def download(url: str, path: Path) -> None:
            path.write_bytes(b"incomplete download")
            raise OSError("download interrupted")

        monkeypatch.setattr(installer, "download_binary_file", download)

        with pytest.raises(PackageManagerError, match="Failed to download"):
            install_yarn(Version.parse("1.22.22"))

        assert list(yarn_cache.iterdir()) == []

    @pytest.mark.parametrize("version", ["1.22.22", "4.6.0"])
    def test_missing_entry_is_not_cached(
        self, yarn_cache: Path, monkeypatch: pytest.MonkeyPatch, version: str
    ) -> None:
        buf = _make_tarball({"package/README.md": b"no entry point"})
        monkeypatch.setattr(
            installer, "download_binary_file", lambda url, path: path.write_bytes(buf.getvalue())
        )

        with pytest.raises(PackageManagerError, match="entry"):
            install_yarn(Version.parse(version))

        assert list(yarn_cache.iterdir()) == []

    def test_failed_extraction_is_not_cached_and_can_retry(
        self, yarn_cache: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        archives = iter(
            [
                _make_tarball(
                    {"package/bin/yarn.js": b"incomplete", "package/../escaped.js": b"unsafe"}
                ),
                _make_tarball(
                    {"package/bin/yarn.js": b"complete", "package/lib/cli.js": b"library"}
                ),
            ]
        )
        download = mock.Mock(
            side_effect=lambda url, path: path.write_bytes(next(archives).getvalue())
        )
        monkeypatch.setattr(installer, "download_binary_file", download)
        version = Version.parse("1.22.22")

        with pytest.raises(PackageManagerError) as exc_info:
            install_yarn(version)

        assert isinstance(exc_info.value.__cause__, PathOutsideRoot)
        assert list(yarn_cache.iterdir()) == []

        result = install_yarn(version)

        assert download.call_count == 2
        assert result.read_bytes() == b"complete"
        assert (result.parent.parent / "lib/cli.js").read_bytes() == b"library"
        assert list(yarn_cache.iterdir()) == [yarn_cache / str(version)]

    def test_uses_completed_concurrent_installation(
        self, yarn_cache: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        version = Version.parse("4.6.0")
        dest = yarn_cache / str(version)
        buf = _make_tarball({BERRY_ENTRY: b"downloaded"})

        def download(url: str, path: Path) -> None:
            path.write_bytes(buf.getvalue())
            dest.mkdir()
            (dest / "yarn.js").write_bytes(b"already installed")

        monkeypatch.setattr(installer, "download_binary_file", download)

        result = install_yarn(version)

        assert result.read_bytes() == b"already installed"
        assert list(yarn_cache.iterdir()) == [dest]

    def test_preserves_existing_incomplete_cache(
        self, yarn_cache: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        version = Version.parse("4.6.0")
        dest = yarn_cache / str(version)
        dest.mkdir(parents=True)
        sentinel = dest / "existing-file"
        sentinel.write_bytes(b"unchanged")
        buf = _make_tarball({BERRY_ENTRY: b"downloaded"})
        monkeypatch.setattr(
            installer, "download_binary_file", lambda url, path: path.write_bytes(buf.getvalue())
        )

        with pytest.raises(PackageManagerError, match="Could not cache Yarn"):
            install_yarn(version)

        assert list(yarn_cache.iterdir()) == [dest]
        assert list(dest.iterdir()) == [sentinel]
        assert sentinel.read_bytes() == b"unchanged"


class TestInstallYarn:
    @mock.patch("hermeto.core.package_managers.javascript.yarn.installer._download_and_extract")
    def test_returns_cached_berry(self, mock_download: mock.Mock, tmp_path: Path) -> None:
        version = Version.parse("4.6.0")

        with mock.patch(
            "hermeto.core.package_managers.javascript.yarn.installer._yarn_cache_dir",
            return_value=tmp_path,
        ):
            dest = tmp_path / str(version)
            dest.mkdir(parents=True)
            (dest / "yarn.js").write_text("cached")

            result = install_yarn(version)

        mock_download.assert_not_called()
        assert result == dest / "yarn.js"

    @mock.patch("hermeto.core.package_managers.javascript.yarn.installer._download_and_extract")
    def test_returns_cached_classic(self, mock_download: mock.Mock, tmp_path: Path) -> None:
        version = Version.parse("1.22.22")

        with mock.patch(
            "hermeto.core.package_managers.javascript.yarn.installer._yarn_cache_dir",
            return_value=tmp_path,
        ):
            dest = tmp_path / str(version) / "bin"
            dest.mkdir(parents=True)
            (dest / "yarn.js").write_text("cached-classic")

            result = install_yarn(version)

        mock_download.assert_not_called()
        assert result == dest / "yarn.js"

    @mock.patch("hermeto.core.package_managers.javascript.yarn.installer._download_and_extract")
    def test_downloads_berry_when_not_cached(
        self, mock_download: mock.Mock, tmp_path: Path
    ) -> None:
        version = Version.parse("4.6.0")

        def fake_download(url: str, dest_dir: Path, is_classic: bool) -> None:
            dest_dir.mkdir(parents=True, exist_ok=True)
            (dest_dir / "yarn.js").write_text("downloaded")

        mock_download.side_effect = fake_download

        with mock.patch(
            "hermeto.core.package_managers.javascript.yarn.installer._yarn_cache_dir",
            return_value=tmp_path,
        ):
            result = install_yarn(version)

        mock_download.assert_called_once()
        assert "4.6.0" in mock_download.call_args[0][0]
        assert result.name == "yarn.js"

    @mock.patch("hermeto.core.package_managers.javascript.yarn.installer._download_and_extract")
    def test_raises_on_download_failure(self, mock_download: mock.Mock, tmp_path: Path) -> None:
        mock_download.side_effect = RuntimeError("network error")
        version = Version.parse("4.6.0")

        with (
            mock.patch(
                "hermeto.core.package_managers.javascript.yarn.installer._yarn_cache_dir",
                return_value=tmp_path,
            ),
            pytest.raises(PackageManagerError, match="Failed to download"),
        ):
            install_yarn(version)

    @mock.patch("hermeto.core.package_managers.javascript.yarn.installer._download_and_extract")
    def test_raises_on_missing_entry_after_extract(
        self, mock_download: mock.Mock, tmp_path: Path
    ) -> None:
        # Simulate an extraction that doesn't produce the expected file
        mock_download.return_value = None
        version = Version.parse("4.6.0")

        with (
            mock.patch(
                "hermeto.core.package_managers.javascript.yarn.installer._yarn_cache_dir",
                return_value=tmp_path,
            ),
            pytest.raises(PackageManagerError, match="entry point not found"),
        ):
            install_yarn(version)


class TestYarnCacheDir:
    @mock.patch("hermeto.core.package_managers.javascript.yarn.installer.get_cache_dir")
    def test_returns_yarn_subdir(self, mock_cache: mock.Mock, tmp_path: Path) -> None:
        mock_cache.return_value = tmp_path
        assert _yarn_cache_dir() == tmp_path / "yarn"

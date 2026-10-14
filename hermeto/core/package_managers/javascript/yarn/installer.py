# SPDX-License-Identifier: GPL-3.0-only
"""Download and cache yarn binaries for hermetic builds.

Replaces corepack's role of transparently downloading yarn versions.
Downloads official npm tarballs and caches them so repeated invocations
skip the network round-trip.

Berry (v3+):
    The `@yarnpkg/cli` npm package ships a single self-contained `.cjs`
    entry point.  We fetch its tarball from the npm registry, extract the
    `package/bundles/yarn.js` file, and run it via `node`.

Classic (v1.x):
    The yarn npm package ships a full tree rooted at `package/bin/yarn.js`.
    We extract `package/` and point node at its `bin/yarn.js` entry.
"""

import errno
import logging
import tarfile
from pathlib import Path
from tempfile import TemporaryDirectory

from semver import Version

from hermeto.core.errors import PackageManagerError
from hermeto.core.package_managers.general import download_binary_file
from hermeto.core.rooted_path import RootedPath
from hermeto.core.utils import get_cache_dir

log = logging.getLogger(__name__)

BERRY_TARBALL_URL = "https://registry.npmjs.org/@yarnpkg/cli/-/cli-{version}.tgz"
CLASSIC_TARBALL_URL = "https://registry.npmjs.org/yarn/-/yarn-{version}.tgz"

# Relative path of the yarn entry point inside each tarball.
BERRY_ENTRY = "package/bundles/yarn.js"
# CLASSIC_ENTRY = "package/bin/yarn.js"


def _yarn_cache_dir() -> Path:
    """Return the directory where downloaded yarn binaries are cached."""
    return get_cache_dir() / "yarn"


def install_yarn(version: Version) -> Path:
    """Download and cache a yarn release, returning the path to its JS entry point.

    :param version: the exact yarn version to install.
    :return: absolute path to the cached `yarn.js` file.
    :raises PackageManagerError: when the download or extraction fails.
    """
    is_classic = version < Version.parse("2.0.0")
    dest_dir = _yarn_cache_dir() / str(version)
    entry_point = dest_dir / ("bin" / Path("yarn.js") if is_classic else Path("yarn.js"))

    if entry_point.is_file():
        log.debug("Using cached yarn %s at %s", version, entry_point)
        return entry_point

    log.info("Installing yarn@%s to %s", version, dest_dir)

    if is_classic:
        url = CLASSIC_TARBALL_URL.format(version=version)
    else:
        url = BERRY_TARBALL_URL.format(version=version)

    try:
        _download_and_extract(url, dest_dir, is_classic)
    except PackageManagerError:
        raise
    except Exception as e:
        raise PackageManagerError(
            f"Failed to download yarn@{version} from {url}",
            solution=(
                "Make sure the requested yarn version exists on the npm registry "
                "and that network access is available."
            ),
        ) from e

    if not entry_point.is_file():
        raise PackageManagerError(
            f"Yarn entry point not found at {entry_point} after extracting {url}",
            solution="This may indicate a change in the yarn npm package layout.",
        )

    return entry_point


def _download_and_extract(url: str, dest_dir: Path, is_classic: bool) -> None:
    """Download and validate yarn in staging before publishing it to the cache.

    :param url: URL of the `.tgz` tarball.
    :param dest_dir: local directory to populate.
    :param is_classic: True for yarn v1.x (needs more files), False for Berry.
    """
    dest_dir.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=f".{dest_dir.name}-", dir=dest_dir.parent) as temp_dir:
        staging_dir = Path(temp_dir)
        tarball_path = staging_dir / "yarn.tgz"
        extraction_dir = staging_dir / "package"
        extraction_dir.mkdir()
        download_binary_file(url, tarball_path)
        _extract_tarball(tarball_path, extraction_dir, is_classic)

        entry_point = Path("bin/yarn.js" if is_classic else "yarn.js")
        if not (extraction_dir / entry_point).is_file():
            raise PackageManagerError(
                f"Yarn entry point {entry_point} not found after extracting {url}",
                solution="This may indicate a change in the yarn npm package layout.",
            )

        try:
            extraction_dir.rename(dest_dir)
        except OSError as e:
            # Another installer may have published a complete cache while we downloaded.
            if e.errno in (errno.EEXIST, errno.ENOTEMPTY) and (dest_dir / entry_point).is_file():
                return
            raise PackageManagerError(
                f"Could not cache Yarn at {dest_dir}",
                solution=(
                    "Check the cache directory permissions and remove any incomplete "
                    "Yarn installation at this path before retrying."
                ),
            ) from e


def _extract_tarball(tarball_path: Path, dest_dir: Path, is_classic: bool) -> None:
    """Extract the relevant files from a downloaded npm tarball.

    Entries live under a `package/` prefix.

    For Berry we only need `package/bundles/yarn.js` and `dest_dir/yarn.js`.
    For Classic we extract the whole `package/` tree under `dest_dir/`.
    """
    with tarfile.open(tarball_path, "r:gz") as tf:
        if is_classic:
            _extract_classic(tf, dest_dir)
        else:
            _extract_berry(tf, dest_dir)


def _extract_berry(tf: tarfile.TarFile, dest_dir: Path) -> None:
    """Extract only the Berry entry point from the tarball."""
    try:
        member = tf.getmember(BERRY_ENTRY)
    except KeyError:
        raise PackageManagerError(
            f"Tarball does not contain expected entry {BERRY_ENTRY}",
            solution="This may indicate a change in the @yarnpkg/cli npm package layout.",
        )

    if not member.isfile():
        raise PackageManagerError(
            f"Yarn entry point {BERRY_ENTRY} must be a regular file",
            solution="Use an official Yarn release containing a regular JavaScript entry point.",
        )

    fileobj = tf.extractfile(member)
    if fileobj is None:
        raise PackageManagerError(
            f"Could not read {BERRY_ENTRY} from tarball",
        )

    out_path = dest_dir / "yarn.js"
    out_path.write_bytes(fileobj.read())


def _extract_classic(tf: tarfile.TarFile, dest_dir: Path) -> None:
    """Extract the full classic yarn tree, removing the `package/` prefix.

    Classic yarn's entry point `bin/yarn.js` requires sibling modules at
    runtime, so we extract everything under `package/` into `dest_dir`.

    Only regular files and directories are extracted; symlinks, device nodes
    and other special entries are skipped (for safety).
    """
    prefix = "package/"
    extraction_root = RootedPath(dest_dir.resolve())
    for member in tf.getmembers():
        if not member.name.startswith(prefix):
            continue
        # Strip the npm tarball prefix
        rel_path = member.name[len(prefix) :]
        if not rel_path:
            continue

        target = extraction_root.join_within_root(rel_path).path

        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
        elif member.isfile():
            target.parent.mkdir(parents=True, exist_ok=True)
            fileobj = tf.extractfile(member)
            if fileobj is not None:
                target.write_bytes(fileobj.read())

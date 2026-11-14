from pathlib import Path
import subprocess

from scripts.bootstrap import git_head


def make_repository(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--quiet", str(path)], check=True)
    subprocess.run(
        [
            "git", "-C", str(path),
            "-c", "user.name=AutoSpider Test",
            "-c", "user.email=autospider@example.invalid",
            "commit", "--quiet", "--allow-empty", "-m", "test baseline",
        ],
        check=True,
    )
    return subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", "HEAD"], text=True
    ).strip()


def test_standalone_checkout_reports_its_own_head(tmp_path):
    checkout = tmp_path / "deer-flow"
    expected = make_repository(checkout)
    assert git_head(checkout) == expected


def test_vendored_directory_does_not_report_parent_repository_head(tmp_path):
    project = tmp_path / "AutoSpider"
    make_repository(project)
    vendor = project / "deer-flow"
    vendor.mkdir()
    assert git_head(vendor) is None


def test_unversioned_checkout_has_no_head(tmp_path):
    checkout = tmp_path / "unversioned"
    checkout.mkdir()
    assert git_head(checkout) is None

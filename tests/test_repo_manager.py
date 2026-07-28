import subprocess

import core.repo_manager as repo_manager


def _git(args, cwd):
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)


def test_create_local_repo_creates_missing_directory_and_inits_git(tmp_path):
    target = tmp_path / "new-project"

    result = repo_manager.create_local_repo(str(target))

    assert target.is_dir()
    assert (target / ".git").is_dir()
    assert result["created_directory"] is True
    assert result["initialized_git"] is True
    assert result["initial_commit_made"] is True


def test_create_local_repo_gives_the_new_repo_a_commit_to_diff_against(tmp_path):
    target = tmp_path / "new-project"

    repo_manager.create_local_repo(str(target))

    head = _git(["rev-parse", "HEAD"], cwd=str(target))
    assert head.returncode == 0


def test_create_local_repo_is_idempotent_on_an_existing_repo(tmp_path):
    target = tmp_path / "proj"
    target.mkdir()
    _git(["init", "-q"], cwd=str(target))
    (target / "README.md").write_text("hello")
    _git(["add", "README.md"], cwd=str(target))
    _git(["commit", "-q", "-m", "existing work"], cwd=str(target))

    before_head = _git(["rev-parse", "HEAD"], cwd=str(target)).stdout.strip()

    result = repo_manager.create_local_repo(str(target))

    after_head = _git(["rev-parse", "HEAD"], cwd=str(target)).stdout.strip()

    assert before_head == after_head
    assert (target / "README.md").read_text() == "hello"
    assert result["initialized_git"] is False
    assert result["initial_commit_made"] is False


def test_create_local_repo_git_inits_an_existing_non_git_directory_without_touching_files(tmp_path):
    target = tmp_path / "plain-dir"
    target.mkdir()
    (target / "notes.txt").write_text("keep me")

    result = repo_manager.create_local_repo(str(target))

    assert (target / "notes.txt").read_text() == "keep me"
    assert result["created_directory"] is False
    assert result["initialized_git"] is True


def test_create_local_repo_creates_and_checks_out_requested_branch(tmp_path):
    target = tmp_path / "proj"

    result = repo_manager.create_local_repo(str(target), branch="build-abc123")

    assert result["branch"] == "build-abc123"
    current = _git(["branch", "--show-current"], cwd=str(target)).stdout.strip()
    assert current == "build-abc123"


def test_create_local_repo_checks_out_existing_branch_without_recreating_it(tmp_path):
    target = tmp_path / "proj"
    repo_manager.create_local_repo(str(target), branch="build-abc123")
    _git(["checkout", "-q", "-b", "main2"], cwd=str(target))

    result = repo_manager.create_local_repo(str(target), branch="build-abc123")

    assert result["branch"] == "build-abc123"


def test_create_local_repo_defaults_to_whatever_branch_git_init_produces(tmp_path):
    target = tmp_path / "proj"

    result = repo_manager.create_local_repo(str(target))

    assert result["branch"]

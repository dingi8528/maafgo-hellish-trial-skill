#!/usr/bin/env python3
"""Read local versions and optionally compare public upstream metadata; never update."""
import argparse
import importlib.metadata
import json
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


SOURCE_REPOSITORY = "dingi8528/MaaFgo_NEW"
RELEASE_REPOSITORY = "xlxyvergil/MaaFgo"


def git_value(project, *arguments):
    if not shutil.which("git"):
        return None
    try:
        result = subprocess.run(
            ["git", "-c", f"safe.directory={project.as_posix()}", "-C", str(project), *arguments],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def inspect_git(project):
    top = git_value(project, "rev-parse", "--show-toplevel")
    if top is None or Path(top).resolve() != project.resolve():
        return {"status": "not_a_project_git_checkout", "head": None, "dirty": None}
    porcelain = git_value(project, "status", "--porcelain", "--untracked-files=normal")
    return {
        "status": "checked", "head": git_value(project, "rev-parse", "HEAD"),
        "branch": git_value(project, "branch", "--show-current"),
        "dirty": bool(porcelain) if porcelain is not None else None,
        "untracked_included": True,
    }


def inspect_project(project):
    project = Path(project).expanduser().resolve()
    if not project.is_dir():
        return {"status": "not_found", "path": str(project)}
    interface = next((project / item for item in ("assets/interface.json", "interface.json")
                      if (project / item).is_file()), None)
    declared_version, interface_status = None, "not_found"
    if interface:
        try:
            declared_version = json.loads(interface.read_text(encoding="utf-8-sig")).get("version")
            interface_status = "checked"
        except (OSError, ValueError, AttributeError):
            interface_status = "unreadable"
    required = (
        "agent/battle/runtime/hellish_trial.py", "agent/battle/strategies/hellish_trial.py",
        "agent/chaldea/__init__.py",
    )
    return {
        "status": "checked", "path": str(project),
        "interface": {"status": interface_status, "path": str(interface) if interface else None,
                      "declared_version": declared_version,
                      "note": "开发目录的界面版本不能证明源码是否最新；打包版版本也不能证明文件未被修改。"},
        "required_files": {item: (project / item).is_file() for item in required},
        "git": inspect_git(project),
    }


def fetch_json(url, *, timeout=5):
    try:
        request = Request(url, headers={"User-Agent": "MaaFGO-Trial-Skill/1.0", "Accept": "application/json"})
        with urlopen(request, timeout=timeout) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            return {"status": "unknown", "reason": "metadata_too_large"}
        return {"status": "checked", "data": json.loads(raw)}
    except HTTPError as exc:
        return {"status": "unknown", "reason": f"http_{exc.code}"}
    except (URLError, OSError, ValueError) as exc:
        return {"status": "unknown", "reason": type(exc).__name__}


def upstream_versions(source_repo=SOURCE_REPOSITORY, release_repo=RELEASE_REPOSITORY):
    for repository in (source_repo, release_repo):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("GitHub仓库须填写 owner/name，不要填写完整网址或访问令牌。")
    base = f"https://api.github.com/repos/{source_repo}"
    repo = fetch_json(base)
    default_branch = repo.get("data", {}).get("default_branch") if repo["status"] == "checked" else None
    commit = fetch_json(base + "/commits/" + quote(default_branch, safe="")) if default_branch else {
        "status": "unknown", "reason": "default_branch_unavailable"}
    release = fetch_json(f"https://api.github.com/repos/{release_repo}/releases/latest")
    release_data = release.get("data", {})
    release_tag = release_data.get("tag_name") if not release_data.get("draft") and not release_data.get("prerelease") else None
    result = {
        "source_repository": source_repo,
        "release_repository": release_repo,
        "branch": {"status": "checked" if default_branch else "unknown", "name": default_branch,
                   "reason": repo.get("reason")},
        "commit": {"status": commit["status"], "sha": commit.get("data", {}).get("sha"),
                   "reason": commit.get("reason")},
        "stable_release": {"status": "checked" if release_tag else "unknown", "tag": release_tag,
                           "url": release_data.get("html_url") if release_tag else None,
                           "reason": release.get("reason", "no_stable_release" if not release_tag else None)},
        "packages": {},
    }
    for name in ("maa-mcp", "maafw"):
        package = fetch_json(f"https://pypi.org/pypi/{name}/json")
        version = package.get("data", {}).get("info", {}).get("version")
        result["packages"][name] = {"status": "checked" if version else "unknown", "version": version,
                                    "reason": package.get("reason")}
    return result


def compare(local, upstream):
    result = {"packages": {}}
    for name, installed in local["packages"].items():
        newest = upstream["packages"][name].get("version")
        result["packages"][name] = "not_installed" if installed is None else (
            "unknown" if newest is None else "matches" if installed == newest else "different")
    project = local.get("project")
    if project and project.get("status") == "checked":
        head = project["git"].get("head")
        remote = upstream["commit"].get("sha")
        declared = project["interface"].get("declared_version")
        release = upstream["stable_release"].get("tag")
        result["project_head"] = "unknown" if not head or not remote else (
            "matches" if head == remote else "different_inspect_history")
        result["declared_release_tag"] = "unknown" if not declared or not release else (
            "matches" if declared == release else "different")
        result["note"] = "不同不等于过旧：源码可能领先、有私有修改或处于其他分支。ZIP只能核对声明版本，不能确认源码HEAD；先检查差异再决定更新。"
    else:
        result["project_head"] = "not_checked"
    return result


def environment(project=None, *, online=False, source_repo=SOURCE_REPOSITORY, release_repo=RELEASE_REPOSITORY):
    packages = {}
    for name in ("maa-mcp", "maafw"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    result = {
        "python": {"version": platform.python_version(), "executable": sys.executable},
        "system": {"name": platform.system(), "machine": platform.machine()},
        "packages": packages, "git_available": bool(shutil.which("git")),
        "project": inspect_project(project) if project else {"status": "not_checked"},
        "vision": "requires_actual_screenshot_test_by_agent",
        "device": "not_checked_no_device_actions",
    }
    if online:
        result["upstream"] = upstream_versions(source_repo, release_repo)
        result["comparison"] = compare(result, result["upstream"])
    else:
        result["upstream"] = {"status": "not_checked", "note": "加 --online 才会读取 GitHub/PyPI；离线结果不能称为最新。"}
    return result


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, help="MaaFGO工程或解压后的打包目录")
    parser.add_argument("--online", action="store_true", help="只读查询GitHub/PyPI最新公开元数据")
    parser.add_argument("--json", action="store_true", help="兼容显式JSON选项；默认即为JSON")
    parser.add_argument("--source-repo", default=SOURCE_REPOSITORY, help="开发源码仓库，格式owner/name")
    parser.add_argument("--release-repo", default=RELEASE_REPOSITORY, help="发行包仓库，格式owner/name")
    args = parser.parse_args()
    try:
        result = environment(args.project, online=args.online, source_repo=args.source_repo, release_repo=args.release_repo)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

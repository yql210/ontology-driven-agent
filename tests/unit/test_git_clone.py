"""GitCloneService 单元测试。

覆盖：
- URL scheme 白名单（拒绝 file://、http:// 等）
- URL hostname 白名单（拒绝非 github/gitee/gitlab）
- clone 命令参数正确（``--depth 1 --single-branch --branch <branch>``）
- 临时目录创建在 ``config.git_work_dir`` 下
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ontoagent.config import OntoAgentConfig
from ontoagent.service.git_clone import GitCloneError, GitCloneService


@pytest.fixture
def config(tmp_path: Path) -> OntoAgentConfig:
    """测试用配置：白名单含 github/gitee/gitlab，工作目录指向 tmp_path。"""
    return OntoAgentConfig(
        git_allowed_hosts=["github.com", "gitee.com", "gitlab.com"],
        git_clone_timeout=10,
        git_work_dir=str(tmp_path / "repos"),
    )


@pytest.fixture
def service(config: OntoAgentConfig) -> GitCloneService:
    """GitCloneService 实例。"""
    return GitCloneService(config)


class TestValidateUrl:
    def test_validate_url_rejects_unsupported_scheme(self, service: GitCloneService) -> None:
        """scheme 不在白名单（file://、http://、ftp://）必须拒绝。"""
        with pytest.raises(GitCloneError, match="scheme"):
            service._validate_url("file:///etc/passwd")
        with pytest.raises(GitCloneError, match="scheme"):
            service._validate_url("http://github.com/foo/bar")
        with pytest.raises(GitCloneError, match="scheme"):
            service._validate_url("ftp://github.com/foo/bar")

    def test_validate_url_rejects_host_not_in_whitelist(self, service: GitCloneService) -> None:
        """hostname 不在白名单（evil.com）必须拒绝，防 SSRF。"""
        with pytest.raises(GitCloneError, match="whitelist"):
            service._validate_url("https://evil.com/foo/bar")
        with pytest.raises(GitCloneError, match="whitelist"):
            service._validate_url("https://internal.svc.local/foo.git")

    def test_validate_url_accepts_github(self, service: GitCloneService) -> None:
        """github.com 合法 https URL 应通过校验。"""
        service._validate_url("https://github.com/anthropics/claude.git")
        service._validate_url("https://github.com/foo/bar")

    def test_validate_url_accepts_gitee(self, service: GitCloneService) -> None:
        """gitee.com 合法 URL 应通过校验。"""
        service._validate_url("https://gitee.com/oschina/git.git")
        service._validate_url("git@gitee.com:oschina/git.git")

    def test_validate_url_rejects_empty(self, service: GitCloneService) -> None:
        """空 URL 必须拒绝。"""
        with pytest.raises(GitCloneError, match="Empty"):
            service._validate_url("")
        with pytest.raises(GitCloneError, match="Empty"):
            service._validate_url("   ")


class TestCloneCommand:
    """验证 clone 调用 git 时传入的命令行参数。"""

    @pytest.mark.asyncio
    async def test_clone_calls_git_with_correct_args(self, service: GitCloneService) -> None:
        """clone() 必须以参数列表形式调用 git（不通过 shell），且包含分支与 URL。"""
        fake_result = MagicMock(returncode=0, stdout=b"", stderr=b"")
        with patch("ontoagent.service.git_clone.subprocess.run", return_value=fake_result) as mock_run:
            target = await service.clone("https://github.com/foo/bar.git", branch="main")

        assert mock_run.called
        args, kwargs = mock_run.call_args
        cmd = args[0] if args else kwargs.get("args")
        assert cmd[0] == "git"
        assert "clone" in cmd
        assert "https://github.com/foo/bar.git" in cmd
        assert str(target) in cmd
        # 必须 shell=False（不传 shell=True 或显式 False）
        assert kwargs.get("shell", False) is False or "shell" not in kwargs

    @pytest.mark.asyncio
    async def test_clone_uses_depth_1_and_single_branch(self, service: GitCloneService) -> None:
        """clone 命令必须包含 ``--depth 1`` 和 ``--single-branch``（防克隆历史过重）。"""
        fake_result = MagicMock(returncode=0, stdout=b"", stderr=b"")
        with patch("ontoagent.service.git_clone.subprocess.run", return_value=fake_result) as mock_run:
            await service.clone("https://github.com/foo/bar.git", branch="dev")

        cmd = mock_run.call_args.args[0]
        assert "--depth" in cmd
        depth_idx = cmd.index("--depth")
        assert cmd[depth_idx + 1] == "1"
        assert "--single-branch" in cmd
        # branch 参数传递
        branch_idx = cmd.index("--branch")
        assert cmd[branch_idx + 1] == "dev"

    @pytest.mark.asyncio
    async def test_clone_creates_temp_dir_under_work_dir(
        self, service: GitCloneService, config: OntoAgentConfig
    ) -> None:
        """clone() 必须把仓库放到 ``config.git_work_dir`` 下，且目录名以 ``repo-`` 前缀。"""
        fake_result = MagicMock(returncode=0, stdout=b"", stderr=b"")
        with patch("ontoagent.service.git_clone.subprocess.run", return_value=fake_result):
            target = await service.clone("https://github.com/foo/bar.git")
        assert str(target).startswith(config.git_work_dir)
        assert target.name.startswith("repo-")
        # uuid 后缀长度 32（hex）+ "repo-" 前缀
        assert len(target.name) == len("repo-") + 32

    @pytest.mark.asyncio
    async def test_clone_raises_on_git_failure(self, service: GitCloneService) -> None:
        """git 子进程非零退出必须抛 GitCloneError。"""
        err = subprocess.CalledProcessError(returncode=128, cmd=["git", "clone"], stderr="auth failed")
        with (
            patch("ontoagent.service.git_clone.subprocess.run", side_effect=err),
            pytest.raises(GitCloneError, match="auth failed"),
        ):
            await service.clone("https://github.com/foo/bar.git")

    @pytest.mark.asyncio
    async def test_clone_raises_on_timeout(self, service: GitCloneService) -> None:
        """git 超时必须抛 GitCloneError（含超时秒数）。"""
        timeout = subprocess.TimeoutExpired(cmd=["git", "clone"], timeout=10)
        with (
            patch("ontoagent.service.git_clone.subprocess.run", side_effect=timeout),
            pytest.raises(GitCloneError, match="timed out"),
        ):
            await service.clone("https://github.com/foo/bar.git")

    @pytest.mark.asyncio
    async def test_clone_rejects_bad_url_before_subprocess(self, service: GitCloneService) -> None:
        """非法 URL 在调用 subprocess 前就被拒绝。"""
        with (
            patch("ontoagent.service.git_clone.subprocess.run") as mock_run,
            pytest.raises(GitCloneError, match="whitelist"),
        ):
            await service.clone("https://evil.com/foo.git")
        mock_run.assert_not_called()


class TestCloneBranchFallback:
    """P1-3：请求分支不存在时，探测远端默认分支并回退重试。"""

    _BRANCH_NOT_FOUND = "fatal: Remote branch main not found in upstream origin"

    @pytest.mark.asyncio
    async def test_fallback_to_default_branch_on_branch_not_found(self, service: GitCloneService) -> None:
        """main 不存在时探测远端默认分支（master）并重试成功。"""
        branch_err = subprocess.CalledProcessError(returncode=128, cmd=["git", "clone"], stderr=self._BRANCH_NOT_FOUND)
        ls_remote_ok = MagicMock(stdout="ref: refs/heads/master HEAD\n<sha>\tHEAD")
        clone_ok = MagicMock(returncode=0, stdout=b"", stderr=b"")

        with patch(
            "ontoagent.service.git_clone.subprocess.run",
            side_effect=[branch_err, ls_remote_ok, clone_ok],
        ) as mock_run:
            target = await service.clone("https://github.com/foo/bar.git", branch="main")

        assert isinstance(target, Path)
        assert mock_run.call_count == 3
        # 第二次 clone 使用探测到的 master 分支
        retry_cmd = mock_run.call_args_list[2].args[0]
        assert "--branch" in retry_cmd
        assert retry_cmd[retry_cmd.index("--branch") + 1] == "master"

    @pytest.mark.asyncio
    async def test_fallback_failure_raises_with_merged_error(self, service: GitCloneService) -> None:
        """两次 clone 都失败：抛 GitCloneError，错误信息合并原始与重试 stderr。"""
        branch_err = subprocess.CalledProcessError(returncode=128, cmd=["git", "clone"], stderr=self._BRANCH_NOT_FOUND)
        ls_remote_ok = MagicMock(stdout="ref: refs/heads/master HEAD\n<sha>\tHEAD")
        retry_err = subprocess.CalledProcessError(
            returncode=128,
            cmd=["git", "clone"],
            stderr="fatal: unable to access 'https://github.com/foo/bar.git'",
        )

        with (
            patch(
                "ontoagent.service.git_clone.subprocess.run",
                side_effect=[branch_err, ls_remote_ok, retry_err],
            ),
            pytest.raises(GitCloneError, match="also failed"),
        ):
            await service.clone("https://github.com/foo/bar.git", branch="main")

    @pytest.mark.asyncio
    async def test_no_ref_line_raises_original_without_retry(self, service: GitCloneService) -> None:
        """ls-remote 输出无 ref 行：抛原错误，不重试。"""
        branch_err = subprocess.CalledProcessError(returncode=128, cmd=["git", "clone"], stderr=self._BRANCH_NOT_FOUND)
        ls_remote_no_ref = MagicMock(stdout="<sha>\tHEAD\n")

        with (
            patch(
                "ontoagent.service.git_clone.subprocess.run",
                side_effect=[branch_err, ls_remote_no_ref],
            ) as mock_run,
            pytest.raises(GitCloneError, match="Remote branch main not found"),
        ):
            await service.clone("https://github.com/foo/bar.git", branch="main")
        assert mock_run.call_count == 2

    @pytest.mark.asyncio
    async def test_detected_branch_same_as_requested_raises_original(self, service: GitCloneService) -> None:
        """探测到的分支与请求分支相同：抛原错误，不重试。"""
        branch_err = subprocess.CalledProcessError(returncode=128, cmd=["git", "clone"], stderr=self._BRANCH_NOT_FOUND)
        ls_remote_same = MagicMock(stdout="ref: refs/heads/main HEAD\n<sha>\tHEAD")

        with (
            patch(
                "ontoagent.service.git_clone.subprocess.run",
                side_effect=[branch_err, ls_remote_same],
            ) as mock_run,
            pytest.raises(GitCloneError, match="Remote branch main not found"),
        ):
            await service.clone("https://github.com/foo/bar.git", branch="main")
        assert mock_run.call_count == 2

    @pytest.mark.asyncio
    async def test_ls_remote_failure_raises_original(self, service: GitCloneService) -> None:
        """ls-remote 自身失败：抛原错误，不重试。"""
        branch_err = subprocess.CalledProcessError(returncode=128, cmd=["git", "clone"], stderr=self._BRANCH_NOT_FOUND)
        ls_remote_err = subprocess.CalledProcessError(
            returncode=128,
            cmd=["git", "ls-remote"],
            stderr="could not resolve host",
        )

        with (
            patch(
                "ontoagent.service.git_clone.subprocess.run",
                side_effect=[branch_err, ls_remote_err],
            ) as mock_run,
            pytest.raises(GitCloneError, match="Remote branch main not found"),
        ):
            await service.clone("https://github.com/foo/bar.git", branch="main")
        assert mock_run.call_count == 2
